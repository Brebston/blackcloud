import sys
from datetime import timedelta
from pathlib import Path

from .env import env, env_bool, env_int, read_secret

BASE_DIR = Path(__file__).resolve().parent.parent

TESTING = env_bool("DJANGO_TEST") or "pytest" in sys.modules
DEBUG = env_bool("DJANGO_DEBUG")

SECRET_KEY = read_secret("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-dev-only-key-" + "x" * 40
    else:
        raise RuntimeError("DJANGO_SECRET_KEY не задано (див. scripts/init-secrets.sh)")

DOMAIN = env("DOMAIN", "localhost")
ALLOWED_HOSTS = [DOMAIN, "backend", "127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = [f"https://{DOMAIN}"]
ADMIN_URL_PREFIX = env("ADMIN_URL_PREFIX", "bc-admin").strip("/")

# Channels без daphne: ASGI-сервер — uvicorn.
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "apps.core",
    "apps.accounts",
    "apps.storage",
    "apps.calendars",
    "apps.chat",
    "apps.mail",
]

MIDDLEWARE = [
    "apps.core.middleware.MaxBodySizeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.SessionTrackingMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.NoCacheApiMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ─── База даних ────────────────────────────────────────────────
if TESTING or env("DATABASE_URL", "").startswith("sqlite"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "test.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "blackcloud"),
            "USER": env("POSTGRES_USER", "blackcloud"),
            "PASSWORD": read_secret("POSTGRES_PASSWORD"),
            "HOST": env("POSTGRES_HOST", "postgres"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"connect_timeout": 5},
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Redis: кеш, сесії-лічильники, Channels, Celery ──────────────
REDIS_HOST = env("REDIS_HOST", "redis")
REDIS_PASSWORD = read_secret("REDIS_PASSWORD", "")
_redis_auth = f":{REDIS_PASSWORD}@" if REDIS_PASSWORD else ""
REDIS_URL = f"redis://{_redis_auth}{REDIS_HOST}:6379"

if TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": f"{REDIS_URL}/0"}}
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [f"{REDIS_URL}/2"], "capacity": 500, "expiry": 30},
        }
    }

CELERY_BROKER_URL = f"{REDIS_URL}/1"
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = TESTING
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {"apps.storage.tasks.scan_file": {"queue": "scan"}}
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_BEAT_SCHEDULE = {
    "purge-expired-trash": {"task": "apps.storage.tasks.purge_expired_trash", "schedule": timedelta(hours=6)},
    "requeue-stuck-scans": {"task": "apps.storage.tasks.requeue_stuck_scans", "schedule": timedelta(minutes=5)},
    "cleanup-stale-uploads": {"task": "apps.storage.tasks.cleanup_stale_uploads", "schedule": timedelta(hours=1)},
    "expire-public-links": {"task": "apps.storage.tasks.expire_public_links", "schedule": timedelta(hours=1)},
    "event-reminders": {"task": "apps.calendars.tasks.send_due_reminders", "schedule": timedelta(minutes=1)},
    "cleanup-sessions": {"task": "apps.accounts.tasks.cleanup_sessions", "schedule": timedelta(hours=12)},
}

# ─── Автентифікація ─────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/login"

REGISTRATION_OPEN = env_bool("REGISTRATION_OPEN", False)
REQUIRE_2FA_FOR_STAFF = env_bool("REQUIRE_2FA_FOR_STAFF", True)
LOGIN_MAX_FAILURES = env_int("LOGIN_MAX_FAILURES", 5)
LOGIN_LOCKOUT_SECONDS = env_int("LOGIN_LOCKOUT_SECONDS", 15 * 60)
# Скільки секунд після підтвердження пароля дозволені чутливі дії ("sudo mode")
REAUTH_WINDOW_SECONDS = 10 * 60
FIELD_ENCRYPTION_KEY = read_secret("FIELD_ENCRYPTION_KEY")
if not FIELD_ENCRYPTION_KEY and (DEBUG or TESTING):
    FIELD_ENCRYPTION_KEY = "ZGV2LW9ubHktaW5zZWN1cmUtZmVybmV0LWtleS0wMDA="

# ─── Сесії та cookie ────────────────────────────────────────────
SECURE_COOKIES = not (DEBUG or TESTING) or env_bool("FORCE_SECURE_COOKIES")
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = env_int("SESSION_AGE_HOURS", 12) * 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_HTTPONLY = True  # SPA отримує токен через /api/auth/csrf/
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_SECURE = SECURE_COOKIES
CSRF_USE_SESSIONS = False
if SECURE_COOKIES:
    # Префікс __Host- гарантує Secure, Path=/ і відсутність Domain
    SESSION_COOKIE_NAME = "__Host-bc_session"
    CSRF_COOKIE_NAME = "__Host-bc_csrf"
