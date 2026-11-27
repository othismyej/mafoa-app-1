"""Relance de la détection, résolution et réouverture des anomalies depuis la fiche (KAN-42).

Contrôle des rôles côté serveur sur chaque point d'entrée et chaque fragment HTMX ; les refus
sont journalisés et la réponse est **renvoyée** (jamais levée) pour que l'entrée d'audit
survive à ``ATOMIC_REQUESTS``.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.defaults import page_not_found

from apps.accounts.permissions import DIRECTION_ROLES
from apps.audit.models import AuditAction
from apps.audit.services import deny, log, log_consultation
from apps.evaluation.anomalies import resolution
from apps.evaluation.anomalies.service import detecter_anomalies
from apps.evaluation.models import Anomalie

from .models import Candidature
from .views import _fiche_context, _fiche_queryset


def _autorise(request) -> bool:
    return request.user.is_authenticated and request.user.has_role(*DIRECTION_ROLES)


def _htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _reponse(request, pk: int, anomalie_form: dict, status: int = 200):
    candidature = _fiche_queryset().get(pk=pk)
    context = _fiche_context(candidature, anomalie_form=anomalie_form)
    if _htmx(request):
        # Fragment de dossier (NOM Prénom des candidatures liées) : consultation journalisée.
        log_consultation(request, candidature, "fragment", fragment="anomalies")
        response = render(request, "recruitment/_anomalies.html", context, status=status)
    else:
        # Sans JavaScript, la fiche complète est réaffichée : c'est une consultation.
        log_consultation(request, candidature, "fiche_apres_decision", operation="anomalie")
        response = render(request, "recruitment/candidature_detail.html", context, status=status)
    patch_vary_headers(response, ["HX-Request"])
    return response


@require_POST
def anomalies_detecter(request, pk: int):
    """Relance synchrone de la détection pour cette candidature, doublons compris (règle 22)."""
    if not _autorise(request):
        return deny(request, candidature_id=pk)
    candidature = get_object_or_404(Candidature, pk=pk)
    rapport = detecter_anomalies(
        [candidature.pk],
        declencheur="manuel",
        acteur=request.user,
        request=request,
        objet=candidature,
    )
    if rapport.erreurs:
        form = {"erreur": _("La détection n’a pas pu aboutir. Réessayez dans quelques instants.")}
    else:
        form = {
            "succes": _(
                "Détection effectuée : %(n)d nouvelle(s) anomalie(s), %(d)d marquée(s) « cause "
                "disparue »."
            )
            % {"n": rapport.total_creees, "d": rapport.cause_disparue}
        }
    if not _htmx(request) and not rapport.erreurs:
        messages.success(request, form["succes"])
        return redirect("recruitment:candidature_detail", pk=pk)
    return _reponse(request, pk, form, status=200 if not rapport.erreurs else 503)


def _anomalie_ou_404(request, pk: int, anomalie_pk: int):
    """Anomalie de la candidature de l'URL ; un identifiant d'une autre candidature est un envoi
    forgé : 404 et trace (règle 34). Renvoie ``(anomalie, None)`` ou ``(None, réponse)``."""
    get_object_or_404(Candidature.objects.only("pk"), pk=pk)
    anomalie = Anomalie.objects.filter(pk=anomalie_pk).only("pk", "candidature_id").first()
    if anomalie is None:
        return None, page_not_found(request, Http404())
    if anomalie.candidature_id != pk:
        log(
            AuditAction.UPDATE,
            object_repr=f"Candidature #{pk}",
            request=request,
            metadata={
                "refus": "anomalie_hors_candidature",
                "candidature_id": pk,
                "anomalie_id": anomalie_pk,
            },
        )
        return None, page_not_found(request, Http404())
    return anomalie, None


def _operation(request, pk: int, anomalie_pk: int, operation: str):
    if not _autorise(request):
        return deny(request, candidature_id=pk, anomalie_id=anomalie_pk)
    anomalie, refus = _anomalie_ou_404(request, pk, anomalie_pk)
    if refus is not None:
        return refus
    issue = request.POST.get("issue", "")
    motif = request.POST.get("motif", "")
    try:
        if operation == resolution.RESOLUTION:
            resolution.resoudre(anomalie.pk, request.user, issue, motif, request)
            succes = _("Anomalie résolue.")
        else:
            resolution.rouvrir(anomalie.pk, request.user, motif, request)
            succes = _("Anomalie rouverte.")
    except resolution.AnomalieError as exc:
        if exc.status == 409:
            # Formulaire réinitialisé sur l'état à jour ; la première résolution est conservée.
            form = {"erreur": exc.message}
        else:
            form = {
                "anomalie_id": anomalie.pk,
                "issue": issue,
                "motif": motif,
                "erreur": exc.message,
            }
        return _reponse(request, pk, form, status=exc.status)
    if not _htmx(request):
        messages.success(request, succes)
        return redirect("recruitment:candidature_detail", pk=pk)
    return _reponse(request, pk, {"succes": succes})


@require_POST
def anomalie_resoudre(request, pk: int, anomalie_pk: int):
    """Résolution humaine motivée (règle 25)."""
    return _operation(request, pk, anomalie_pk, resolution.RESOLUTION)


@require_POST
def anomalie_rouvrir(request, pk: int, anomalie_pk: int):
    """Réouverture motivée (règle 26)."""
    return _operation(request, pk, anomalie_pk, resolution.REOUVERTURE)
