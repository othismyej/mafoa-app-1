"""Lecture des pièces par l'IA depuis la fiche candidature (spec KAN-41, règles 17 et 28)."""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import DIRECTION_ROLES
from apps.audit.models import AuditAction
from apps.audit.services import deny, log, role_required_logged
from apps.documents.lecture import LectureIndisponible, lancer_pour_candidature
from apps.documents.models import Extraction
from apps.documents.presenters import lecture_context

from .models import Candidature, Piece

MODES = {"lire": False, "relire": True}


def pieces_avec_extractions(candidature_pk: int) -> list[Piece]:
    """Pièces de la candidature avec leurs extractions (sans le texte extrait)."""
    return list(
        Piece.objects.filter(candidature_id=candidature_pk)
        .order_by("type", "id")
        .prefetch_related(
            Prefetch(
                "extractions",
                queryset=Extraction.objects.defer("texte").order_by("-created_at", "-id"),
            )
        )
    )


def _etat(request, candidature, *, message: str = "", ton: str = "success", status: int = 200):
    """Fragment d'état : statuts seulement, aucune donnée personnelle (règle 28)."""
    context = {
        "c": candidature,
        **lecture_context(candidature, pieces_avec_extractions(candidature.pk), avec_donnees=False),
        "lecture_message": message,
        "lecture_message_ton": ton,
        "fragment_etat": True,
    }
    return render(request, "recruitment/_lecture_etat.html", context, status=status)


def _message_rapport(rapport) -> str:
    parts = [
        _("%(n)d pièce(s) mise(s) en lecture") % {"n": len(rapport.en_file)},
    ]
    if rapport.deja_lues:
        parts.append(_("%(n)d déjà lue(s)") % {"n": len(rapport.deja_lues)})
    if rapport.en_cours:
        parts.append(_("%(n)d lecture(s) déjà en cours") % {"n": len(rapport.en_cours)})
    return " · ".join(parts) + "."


@require_POST
def lecture_lancer(request, pk: int):
    """« Lire les pièces » / « Relire » (règle 17). Refus renvoyés (jamais levés) pour que la
    trace ``access_denied`` survive à ``ATOMIC_REQUESTS``."""
    if not (request.user.is_authenticated and request.user.has_role(*DIRECTION_ROLES)):
        return deny(request, candidature_id=pk)
    candidature = get_object_or_404(
        Candidature.objects.select_related("poste_concours", "offre"), pk=pk
    )
    htmx = request.headers.get("HX-Request") == "true"
    mode = request.POST.get("mode", "lire")
    if mode not in MODES:
        mode = "lire"
    try:
        rapport = lancer_pour_candidature(candidature, force=MODES[mode], user=request.user)
    except LectureIndisponible as exc:
        message = str(exc.message)
        status = 400 if exc.code == "spontanee" else 503
        if htmx:
            return _etat(request, candidature, message=message, ton="warning", status=status)
        messages.warning(request, message)
        return redirect("recruitment:candidature_detail", pk=pk)
    log(
        AuditAction.AI_CALL,
        obj=candidature,
        request=request,
        object_repr=f"Candidature #{candidature.pk}",
        metadata={
            "operation": "lecture_pieces",
            "mode": mode,
            "candidature_id": candidature.pk,
            **rapport.as_metadata(),
        },
    )
    message = _message_rapport(rapport)
    if htmx:
        return _etat(request, candidature, message=message)
    messages.success(request, message)
    return redirect("recruitment:candidature_detail", pk=pk)


@require_GET
@role_required_logged(*DIRECTION_ROLES, candidature_kwarg="pk")
def lecture_etat(request, pk: int):
    """Fragment d'état rafraîchi pendant la lecture (règle 28). Non journalisé : il n'affiche
    que des statuts, jamais de contenu de dossier."""
    candidature = get_object_or_404(Candidature, pk=pk)
    return _etat(request, candidature)
