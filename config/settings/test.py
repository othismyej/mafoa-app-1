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
# Les tests vérifient le comportement de production : jamais lu depuis .env.
MFA_REQUIRED = True
ACCOUNT_RATE_LIMITS = False
ANTHROPIC_API_KEY = ""
# WhiteNoise inutile en test (et STATIC_ROOT n'existe pas avant collectstatic).
MIDDLEWARE = [m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"]  # noqa: F405
# Nom de la base de test surchargeable : permet de lancer plusieurs suites en parallèle
# (agents, worktrees) sans qu'elles se marchent dessus.
if os.environ.get("TEST_DATABASE_NAME"):
    DATABASES["default"]["TEST"] = {"NAME": os.environ["TEST_DATABASE_NAME"]}  # noqa: F405
