"""Ключі доступу (WebAuthn / passkeys): Face ID, Touch ID, Windows Hello, апаратні ключі.

Перевірку підписів і формату виконує бібліотека py_webauthn (Duo Labs); тут — політика:

* виклик (challenge) одноразовий, живе 5 хв і зберігається в серверній сесії;
* завжди вимагається перевірка користувача на пристрої (біометрія або PIN) — тому вхід
  ключем доступу вважається двофакторним (mfa) і відкриває адмін-функції;
* ключі прив'язані до домену (rp_id) і origin сайту — фішинговий сайт ними не скористається;
* лічильник підписів (sign_count) захищає від клонованих апаратних ключів (passkeys
  iCloud/Google мають 0 — тоді перевірка не застосовується);
* userHandle з відповіді має збігатися з власником ключа.
"""

import json
import time

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .models import Passkey, User

CHALLENGE_TTL = 300
SESSION_KEY = "webauthn"
TIMEOUT_MS = 120_000
# Невдалі спроби входу ключем з однієї IP (вхід без логіна — блокуємо за IP)
IP_MAX_FAILURES = 20
IP_FAILURE_WINDOW = 15 * 60

# Відомі менеджери ключів за AAGUID — лише для зручної назви в налаштуваннях
KNOWN_PROVIDERS = {
    "fbfc3007-154e-4ecc-8c0b-6e020557d7bd": "iCloud Keychain",
    "dd4ec289-e01d-41c9-bb89-70fa845d4bf2": "iCloud Keychain",
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": "Google Password Manager",
    "adce0002-35bc-c60a-648b-0b25f1f05503": "Chrome (Mac)",
    "08987058-cadc-4b81-b6e1-30de50dcbe96": "Windows Hello",
    "9ddd1817-af5a-4672-a2b9-3e3dd95000a9": "Windows Hello",
    "6028b017-b1d4-4c02-b4b3-afcdafc96bb2": "Windows Hello",
    "bada5566-a7aa-401f-bd96-45619a55120d": "1Password",
    "d548826e-79b4-db40-a3d8-11116f7e8349": "Bitwarden",
}


class PasskeyError(Exception):
    """Будь-яка помилка ключа доступу. Текст для користувача — загальний (без деталей)."""


# ─────────────────────────── Виклики (challenge) ───────────────────────────


def _store(request, purpose: str, challenge: bytes, **extra) -> None:
    request.session[SESSION_KEY] = {"p": purpose, "c": bytes_to_base64url(challenge), "ts": int(time.time()), **extra}


def _take(request, purpose: str) -> dict:
    """Забирає виклик із сесії (одноразово). Інша мета, прострочений або відсутній — помилка."""
    data = request.session.pop(SESSION_KEY, None)
    if not isinstance(data, dict) or data.get("p") != purpose or time.time() - data.get("ts", 0) > CHALLENGE_TTL:
        raise PasskeyError("challenge")
    return data


def _descriptor(pk: Passkey) -> PublicKeyCredentialDescriptor:
    transports = []
    for t in pk.transports or []:
        try:
            transports.append(AuthenticatorTransport(t))
        except ValueError:
            pass
    return PublicKeyCredentialDescriptor(id=bytes(pk.credential_id), transports=transports or None)


def _options(opts) -> dict:
    return json.loads(options_to_json(opts))


# ─────────────────────────── Реєстрація ───────────────────────────


def registration_options(request, user: User, *, platform: bool = False) -> dict:
    """platform=True — запропонувати саме вбудований у пристрій спосіб (Face ID / Touch ID / Windows Hello)."""
    opts = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=user.pk.bytes,  # непрозорий ідентифікатор, без персональних даних
        user_name=user.username,
        user_display_name=user.display_name or user.username,
        timeout=TIMEOUT_MS,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM if platform else None,
            resident_key=ResidentKeyRequirement.REQUIRED,  # вхід без введення логіна
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        # Пристрій, на якому ключ уже є, не створить другий
        exclude_credentials=[_descriptor(pk) for pk in user.passkeys.all()],
    )
    _store(request, "register", opts.challenge, uid=str(user.pk))
    return _options(opts)


