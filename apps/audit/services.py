"""Point d'entrée unique pour écrire dans le journal d'audit."""

from __future__ import annotations

import ipaddress
import json
from functools import wraps
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.forms.models import model_to_dict
from django.utils.text import capfirst
from django.views.defaults import permission_denied

from .models import AuditAction, JournalAudit


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


# --------------------------------------------------------------------------------------
# Consultations de dossier et accès refusés (KAN-14)
# --------------------------------------------------------------------------------------

#: Valeurs admises pour ``metadata.vue`` (règles 2 et 15 de la spec KAN-14).
VUES_CONSULTATION = frozenset(
    {
        "fiche",
        "fiche_apres_decision",
        "piece",
        "fragment",
        "admin_detail",
        "admin_liste",
        "journal_liste",
        "journal_detail",
        "journal_dossier",
        # Récapitulatif d'une action en lot (KAN-15, règle 31) : liste nominative.
        "lot_recapitulatif",
        # Liste des candidatures spontanées (KAN-39, règle 38).
        "spontanees_liste",
    }
)
MODES_PIECE = frozenset({"apercu", "telechargement"})

_CANDIDATURE = "recruitment.candidature"
_PIECE = "recruitment.piece"
_CANDIDAT = "recruitment.candidat"


def _cached(instance: models.Model, field: str):
    """Objet lié déjà chargé (``select_related``) sans requête supplémentaire, sinon ``None``."""
    return instance._state.fields_cache.get(field)


def _consultation_refs(obj: models.Model) -> dict[str, int]:
    """Identifiants internes dérivables de l'objet consulté, sans donnée nominative."""
    label = obj._meta.label_lower
    refs: dict[str, int] = {}
    if label == _CANDIDATURE:
        refs["candidature_id"] = obj.pk
        refs["candidat_id"] = obj.candidat_id
    elif label == _PIECE:
        refs["piece_id"] = obj.pk
        refs["candidature_id"] = obj.candidature_id
        candidature = _cached(obj, "candidature")
        if candidature is not None:
            refs["candidat_id"] = candidature.candidat_id
    elif label == _CANDIDAT:
        refs["candidat_id"] = obj.pk
    else:
        candidature_id = getattr(obj, "candidature_id", None)
        candidature = _cached(obj, "candidature") if candidature_id else None
        if candidature_id is None and getattr(obj, "piece_id", None):
            # Objet rattaché à une pièce (ex. Extraction) : la pièce porte la candidature.
            piece = obj.piece
            refs["piece_id"] = piece.pk
            candidature_id = piece.candidature_id
            candidature = _cached(piece, "candidature")
        if candidature_id is not None:
            refs["candidature_id"] = candidature_id
        if candidature is not None:
            refs["candidat_id"] = candidature.candidat_id
    return refs


def consultation_repr(obj: models.Model) -> str:
    """Libellé sans nom d'un objet consulté (règle 2) : ``Candidature #42``, ``Pièce #7
    (Candidature #42)``, ``Candidat #3``, ``<Type> #<id>``."""
    label = obj._meta.label_lower
    if label == _CANDIDATURE:
        return f"Candidature #{obj.pk}"
    if label == _PIECE:
        return f"Pièce #{obj.pk} (Candidature #{obj.candidature_id})"
    if label == _CANDIDAT:
        return f"Candidat #{obj.pk}"
    return f"{capfirst(obj._meta.verbose_name)} #{obj.pk}"