else:
    SESSION_COOKIE_NAME = "bc_session"
    CSRF_COOKIE_NAME = "bc_csrf"

# ─── Заголовки безпеки (дублюють Traefik — захист у глибину) ─────
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = False
SECURE_SSL_REDIRECT = False  # редирект робить Traefik
SECURE_HSTS_SECONDS = 0 if DEBUG else 63072000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SILENCED_SYSTEM_CHECKS = ["security.W008"]  # SSL redirect виконує Traefik

DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024
MAX_REQUEST_BODY = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

# ─── DRF ────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.core.authentication.CsrfSessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "1200/min",
        "login": "10/min",
        "two_factor": "10/min",
        "register": "5/hour",
        "upload_chunk": "1200/min",
        "public_link": "60/min",
        "chat_send": "120/min",
        "mail_send": "30/hour",
        "user_search": "60/min",
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}
if TESTING:
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []

# ─── Файлове сховище ────────────────────────────────────────────
GB = 1024**3
DEFAULT_QUOTA_BYTES = env_int("DEFAULT_QUOTA_GB", 10) * GB
MAX_FILE_SIZE = env_int("MAX_FILE_SIZE_GB", 10) * GB
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
UPLOAD_SESSION_TTL = timedelta(hours=24)
TRASH_RETENTION = timedelta(days=env_int("TRASH_RETENTION_DAYS", 30))
PUBLIC_LINK_MAX_DAYS = env_int("PUBLIC_LINK_MAX_DAYS", 30)
UNSCANNED_POLICY = env("UNSCANNED_POLICY", "block")  # block | allow

# local — шифровані чанки на Docker-томі (за замовчуванням);
# s3 — будь-яке S3-сумісне сховище (Garage, SeaweedFS, AWS S3, Backblaze B2 тощо)
OBJECT_STORE = "local" if TESTING else env("OBJECT_STORE", "local")
LOCAL_OBJECT_STORE_DIR = BASE_DIR / ".local-objects" if TESTING else Path(env("LOCAL_OBJECT_STORE_DIR", str(BASE_DIR / ".local-objects")))
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY = read_secret("S3_ACCESS_KEY")
S3_SECRET_KEY = read_secret("S3_SECRET_KEY")
S3_BUCKET = env("S3_BUCKET", "blackcloud-files")
FILE_MASTER_KEYS = read_secret("FILE_MASTER_KEYS")
if not FILE_MASTER_KEYS and (DEBUG or TESTING):
    FILE_MASTER_KEYS = "1:" + "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="

CLAMAV_HOST = env("CLAMAV_HOST", "clamav")
CLAMAV_PORT = env_int("CLAMAV_PORT", 3310)
CLAMAV_MAX_STREAM = env_int("CLAMAV_MAX_STREAM_MB", 2000) * 1024 * 1024
CLAMAV_ENABLED = not TESTING and env_bool("CLAMAV_ENABLED", True)

# ─── Пошта ─────────────────────────────────────────────────────
MAIL_DOMAIN = env("MAIL_DOMAIN", "example.com")
MAIL_HOSTNAME = env("MAIL_HOSTNAME", f"mail.{MAIL_DOMAIN}")
MAIL_AUTO_CREATE = env_bool("MAIL_AUTO_CREATE", True)
MAIL_DEFAULT_QUOTA_MB = env_int("MAIL_DEFAULT_QUOTA_MB", 2048)
IMAP_HOST = env("IMAP_HOST", "dovecot")
IMAP_PORT = env_int("IMAP_PORT", 143)
SMTP_HOST = env("SMTP_HOST", "postfix")
SMTP_PORT = env_int("SMTP_PORT", 10025)
DOVECOT_MASTER_USER = "webmail"
DOVECOT_MASTER_PASSWORD = read_secret("DOVECOT_MASTER_PASSWORD")
MAIL_MAX_ATTACHMENTS_BYTES = 15 * 1024 * 1024

# ─── Інтернаціоналізація ───────────────────────────────────────
LANGUAGE_CODE = env("LANGUAGE_CODE", "uk")
TIME_ZONE = env("TIME_ZONE", "Europe/Kyiv")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/django-static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "blackcloud": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
