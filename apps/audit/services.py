"""Point d'entrée unique pour écrire dans le journal d'audit."""

from __future__ import annotations

import ipaddress
import json
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.forms.models import model_to_dict

from .models import JournalAudit


def snapshot(instance: models.Model | None) -> dict[str, Any] | None:
    """Photographie JSON d'une instance (état « avant » ou « après »)."""
    if instance is None:
        return None
    data = model_to_dict(instance)
    data["id"] = instance.pk
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder, default=str))


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request) -> str | None:
    """Adresse IP du client, fiable pour le journal d'audit.

    ``X-Forwarded-For`` peut être forgé par n'importe quel client : il n'est lu que si
    ``TRUSTED_PROXY_COUNT`` > 0 (proxys sous notre contrôle), et l'on prend alors la N-ième
    adresse en partant de la droite, celle ajoutée par notre proxy le plus externe.
    Une valeur invalide n'est jamais retenue et ne lève jamais d'exception.
    """
    if request is None:
        return None
    remote = _valid_ip(request.META.get("REMOTE_ADDR"))
    count = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if count > 0 and forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= count:
            return _valid_ip(parts[-count]) or remote
    return remote


def log(
    action: str,
    *,
    actor=None,
    obj: models.Model | None = None,
    before: dict | None = None,
    after: dict | None = None,
    request=None,
    actor_repr: str = "",
    object_repr: str = "",
    metadata: dict | None = None,
) -> JournalAudit:
    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    return JournalAudit.objects.create(
        actor=actor,
        actor_repr=actor_repr or (actor.email if actor else "système"),
        action=action,
        content_type=ContentType.objects.get_for_model(obj) if obj is not None else None,
        object_id=str(obj.pk) if obj is not None and obj.pk is not None else "",
        object_repr=(object_repr or (str(obj) if obj is not None else ""))[:255],
        before=before,
        after=after,
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
        metadata=metadata or {},
    )
