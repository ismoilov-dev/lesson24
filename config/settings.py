"""Lesson24 backend settings. All environment-specific values come from `.env`."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    # local
    "apps.users",
    "apps.courses",
    "apps.payments",
    "apps.certificates",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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

# --- Database ---------------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}
# Pooler (Neon / Supabase) bilan ishlaganda DB_CONN_MAX_AGE=0
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"]["OPTIONS"] = {"sslmode": env("DB_SSLMODE", default="prefer")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- I18N / time ------------------------------------------------------------
LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

# --- Static / media ---------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
# Nginx `internal` location: himoyalangan fayllar (to'lov cheklari) X-Accel-Redirect orqali beriladi
PROTECTED_MEDIA_URL = "/protected-media/"
USE_X_ACCEL_REDIRECT = env.bool("USE_X_ACCEL_REDIRECT", default=not DEBUG)
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
PAYMENT_SCREENSHOT_MAX_BYTES = 5 * 1024 * 1024
COURSE_COVER_MAX_BYTES = 5 * 1024 * 1024

# Redis'siz: throttling hisoblagichlari barcha gunicorn worker'lar uchun umumiy bo'lishi kerak
# (LocMem har worker'da alohida). Jadval: `python manage.py createcachetable`.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

# --- DRF / JWT / docs -------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # Faqat JSON; fayl yuklash (multipart) — faqat kurs muqovasi va to'lov cheki view'larida
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_RATES": {"auth_verify": "5/min"},
    # Nginx ortida 1: throttling haqiqiy IP (X-Forwarded-For) bo'yicha ishlaydi
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.errors.exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Lesson24 API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    # Swagger'da panel bo'yicha tartib; frontend panelni GET /api/v1/me/ → role orqali tanlaydi
    "TAGS": [
        {"name": "auth", "description": "Hamma: Telegram kodi → JWT"},
        {"name": "me", "description": "Kirgan foydalanuvchi: profil, kurslarim, sertifikatlarim"},
        {"name": "courses", "description": "Ommaviy: katalog"},
        {"name": "lessons", "description": "Student: dars, video, test"},
        {"name": "orders", "description": "Student / ota-ona: to'lov va buyurtmalar"},
        {"name": "parents", "description": "Ota-ona: farzandlar va progress"},
        {"name": "certificates", "description": "Ommaviy: sertifikatni tekshirish"},
        {"name": "teacher", "description": "O'qituvchi paneli: o'z kurslari, profil"},
        {"name": "admin", "description": "Admin paneli"},
    ],
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# --- Integrations -----------------------------------------------------------
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="").lstrip("@")
TELEGRAM_ADMIN_CHAT_ID = env("TELEGRAM_ADMIN_CHAT_ID", default="")
# True — xabarlar fon thread'isiz, darhol yuboriladi (testlar uchun)
TELEGRAM_NOTIFY_SYNC = env.bool("TELEGRAM_NOTIFY_SYNC", default=False)

PAYMENT_CARD_NUMBER = env("PAYMENT_CARD_NUMBER", default="")
PAYMENT_CARD_HOLDER = env("PAYMENT_CARD_HOLDER", default="")

# Kurs muqovalari ImgBB'ga yuklanadi (ommaviy rasm xostingi); .env da IMGBB_API_KEY (yoki IMGBB)
IMGBB_API_KEY = env("IMGBB_API_KEY", default="") or env("IMGBB", default="")

# drive — Google Drive embed (iframe); bunny — Bunny Stream imzolangan HLS
VIDEO_PROVIDER = env("VIDEO_PROVIDER", default="drive")
VIDEO_PROVIDER_API_KEY = env("VIDEO_PROVIDER_API_KEY", default="")
VIDEO_SIGNING_KEY = env("VIDEO_SIGNING_KEY", default="")
VIDEO_CDN_HOSTNAME = env("VIDEO_CDN_HOSTNAME", default="")
VIDEO_URL_TTL = env.int("VIDEO_URL_TTL", default=3600)

CERTIFICATE_BASE_URL = env("CERTIFICATE_BASE_URL", default="https://lesson24.uz/certificate/")

# --- Production security ----------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # preload ro'yxatiga qo'shilish — qaytarib bo'lmaydigan qaror, ataylab yoqilmagan
    SILENCED_SYSTEM_CHECKS = ["security.W021"]
    CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if "." in host]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    # httpx so'rov URL'ini log qiladi — Telegram Bot API URL'ida token bor
    "loggers": {"httpx": {"level": "WARNING"}, "httpcore": {"level": "WARNING"}},
}
