from celery import shared_task

from .base import get_source
from .sync import sync_from_source


@shared_task(name="site_source.sync")
def sync_site_task(source_name: str | None = None) -> dict:
    """Synchronisation de rattrapage (prévue toutes les 15 min via Celery Beat)."""
    report = sync_from_source(get_source(source_name))
    return {"created": report.created, "updated": report.updated}
