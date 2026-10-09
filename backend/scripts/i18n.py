#!/usr/bin/env python3
"""Переклади бекенду без GNU gettext (msgfmt/xgettext не потрібні ні в образі, ні в CI).

Рядки в коді пишуться українською й позначаються gettext / gettext_lazy / gettext_noop (зазвичай як `_`).
Англійський переклад зберігається в locale/en/LC_MESSAGES/django.po, скомпільований — у django.mo.

    python scripts/i18n.py extract   # додати нові рядки в .po (порожній msgstr), прибрати зниклі
    python scripts/i18n.py compile   # зібрати .mo з .po
    python scripts/i18n.py check     # CI: усі рядки перекладено, .mo актуальний, плейсхолдери збігаються
"""

from __future__ import annotations

import ast
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "apps", ROOT / "config"]
PO = ROOT / "locale" / "en" / "LC_MESSAGES" / "django.po"
MO = PO.with_suffix(".mo")
MARKERS = {"_", "gettext", "gettext_lazy", "gettext_noop", "_lazy", "_noop", "pgettext"}
HEADER = (
    "Project-Id-Version: blackcloud\n"
    "Language: en\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
    "Plural-Forms: nplurals=2; plural=(n != 1);\n"
)
PLACEHOLDER_RE = re.compile(r"%\((\w+)\)[sdfr]|\{(\w*)\}")


def _skip(path: Path) -> bool:
    parts = set(path.parts)
    return "migrations" in parts or "tests" in parts or "__pycache__" in parts


def extract() -> dict[str, list[str]]:
    """msgid → список місць (файл:рядок)."""
    found: dict[str, list[str]] = {}
    for base in SOURCES:
        for path in sorted(base.rglob("*.py")):
            if _skip(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                if name not in MARKERS:
                    continue
                arg = node.args[1] if name == "pgettext" and len(node.args) > 1 else node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                    found.setdefault(arg.value, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


# ─── .po ────────────────────────────────────────────────────────


def _unquote(s: str) -> str:
    s = s.strip()
    assert s.startswith('"') and s.endswith('"'), s
    return ast.literal_eval(s)


def _quote(s: str) -> str:
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{out}"'


def read_po(path: Path = PO) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    msgid = msgstr = None
    field = None
    for raw in path.read_text(encoding="utf-8").splitlines() + [""]:
        line = raw.strip()
        if not line or line.startswith("#"):
            if msgid is not None and msgstr is not None:
                entries[msgid] = msgstr
            if not line:
                msgid = msgstr = field = None
            continue
        if line.startswith("msgid "):
            if msgid is not None and msgstr is not None:
                entries[msgid] = msgstr
            msgid, msgstr, field = _unquote(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            msgstr, field = _unquote(line[7:]), "str"
        elif line.startswith('"'):
            if field == "id":
                msgid += _unquote(line)
            elif field == "str":
                msgstr += _unquote(line)
    entries.pop("", None)
    return entries


def write_po(entries: dict[str, str], refs: dict[str, list[str]], path: Path = PO) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Англійський переклад повідомлень бекенду BlackCloud.",
        "# Оновлення: python scripts/i18n.py extract → перекласти порожні msgstr → python scripts/i18n.py compile",
        'msgid ""',
        'msgstr ""',
    ]
    lines += [_quote(h + "\n") for h in HEADER.rstrip("\n").split("\n")]
    for msgid in sorted(entries, key=lambda m: (refs.get(m, ["~"])[0], m)):
        lines.append("")
        for ref in refs.get(msgid, [])[:3]:
            lines.append(f"#: {ref}")
        if PLACEHOLDER_RE.search(msgid) and "%(" in msgid:
            lines.append("#, python-format")
        lines.append(f"msgid {_quote(msgid)}")
        lines.append(f"msgstr {_quote(entries[msgid])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── .mo ────────────────────────────────────────────────────────


def build_mo(entries: dict[str, str]) -> bytes:
    """Формат GNU .mo (як Tools/i18n/msgfmt.py з CPython); порожні переклади не включаються."""
    catalog = {"": HEADER}
    catalog.update({k: v for k, v in entries.items() if v})
    keys = sorted(catalog)
    ids = b""
    strs = b""
    offsets = []
    for k in keys:
        kid, kstr = k.encode("utf-8"), catalog[k].encode("utf-8")
        offsets.append((len(ids), len(kid), len(strs), len(kstr)))
        ids += kid + b"\0"
        strs += kstr + b"\0"
    n = len(keys)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets: list[int] = []
    voffsets: list[int] = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    header = struct.pack("Iiiiiii", 0x950412DE, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    return header + struct.pack(f"{len(koffsets)}i", *koffsets) + struct.pack(f"{len(voffsets)}i", *voffsets) + ids + strs


def _placeholders(s: str) -> set[str]:
    return {a or b for a, b in PLACEHOLDER_RE.findall(s)}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    refs = extract()
    po = read_po()
    if cmd == "extract":
        merged = {m: po.get(m, "") for m in refs}
        write_po(merged, refs)
        missing = sum(1 for v in merged.values() if not v)
        print(f"{len(merged)} рядків, без перекладу: {missing}, видалено застарілих: {len(set(po) - set(refs))}")
        return 0
    if cmd == "compile":
        MO.write_bytes(build_mo(po))
        print(f"{MO.relative_to(ROOT)}: {sum(1 for v in po.values() if v)} перекладів")
        return 0
    if cmd == "check":
        errors = []
        for msgid, where in refs.items():
            if not po.get(msgid):
                errors.append(f"немає перекладу: {msgid!r} ({where[0]})")
            elif _placeholders(msgid) != _placeholders(po[msgid]):
                errors.append(f"плейсхолдери не збігаються: {msgid!r} → {po[msgid]!r}")
        stale = set(po) - set(refs)
        if stale:
            errors.append(f"застарілі рядки в .po ({len(stale)}): запустіть extract")
        if not MO.exists() or MO.read_bytes() != build_mo(po):
            errors.append("django.mo не відповідає django.po: запустіть compile")
        for e in errors:
            print(e)
        print("OK" if not errors else f"Помилок: {len(errors)}")
        return 1 if errors else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
