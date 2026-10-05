"""TOTP (RFC 6238) з захистом від повторного використання коду та резервні коди."""

import base64
import hmac
import re
import io
import secrets
import time

import pyotp
import qrcode
import qrcode.image.svg
from django.db import transaction
from django.utils import timezone

from apps.core.crypto import decrypt_str, encrypt_str

from .models import BackupCode, TOTPDevice

ISSUER = "BlackCloud"
STEP = 30
VALID_WINDOW = 1  # ±30 секунд на розбіжність годинників
BACKUP_CODES_COUNT = 10


def start_totp_setup(user) -> tuple[str, str, str]:
    """Створює (або перезаписує непідтверджений) TOTP-пристрій.
    Повертає (secret, otpauth_uri, qr_svg_data_uri)."""
    secret = pyotp.random_base32(length=32)
    TOTPDevice.objects.update_or_create(
        user=user,
        defaults={"secret_encrypted": encrypt_str(secret), "confirmed": False, "last_used_step": 0},
    )
    uri = pyotp.TOTP(secret, interval=STEP).provisioning_uri(name=user.username, issuer_name=ISSUER)
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    data_uri = "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()
    return secret, uri, data_uri


def _clean(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isdigit())


def verify_totp(device: TOTPDevice, code: str) -> bool:
    """Перевіряє код і фіксує використаний крок часу (код не можна використати вдруге)."""
    code = _clean(code)
    if len(code) != 6:
        return False
    secret = decrypt_str(device.secret_encrypted)
    totp = pyotp.TOTP(secret, interval=STEP)
    now_step = int(time.time()) // STEP
    matched_step = None
    for offset in range(-VALID_WINDOW, VALID_WINDOW + 1):
        step = now_step + offset
        candidate = totp.generate_otp(step)
        if hmac.compare_digest(candidate, code):
            matched_step = step
    if matched_step is None:
        return False
    with transaction.atomic():
        locked = TOTPDevice.objects.select_for_update().get(pk=device.pk)
        if matched_step <= locked.last_used_step:
            return False  # повтор (replay)
        locked.last_used_step = matched_step
        locked.save(update_fields=["last_used_step"])
    return True


def confirm_totp(user, code: str) -> list[str] | None:
    device = getattr(user, "totp_device", None)
    if device is None or device.confirmed:
        return None
    if not verify_totp(device, code):
        return None
    device.confirmed = True
    device.confirmed_at = timezone.now()
    device.save(update_fields=["confirmed", "confirmed_at"])
    return regenerate_backup_codes(user)


def regenerate_backup_codes(user) -> list[str]:
    codes = []
    with transaction.atomic():
        BackupCode.objects.filter(user=user).delete()
        for _ in range(BACKUP_CODES_COUNT):
            # 10 байтів = 80 біт ентропії → 16 символів base32 у форматі xxxx-xxxx-xxxx-xxxx
            raw = base64.b32encode(secrets.token_bytes(10)).decode().lower()
            code = "-".join(raw[i : i + 4] for i in range(0, 16, 4))
            codes.append(code)
            BackupCode.objects.create(user=user, code_hash=BackupCode.hash_code(code))
    return codes


def use_backup_code(user, code: str) -> bool:
    code_hash = BackupCode.hash_code(code or "")
    with transaction.atomic():
        bc = (
            BackupCode.objects.select_for_update()
            .filter(user=user, code_hash=code_hash, used_at__isnull=True)
            .first()
        )
        if bc is None:
            return False
        bc.used_at = timezone.now()
        bc.save(update_fields=["used_at"])
    return True


def verify_second_factor(user, code: str) -> str | None:
    """Повертає 'totp' / 'backup_code' при успіху, інакше None."""
    device = getattr(user, "totp_device", None)
    if device is None or not device.confirmed:
        return None
    if verify_totp(device, code):
        return "totp"
    compact = re.sub(r"[\s-]", "", code or "")
    # 16 символів — нові коди (80 біт); 10 — старі, видані до оновлення (діють до перевипуску)
    if len(compact) in (10, 16):
        if use_backup_code(user, code):
            return "backup_code"
    return None


def disable_2fa(user) -> None:
    TOTPDevice.objects.filter(user=user).delete()
    BackupCode.objects.filter(user=user).delete()


def generate_secret_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