def register(request, user: User, credential: dict, name: str = "") -> Passkey:
    data = _take(request, "register")
    if data.get("uid") != str(user.pk):
        raise PasskeyError("user")
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(data["c"]),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as exc:
        raise PasskeyError("verify") from exc
    if Passkey.objects.filter(credential_id=verified.credential_id).exists():
        raise PasskeyError("duplicate")
    response = credential.get("response") if isinstance(credential, dict) else None
    transports = [str(t)[:16] for t in (response or {}).get("transports") or []][:8]
    aaguid = str(verified.aaguid or "")
    return Passkey.objects.create(
        user=user,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=transports,
        aaguid=aaguid,
        backed_up=bool(getattr(verified, "credential_backed_up", False)),
        name=(name or default_name(request, aaguid))[:64],
    )


def default_name(request, aaguid: str = "") -> str:
    ua = request.META.get("HTTP_USER_AGENT", "")
    device = (
        "iPhone" if "iPhone" in ua else
        "iPad" if "iPad" in ua else
        "Android" if "Android" in ua else
        "Mac" if "Macintosh" in ua else
        "Windows" if "Windows" in ua else
        "Linux" if "Linux" in ua else
        ""
    )  # fmt: skip
    provider = KNOWN_PROVIDERS.get(aaguid, "")
    parts = [p for p in (device, provider) if p]
    return " · ".join(parts) or "Passkey"


# ─────────────────────────── Вхід ───────────────────────────


def _ip_key(ip: str | None) -> str:
    return f"passkey-fail:{ip or '-'}"


def ip_locked(ip: str | None) -> bool:
    return (cache.get(_ip_key(ip)) or 0) >= IP_MAX_FAILURES


def register_ip_failure(ip: str | None) -> None:
    key = _ip_key(ip)
    if not cache.add(key, 1, IP_FAILURE_WINDOW):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, IP_FAILURE_WINDOW)


def authentication_options(request, *, user: User | None = None, purpose: str = "login") -> dict:
    """user=None — вхід без логіна (браузер покаже ключі, збережені для цього сайту)."""
    allow = [_descriptor(pk) for pk in user.passkeys.all()] if user is not None else []
    opts = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        timeout=TIMEOUT_MS,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _store(request, purpose, opts.challenge)
    return _options(opts)


def authenticate(request, credential: dict, *, purpose: str = "login", expected_user: User | None = None) -> Passkey:
    """Перевіряє відповідь navigator.credentials.get(). Повертає ключ (з власником) або PasskeyError."""
    data = _take(request, purpose)
    if not isinstance(credential, dict):
        raise PasskeyError("format")
    try:
        raw_id = base64url_to_bytes(str(credential.get("rawId") or credential.get("id") or ""))
    except (ValueError, TypeError) as exc:
        raise PasskeyError("format") from exc
    pk = Passkey.objects.select_related("user").filter(credential_id=raw_id).first()
    if pk is None or not pk.user.is_active:
        raise PasskeyError("unknown")
    if expected_user is not None and pk.user_id != expected_user.pk:
        raise PasskeyError("user")
    handle = ((credential.get("response") or {}).get("userHandle")) or ""
    if handle:
        try:
            if base64url_to_bytes(handle) != pk.user.pk.bytes:
                raise PasskeyError("user")
        except (ValueError, TypeError) as exc:
            raise PasskeyError("format") from exc
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(data["c"]),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=bytes(pk.public_key),
            credential_current_sign_count=pk.sign_count,
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError) as exc:
        raise PasskeyError("verify") from exc
    Passkey.objects.filter(pk=pk.pk).update(
        sign_count=verified.new_sign_count,
        last_used_at=timezone.now(),
        backed_up=bool(verified.credential_backed_up),
    )
    return pk


def serialize(pk: Passkey) -> dict:
    return {
        "id": str(pk.pk),
        "name": pk.name,
        "provider": KNOWN_PROVIDERS.get(pk.aaguid, ""),
        "synced": pk.backed_up,
        "created_at": pk.created_at,
        "last_used_at": pk.last_used_at,
    }
