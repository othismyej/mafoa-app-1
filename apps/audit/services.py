"""Point d'entrée unique pour écrire dans le journal d'audit."""

from __future__ import annotations

import json
from typing import Any

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


def client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def log(
    action: str,
    *,
    actor=None,
    obj: models.Model | None = None,
    before: dict | None = None,
    after: dict | None = None,
    request=None,
    actor_repr: str = "",
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
        object_repr=str(obj)[:255] if obj is not None else "",
        before=before,
        after=after,
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
        metadata=metadata or {},
    )
