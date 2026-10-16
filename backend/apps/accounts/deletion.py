"""Видалення акаунта адміністратором — у два кроки.

1. schedule(): акаунт одразу блокується (сесії, WebSocket, WebDAV, пошта — усе вимикається),
   а остаточне видалення призначається через ACCOUNT_DELETION_DELAY_DAYS. До того
   адміністратор може скасувати (акаунт лишається заблокованим, розблокування — окремо).
2. purge() (Celery beat, щогодини): стирає листи через IMAP, файли й версії зі сховища,
   потім запис користувача — каскадом календарі, чати-учасники, push, паролі WebDAV тощо.
   Записи аудиту лишаються (user=NULL). Якщо пошта недоступна — нічого не видаляється,
   спроба повториться за годину (щоб на диску не лишилося листів без власника).

Ім'я та поштові адреси ще ACCOUNT_NAME_HOLD_DAYS після видалення не видаються іншим.
Резервні копії зберігають дані до кінця свого терміну (до 12 місяців, див. docs/BACKUP.md).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.audit import audit

from .models import AccountDeletion, User

logger = logging.getLogger("blackcloud")
LOCK_KEY = "account-purge:lock"
BATCH = 20


def _hold_cutoff():
    return timezone.now() - timedelta(days=settings.ACCOUNT_NAME_HOLD_DAYS)


def _active_holds():
    return AccountDeletion.objects.filter(Q(purged_at__isnull=True) | Q(purged_at__gte=_hold_cutoff()))


def name_on_hold(username: str) -> bool:
    return _active_holds().filter(username=username.lower()).exists()


def address_on_hold(address: str) -> bool:
    address = address.lower()
    return any(address in (d.addresses or []) for d in _active_holds().only("addresses"))


def _user_addresses(user: User) -> list[str]:
    from apps.mail.models import Alias

    addresses = {mb.address.lower() for mb in user.mailboxes.select_related("domain")}
    addresses |= {a.lower() for a in Alias.objects.filter(owner=user).values_list("source", flat=True)}
    return sorted(addresses)


def pending_for(user: User) -> AccountDeletion | None:
    try:
        d = user.deletion
    except AccountDeletion.DoesNotExist:
        return None
    return d if d.purged_at is None else None


def schedule(user: User, by: User, reason: str = "") -> AccountDeletion:
    from .views import _mark_blocked

    with transaction.atomic():
        d = AccountDeletion.objects.create(
            user=user,
            username=user.username,
            addresses=_user_addresses(user),
            reason=reason[:500],
            requested_by=by,
            purge_after=timezone.now() + timedelta(days=settings.ACCOUNT_DELETION_DELAY_DAYS),
        )
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])
        _mark_blocked(user, by, reason)
    return d


def cancel(user: User) -> bool:
    d = pending_for(user)
    if d is None or d.started_at is not None:
        return False
    d.delete()
    return True


# ─────────────────────────── Остаточне видалення ───────────────────────────


class PurgeFailed(Exception):
    pass


def _purge_mail(user: User) -> int:
    from apps.mail import imap
    from apps.mail.models import Alias, Mailbox
    from apps.mail.views_admin import wipe_mailbox

    removed = 0
    for mb in list(user.mailboxes.select_related("domain")):
        address = mb.address
        if settings.DOVECOT_MASTER_PASSWORD:
            # Dovecot бачить скриньку лише активною (і лише поки started_at не NULL — див. mail/sql.py);
            # клієнтський пароль прибираємо, щоб за цей час не увійшов ніхто, крім служби.
            Mailbox.objects.filter(pk=mb.pk).update(active=True, client_password_hash="")
            try:
                removed += wipe_mailbox(address)
            except imap.MailUnavailable as exc:
                Mailbox.objects.filter(pk=mb.pk).update(active=mb.active, client_password_hash=mb.client_password_hash)
                raise PurgeFailed("mail_unavailable") from exc
        else:
            logger.warning("account purge: webmail not configured, mailbox %s removed without IMAP wipe", mb.pk)
        with transaction.atomic():
            Alias.objects.filter(Q(destination=address) | Q(source=address)).delete()
            mb.scheduled_messages.all().delete()
            mb.delete()
        cache.delete(f"push:mail:{mb.pk}")
    Alias.objects.filter(owner=user).delete()
    return removed


def _purge_files(user: User) -> int:
    from apps.storage import services
    from apps.storage.models import File, Folder

    count = 0
    # Кореневі теки користувача (і його теки всередині чужих) — разом з усім вмістом
    while True:
        roots = list(Folder.objects.filter(owner=user).filter(Q(parent__isnull=True) | ~Q(parent__owner=user))[:100])
        if not roots:
            break
        for folder in roots:
            count += services.purge_folder(folder)
    for f in File.objects.filter(owner=user).select_related("owner").iterator():
        services.purge_file(f)
        count += 1
    return count


def purge(d: AccountDeletion) -> bool:
    """True — акаунт видалено; False — не вдалося (повтор наступного запуску)."""
    from .views import _revoke_all_sessions

    user = d.user
    if user is None:
        AccountDeletion.objects.filter(pk=d.pk).update(purged_at=d.purged_at or timezone.now(), started_at=None)
        return True
    d.addresses = sorted(set(d.addresses or []) | set(_user_addresses(user)))
    d.started_at = timezone.now()
    d.attempts += 1
    d.save(update_fields=["addresses", "started_at", "attempts"])
    User.objects.filter(pk=user.pk).update(is_active=False)
    _revoke_all_sessions(user)
    try:
        messages = _purge_mail(user)
        files = _purge_files(user)
    except PurgeFailed as exc:
        AccountDeletion.objects.filter(pk=d.pk).update(started_at=None, last_error=str(exc))
        logger.warning("account purge postponed for %s: %s", d.username, exc)
        return False
    except Exception:
        AccountDeletion.objects.filter(pk=d.pk).update(started_at=None, last_error="files_failed")
        logger.exception("account purge failed for %s", d.username)
        return False
    username = user.username
    user.delete()
    AccountDeletion.objects.filter(pk=d.pk).update(user=None, started_at=None, purged_at=timezone.now(), last_error="")
    audit(None, "account.purged", target=username, messages=messages, files=files, requested_by=getattr(d.requested_by, "username", ""))
    return True


def purge_due() -> int:
    if not cache.add(LOCK_KEY, 1, 3600):
        return 0
    done = 0
    try:
        due = AccountDeletion.objects.filter(purged_at__isnull=True, user__isnull=False, purge_after__lte=timezone.now())
        for d in due.select_related("user", "requested_by")[:BATCH]:
            if purge(d):
                done += 1
    finally:
        cache.delete(LOCK_KEY)
    return done
