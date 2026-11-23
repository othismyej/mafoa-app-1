"""Offres d'emploi (recrutement privé) et dossiers à vérifier (spec KAN-38).

Les refus de rôle sont journalisés (``access_denied``) et la 403 est renvoyée, de sorte que la
trace persiste malgré ``ATOMIC_REQUESTS`` (règle 29).
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.accounts.permissions import DIRECTION_ROLES
from apps.audit.models import AuditAction
from apps.audit.services import log, role_required_logged

from .models import OffreEmploi
from .selectors import dossiers_a_verifier, offres_avec_compteurs
from .views import _liste_candidatures

OFFRES_PAR_PAGE = 50


def _repr_offre(kwargs) -> str:
    return f"Offre d'emploi #{kwargs.get('pk')}"


@require_GET
@role_required_logged(*DIRECTION_ROLES, object_repr="Liste des offres d'emploi")
def offres_liste(request):
    """Liste des offres d'emploi, filtrable par statut (règles 4 à 10)."""
    valides = set(OffreEmploi.Statut.values)
    statuts = [s for s in dict.fromkeys(request.GET.getlist("statut")) if s in valides]
    queryset = offres_avec_compteurs()
    if statuts:
        queryset = queryset.filter(statut__in=statuts)
    page = Paginator(queryset, OFFRES_PAR_PAGE).get_page(request.GET.get("page") or "1")
    total = OffreEmploi.objects.count()
    log(
        AuditAction.VIEW,
        request=request,
        object_repr="Liste des offres d'emploi",
        metadata={
            "vue": "offres_liste",
            "statuts": statuts,
            "page": page.number,
            "nb_resultats": page.paginator.count,
        },
    )
    return render(
        request,
        "recruitment/offres_liste.html",
        {
            "page": page,
            "total": total,
            "statuts": statuts,
            "statut_choices": OffreEmploi.Statut.choices,
            "querystring": urlencode([("statut", s) for s in statuts]),
        },
    )


@require_GET
@role_required_logged(*DIRECTION_ROLES, object_repr=_repr_offre)
def offre_detail(request, pk: int):
    """Fiche de poste résumée et liste des candidatures d'une offre (règles 11 à 17)."""
    offre = get_object_or_404(OffreEmploi.objects.select_related("organisation"), pk=pk)
    return _liste_candidatures(
        request,
        offre,
        "recruitment/offre_detail.html",
        {"offre": offre, "lot_actif": False},
    )


@require_GET
@role_required_logged(*DIRECTION_ROLES, object_repr="Dossiers à vérifier")
def a_verifier(request):
    """Dossiers à vérifier, regroupés par poste de concours ou offre (règles 24 à 26)."""
    lignes = dossiers_a_verifier()
    total = sum(ligne.nombre for ligne in lignes)
    log(
        AuditAction.VIEW,
        request=request,
        object_repr="Dossiers à vérifier",
        metadata={"vue": "a_verifier", "nb_cibles": len(lignes), "nb_resultats": total},
    )
    return render(request, "recruitment/a_verifier.html", {"lignes": lignes, "total": total})
