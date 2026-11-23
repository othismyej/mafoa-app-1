"""Exports Excel des candidatures par poste et par concours (spec KAN-37).

Réservés à ``admin`` et ``direction`` ; ``GET``/``HEAD`` uniquement. Un refus est journalisé
(``access_denied``) et la 403 renvoyée ; chaque export produit est journalisé (``export``) avec
le périmètre, les filtres et le nombre de lignes, sans nom ni identifiant de candidature.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from apps.accounts.permissions import DIRECTION_ROLES
from apps.audit.models import AuditAction
from apps.audit.services import log, role_required_logged
from apps.recruitment.filters import filter_candidatures
from apps.recruitment.models import Concours, PosteConcours

from . import candidatures as xlsx

FORMAT = "xlsx"


def _nombre(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _au_dela_du_plafond(request, total: int, retour: str):
    plafond = settings.EXPORT_CANDIDATURES_MAX_ROWS
    if total <= plafond:
        return None
    messages.warning(
        request,
        _(
            "L’export est limité à %(max)s candidatures (%(n)s correspondent) : "
            "restreignez les filtres."
        )
        % {"max": _nombre(plafond), "n": _nombre(total)},
    )
    return redirect(retour)


def _fichier(contenu: bytes, nom: str) -> HttpResponse:
    response = HttpResponse(contenu, content_type=xlsx.CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{nom}"'
    response["Cache-Control"] = "no-store"
    return response


@require_safe
@role_required_logged(
    *DIRECTION_ROLES,
    object_repr=lambda kw: f"Export des candidatures du poste #{kw.get('poste_pk')}",
)
def export_poste(request, concours_pk: int, poste_pk: int):
    """Candidatures d'un poste retenues par les filtres de la liste, toutes pages (règle 1)."""
    poste = get_object_or_404(
        PosteConcours.objects.select_related("concours"), pk=poste_pk, concours_id=concours_pk
    )
    queryset, flt = filter_candidatures(poste, request.GET)
    total = queryset.count()
    liste = reverse("recruitment:poste_candidatures", args=[concours_pk, poste_pk])
    query = flt.querystring()
    refus = _au_dela_du_plafond(request, total, f"{liste}?{query}" if query else liste)
    if refus is not None:
        return refus
    contenu = xlsx.classeur_poste(poste, queryset)
    log(
        AuditAction.EXPORT,
        obj=poste,
        request=request,
        metadata={
            "vue": "export_candidatures_poste",
            "format": FORMAT,
            "concours_id": poste.concours_id,
            "filtres": {
                "statuts": flt.statuts,
                "verdicts": flt.verdicts,
                "anomalies": flt.anomalies,
                "tri": flt.tri_param,
                # Jamais le terme lui-même : il peut contenir un nom (règle 9 de KAN-14).
                "recherche_appliquee": bool(flt.search_tokens),
            },
            "nb_lignes": total,
        },
    )
    return _fichier(contenu, xlsx.nom_fichier("poste", poste.pk))


@require_safe
@role_required_logged(
    *DIRECTION_ROLES, object_repr=lambda kw: f"Export des candidatures du concours #{kw.get('pk')}"
)
def export_concours(request, pk: int):
    """Onglet « Synthèse » puis un onglet par poste, sans filtre (règle 2)."""
    concours = get_object_or_404(Concours, pk=pk)
    total = xlsx.candidatures_concours(concours).count()
    refus = _au_dela_du_plafond(
        request, total, reverse("recruitment:concours_detail", args=[concours.pk])
    )
    if refus is not None:
        return refus
    contenu, nb_lignes, nb_postes = xlsx.classeur_concours(concours)
    log(
        AuditAction.EXPORT,
        obj=concours,
        request=request,
        metadata={
            "vue": "export_candidatures_concours",
            "format": FORMAT,
            "nb_lignes": nb_lignes,
            "nb_postes": nb_postes,
        },
    )
    return _fichier(contenu, xlsx.nom_fichier("concours", concours.pk))
