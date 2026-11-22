"""Settings des tests (pytest)."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-secret")
os.environ.setdefault("DATABASE_URL", "postgres://mafoa:mafoa@localhost:5432/mafoa")

from .base import *  # noqa: E402, F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
CELERY_TASK_ALWAYS_EAGER = True
ACCOUNT_RATE_LIMITS = False
ANTHROPIC_API_KEY = ""
# WhiteNoise inutile en test (et STATIC_ROOT n'existe pas avant collectstatic).
MIDDLEWARE = [m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"]  # noqa: F405
