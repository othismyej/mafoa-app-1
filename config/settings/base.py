"""Settings communs à tous les environnements.

Tous les secrets proviennent des variables d'environnement (voir .env.example).
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Tiers
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "csp",
    # Apps Mafoa
    "apps.accounts",
    "apps.audit",
    "apps.recruitment",
    "apps.documents",
    "apps.evaluation",
    "apps.exports",
    "integrations.site_source",
    "ai",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.accounts.middleware.LoginRequiredMiddleware",
    "apps.accounts.middleware.MFARequiredMiddleware",
    "apps.accounts.middleware.NoStoreForAuthenticatedMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentification -------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "account_login"

# django-allauth : connexion par email, aucune inscription publique (voir AccountAdapter).
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "none"  # comptes créés par invitation : email déjà connu
ACCOUNT_SESSION_REMEMBER = False
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_RATE_LIMITS = {
    "login_failed": "5/5m/ip,5/15m/key",
    "login": "20/m/ip",
    "reset_password": "5/h/ip,3/h/key",
}
# Double authentification obligatoire. Désactivable uniquement en développement
# (voir dev.py) ; toujours forcée en production (voir prod.py).
MFA_REQUIRED = env.bool("MFA_REQUIRED", default=True)
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "Mafoa AI"

# Nombre de proxys de confiance devant l'application (load balancer…). 0 = aucun :
# l'en-tête X-Forwarded-For, falsifiable, est alors ignoré (audit et limitation des tentatives).
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)
ALLAUTH_TRUSTED_PROXY_COUNT = TRUSTED_PROXY_COUNT

# --- Journal d'audit (KAN-14) ----------------------------------------------
# Plafond de lignes d'un export du journal : au-delà, aucun fichier n'est produit.
AUDIT_EXPORT_MAX_ROWS = env.int("AUDIT_EXPORT_MAX_ROWS", default=50_000)

# --- Sessions et cookies ----------------------------------------------------
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=8 * 3600)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# CSP : tout est servi localement (CSS Tailwind compilé, polices, HTMX) ; aucune origine externe.
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "font-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "frame-ancestors": ["'none'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
        "form-action": ["'self'"],
    }
}

# --- i18n -------------------------------------------------------------------
LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", "Français"), ("ar", "العربية")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Africa/Casablanca"
USE_I18N = True
USE_TZ = True

# --- Fichiers statiques -----------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Celery -----------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_TIME_LIMIT = 15 * 60
CELERY_TIMEZONE = TIME_ZONE

# --- Intégration site -------------------------------------------------------
# "fixture" (données fictives), plus tard "database" ou "api".
SITE_SOURCE = env("SITE_SOURCE", default="fixture")
SITE_SOURCE_FIXTURE_SEED = env.int("SITE_SOURCE_FIXTURE_SEED", default=20260101)
# Visionneuse des pièces (KAN-12) : taille maximale lue en mémoire et délai de lecture (s).
PIECE_VIEWER_MAX_BYTES = env.int("PIECE_VIEWER_MAX_BYTES", default=20 * 1024 * 1024)
PIECE_SOURCE_TIMEOUT = env.float("PIECE_SOURCE_TIMEOUT", default=10.0)

# --- IA ---------------------------------------------------------------------
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
AI_MODEL = env("AI_MODEL", default="claude-opus-5-5")
AI_PSEUDONYMIZE = env.bool("AI_PSEUDONYMIZE", default=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
