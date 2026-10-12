"""Легкі блокування WebDAV (клас 2) у кеші Django (Redis).

Finder і Windows вимагають LOCK/UNLOCK для запису. Блокування живуть лише в кеші
(з тайм-аутом, максимум MAX_TIMEOUT), всі блокування користувача — в одному ключі.
Шлях — кортеж сегментів від кореня сховища користувача, тут — рядок "a/b/c".
"""

import re
import time
import uuid

from django.core.cache import cache

from .xmlutil import text

DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 3600
MIN_TIMEOUT = 30
MAX_LOCKS_PER_USER = 500
TOKEN_RE = re.compile(r"<(opaquelocktoken:[0-9a-fA-F-]{36})>")


def _key(user_id) -> str:
    return f"dav-locks:{user_id}"


def _mutex(user_id):
    """Короткий м'ютекс навколо читання-зміни-запису словника блокувань."""
    key = f"dav-locks-mx:{user_id}"

    class _M:
        def __enter__(self):
            for _ in range(50):
                if cache.add(key, 1, 5):
                    self.acquired = True
                    return self
                time.sleep(0.02)
            self.acquired = False  # не блокуємо роботу через завислий м'ютекс
            return self

        def __exit__(self, *exc):
            if self.acquired:
                cache.delete(key)

    return _M()


def _load(user_id) -> dict:
    now = time.time()
    locks = cache.get(_key(user_id)) or {}
    return {t: lk for t, lk in locks.items() if lk["expires"] > now}


def _save(user_id, locks: dict) -> None:
    if locks:
        cache.set(_key(user_id), locks, MAX_TIMEOUT + 60)
    else:
        cache.delete(_key(user_id))


def parse_timeout(header: str | None) -> int:
    for part in (header or "").split(","):
        part = part.strip().lower()
        if part == "infinite":
            return MAX_TIMEOUT
        if part.startswith("second-"):
            try:
                return max(MIN_TIMEOUT, min(MAX_TIMEOUT, int(part[7:])))
            except ValueError:
                continue
    return DEFAULT_TIMEOUT


def submitted_tokens(request) -> set[str]:
    tokens = set(TOKEN_RE.findall(request.META.get("HTTP_IF", "")))
    lock_token = request.META.get("HTTP_LOCK_TOKEN", "")
    tokens.update(TOKEN_RE.findall(lock_token))
    return {t.lower() for t in tokens}


def _covers(lock: dict, path: str) -> bool:
    lp = lock["path"]
    if lp == path:
        return True
    if lock["depth"] != "infinity":
        return False
    return lp == "" or path.startswith(lp + "/")


def _under(lock: dict, path: str) -> bool:
    return path == "" or lock["path"].startswith(path + "/")


def locks_for(user_id, path: str) -> list[dict]:
    """Активні блокування, що діють на ресурс (для lockdiscovery)."""
    return [lk for lk in _load(user_id).values() if _covers(lk, path)]


def conflicting(user_id, path: str, *, subtree: bool = False) -> list[dict]:
    """Блокування, що забороняють зміну path (і його вмісту, якщо subtree)."""
    return [lk for lk in _load(user_id).values() if _covers(lk, path) or (subtree and _under(lk, path))]


def check_write(request, user_id, path: str, *, subtree: bool = False, parent: str | None = None, resource=True) -> bool:
    """True — запис дозволено.

    Для самого ресурсу (resource) і окремо для батьківської колекції (parent — зміна її
    складу) потрібен токен хоча б одного з чинних блокувань, якщо вони є."""
    tokens = submitted_tokens(request)
    all_locks = list(_load(user_id).values())
    groups = []
    if resource:
        groups.append([lk for lk in all_locks if _covers(lk, path) or (subtree and _under(lk, path))])
    if parent is not None:
        groups.append([lk for lk in all_locks if lk["path"] == parent or _covers(lk, parent)])
    return all(not group or any(lk["token"] in tokens for lk in group) for group in groups)


def create(user_id, path: str, *, scope: str, depth: str, owner: str, timeout: int) -> dict | None:
    """Нове блокування; None — конфлікт з наявним."""
    with _mutex(user_id):
        locks = _load(user_id)
        for lk in locks.values():
            overlaps = _covers(lk, path) or (depth == "infinity" and _under(lk, path))
            if overlaps and (scope == "exclusive" or lk["scope"] == "exclusive"):
                return None
        if len(locks) >= MAX_LOCKS_PER_USER:
            return None
        lock = {
            "token": f"opaquelocktoken:{uuid.uuid4()}",
            "path": path,
            "scope": scope,
            "depth": depth,
            "owner": owner[:256],
            "timeout": timeout,
            "expires": time.time() + timeout,
        }
        locks[lock["token"]] = lock
        _save(user_id, locks)
        return lock


def refresh(user_id, path: str, tokens: set[str], timeout: int) -> dict | None:
    with _mutex(user_id):
        locks = _load(user_id)
        for token in tokens:
            lk = locks.get(token)
            if lk and _covers(lk, path):
                lk["timeout"] = timeout
                lk["expires"] = time.time() + timeout
                _save(user_id, locks)
                return lk
    return None


def release(user_id, path: str, token: str) -> bool:
    with _mutex(user_id):
        locks = _load(user_id)
        lk = locks.get(token.lower())
        if lk is None or not _covers(lk, path):
            return False
        del locks[lk["token"]]
        _save(user_id, locks)
        return True


def drop_subtree(user_id, path: str) -> None:
    """Після DELETE/MOVE ресурсу його блокування (і вкладених) зникають."""
    with _mutex(user_id):
        locks = _load(user_id)
        keep = {t: lk for t, lk in locks.items() if not (lk["path"] == path or _under(lk, path))}
        if len(keep) != len(locks):
            _save(user_id, keep)


def activelock_xml(lock: dict, root_href: str) -> str:
    remaining = max(0, int(lock["expires"] - time.time()))
    owner = f"<D:owner><D:href>{text(lock['owner'])}</D:href></D:owner>" if lock["owner"] else ""
    return (
        "<D:activelock>"
        f"<D:locktype><D:write/></D:locktype><D:lockscope><D:{lock['scope']}/></D:lockscope>"
        f"<D:depth>{'infinity' if lock['depth'] == 'infinity' else '0'}</D:depth>{owner}"
        f"<D:timeout>Second-{remaining}</D:timeout>"
        f"<D:locktoken><D:href>{text(lock['token'])}</D:href></D:locktoken>"
        f"<D:lockroot><D:href>{text(root_href)}</D:href></D:lockroot>"
        "</D:activelock>"
    )


SUPPORTED_LOCK = (
    "<D:lockentry><D:lockscope><D:exclusive/></D:lockscope><D:locktype><D:write/></D:locktype></D:lockentry>"
    "<D:lockentry><D:lockscope><D:shared/></D:lockscope><D:locktype><D:write/></D:locktype></D:lockentry>"
)