def log_consultation(
    request,
    obj: models.Model | None,
    vue: str,
    *,
    object_repr: str = "",
    **metadata: Any,
) -> JournalAudit:
    """Journalise une consultation de dossier (action ``view``), spec KAN-14 règles 1 à 6.

    Point d'entrée unique pour la fiche candidature, les pièces (KAN-12), les fragments HTMX
    de dossier, l'administration Django et les écrans du journal.

    - ``request`` : requête courante (acteur, adresse IP, navigateur) ;
    - ``obj`` : objet consulté (candidature, pièce, candidat, objet d'évaluation…) ou ``None``
      (liste) ; ``object_repr`` est alors obligatoire ;
    - ``vue`` : nature de la consultation, parmi :data:`VUES_CONSULTATION` ;
    - ``**metadata`` : métadonnées complémentaires (``mode`` pour une pièce, ``fragment`` pour
      un fragment, filtres d'une liste…). ``candidature_id``, ``candidat_id`` et ``piece_id``
      sont déduits de ``obj`` ; une valeur passée explicitement est prioritaire.

    ``object_repr`` est construit sans nom (``consultation_repr``). Ne jamais passer de nom,
    CIN, email, téléphone ni date de naissance d'un candidat dans ``metadata``.

    Exemple pour la vue pièce de KAN-12 (à appeler juste avant de servir le contenu, après le
    contrôle d'accès, et seulement pour la première requête d'une ouverture : pas pour une
    requête ``Range`` au-delà du premier octet ni une miniature)::

        log_consultation(request, piece, "piece", mode="apercu")  # ou "telechargement"

    Toute erreur d'écriture est propagée : la réponse ne doit pas être servie (règle 6).
    """
    if vue not in VUES_CONSULTATION:
        raise ValueError(f"Vue de consultation inconnue : {vue!r}")
    if vue == "piece" and metadata.get("mode") not in MODES_PIECE:
        raise ValueError("Une consultation de pièce exige mode='apercu' ou 'telechargement'.")
    if obj is None and not object_repr:
        raise ValueError("object_repr est obligatoire pour une consultation sans objet.")
    data: dict[str, Any] = {"vue": vue}
    if obj is not None:
        data.update(_consultation_refs(obj))
    if vue == "fragment":
        data["htmx"] = True
    data.update({k: v for k, v in metadata.items() if v is not None})
    return log(
        AuditAction.VIEW,
        obj=obj,
        request=request,
        object_repr=object_repr or consultation_repr(obj),
        metadata=data,
    )


def log_access_denied(
    request,
    *,
    candidature_id: int | None = None,
    object_repr: str = "",
    **metadata: Any,
) -> JournalAudit:
    """Trace un refus d'accès (action ``access_denied``, règle 8).

    À appeler avant de **renvoyer** (et non lever) la réponse 403, pour que l'entrée survive à
    ``ATOMIC_REQUESTS`` : voir :func:`deny`.
    """
    data: dict[str, Any] = {"chemin": request.path}
    if candidature_id is not None:
        data["candidature_id"] = int(candidature_id)
        object_repr = object_repr or f"Candidature #{int(candidature_id)}"
    data.update(metadata)
    return log(
        AuditAction.ACCESS_DENIED,
        request=request,
        actor_repr="" if request.user.is_authenticated else "anonyme",
        object_repr=object_repr,
        metadata=data,
    )


def deny(request, **kwargs):
    """Journalise le refus puis renvoie la réponse 403 standard (sans la lever)."""
    log_access_denied(request, **kwargs)
    return permission_denied(request, PermissionDenied())


def role_required_logged(*roles: str, candidature_kwarg: str | None = None, object_repr=""):
    """Comme ``role_required``, mais le refus est journalisé (``access_denied``) et la 403 est
    renvoyée, de sorte que la trace persiste malgré ``ATOMIC_REQUESTS``.

    ``candidature_kwarg`` : nom de l'argument d'URL portant le numéro de candidature visé.
    ``object_repr`` : libellé sans nom, chaîne ou fonction ``(kwargs) -> str``.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if user.is_authenticated and user.has_role(*roles):
                return view(request, *args, **kwargs)
            if not user.is_authenticated:
                # Ne devrait pas arriver (LoginRequiredMiddleware) : rien n'est journalisé.
                raise PermissionDenied
            repr_ = object_repr(kwargs) if callable(object_repr) else object_repr
            return deny(
                request,
                candidature_id=kwargs.get(candidature_kwarg) if candidature_kwarg else None,
                object_repr=repr_,
            )

        return wrapper

    return decorator
