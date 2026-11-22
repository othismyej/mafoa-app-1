import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("mafoa")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(name="mafoa.ping")
def ping() -> str:
    """Tâche de diagnostic : vérifie que le worker répond."""
    return "pong"
