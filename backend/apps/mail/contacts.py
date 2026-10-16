"""Адресна книга й підказки одержувачів (як у Gmail).

Підказки для поля «Кому» об'єднують три джерела:
* власні контакти користувача;
* тих, кому він писав (RecipientStat: скільки разів і коли востаннє);
* інших користувачів BlackCloud, які дозволили себе знаходити (Налаштування → Приватність).

Ранжування: збіг з початком адреси чи слова в імені важить більше за збіг усередині;
далі — як часто й нещодавно писали, зірочка в контактах.
"""

from datetime import timedelta
from email.utils import getaddresses

from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import Contact, Mailbox, RecipientStat

MAX_RECIPIENT_STATS = 3000
SOURCE_BONUS = {"contact": 25, "recent": 20, "user": 15}


def _valid_address(address: str) -> bool:
    try:
        validate_email(address)
    except DjangoValidationError:
        return False
    return True


def record_recipients(user, header_values: list) -> None:
    """Після надсилання: +1 кожному одержувачу (To/Cc/Bcc) — для підказок."""
    now = timezone.now()
    seen = set()
    for name, address in getaddresses([v for v in header_values if v]):
        address = (address or "").strip().lower()[:320]
        if not address or address in seen or not _valid_address(address):
            continue
        seen.add(address)
        name = (name or "").strip()[:200]
        updated = RecipientStat.objects.filter(owner=user, address=address).update(count=F("count") + 1, last_used=now)
        if updated:
            if name:
                RecipientStat.objects.filter(owner=user, address=address).update(name=name)
            continue
        try:
            with transaction.atomic():
                RecipientStat.objects.create(owner=user, address=address, name=name, count=1, last_used=now)
        except IntegrityError:
            RecipientStat.objects.filter(owner=user, address=address).update(count=F("count") + 1, last_used=now)
    total = RecipientStat.objects.filter(owner=user).count()
    if total > MAX_RECIPIENT_STATS:
        stale = RecipientStat.objects.filter(owner=user).order_by("count", "last_used").values_list("pk", flat=True)
        RecipientStat.objects.filter(pk__in=list(stale[: total - MAX_RECIPIENT_STATS])).delete()


def _match(q: str, name: str, address: str) -> int:
    name = (name or "").lower()
    address = address.lower()
    local, _at, domain = address.partition("@")
    if local.startswith(q) or address.startswith(q):
        return 60
    if any(word.startswith(q) for word in name.replace(".", " ").replace("-", " ").split()):
        return 50
    if name.startswith(q):
        return 50
    if domain.startswith(q):
        return 20
    if q in address or q in name:
        return 10
    return 0


def suggest(user, q: str, limit: int = 8) -> list[dict]:
    q = (q or "").strip().lower()[:100]
    if not q:
        return []
    found: dict[str, dict] = {}

    def add(address: str, name: str, source: str, bonus: int, extra: dict | None = None):
        address = (address or "").strip().lower()
        if "@" not in address:
            return
        m = _match(q, name, address)
        if not m:
            return
        item = found.get(address)
        if item is None:
            item = found[address] = {"address": address, "name": "", "sources": [], "score": 0, "match": 0}
        item["match"] = max(item["match"], m)
        if source not in item["sources"]:
            item["sources"].append(source)
            item["score"] += bonus
        # Ім'я: з контакту важливіше, ніж із листування чи профілю
        if name and (not item["name"] or source == "contact"):
            item["name"] = name
        if extra:
            item.update(extra)

    for c in Contact.objects.filter(owner=user, search_text__contains=q)[:200]:
        for e in c.emails or []:
            add(e.get("address", ""), c.display_name, "contact", SOURCE_BONUS["contact"] + (10 if c.starred else 0), {"contact_id": str(c.pk)})

    month_ago = timezone.now() - timedelta(days=30)
    recent = RecipientStat.objects.filter(owner=user).filter(Q(address__icontains=q) | Q(name__icontains=q)).order_by("-count")[:50]
    for r in recent:
        add(r.address, r.name, "recent", SOURCE_BONUS["recent"] + min(r.count, 30) + (10 if r.last_used >= month_ago else 0))

    User = get_user_model()
    users = (
        User.objects.filter(is_active=True, preferences__discoverable=True)
        .exclude(pk=user.pk)
        .filter(Q(username__istartswith=q) | Q(display_name__icontains=q) | Q(mailboxes__local_part__istartswith=q))
        .distinct()[:20]
    )
    boxes = Mailbox.objects.filter(user__in=users, active=True).select_related("domain", "user")
    for mb in boxes:
        u = mb.user
        add(mb.address, mb.display_name or u.display_name or u.username, "user", SOURCE_BONUS["user"], {"avatar_url": u.avatar_url})

    items = sorted(found.values(), key=lambda i: (-(i["match"] + i["score"]), i["name"] or i["address"]))[:limit]
    for i in items:
        i.pop("score", None)
        i.pop("match", None)
    return items
