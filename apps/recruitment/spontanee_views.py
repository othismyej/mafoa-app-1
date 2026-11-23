"""Écran « Candidatures spontanées » (spec KAN-39, règles 21 à 25 et 38).

Le refus de rôle est journalisé (``access_denied``) et la 403 renvoyée, de sorte que la trace
persiste malgré ``ATOMIC_REQUESTS``. Chaque réponse servie (page ou fragment HTMX) crée une
entrée ``view`` sans objet.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from apps.accounts.permissions import DIRECTION_ROLES
from apps.audit.services import log_consultation, role_required_logged

from . import spontanees as service
from .filters import ANOMALIE_CHOICES, PAGE_SIZES
from .models import Candidature

LISTE_REPR = "Liste : candidatures spontanées"

SORT_HEADERS = [
    ("candidat", _("Candidat")),
    ("date", _("Date de candidature")),
    ("fonction", _("Fonction souhaitée")),
    (None, _("Secteur souhaité")),
    (None, _("Lieu souhaité")),
    (None, _("Pièces")),
    ("statut", _("Statut")),
    ("anomalies", _("Anomalies")),
]


@require_GET
@role_required_logged(*DIRECTION_ROLES, object_repr=LISTE_REPR)
def spontanees_liste(request):
    secteurs, lieux = service.options_secteurs(), service.options_lieux()
    queryset, flt = service.filter_spontanees(request.GET, secteurs=secteurs, lieux=lieux)
    page = Paginator(queryset, flt.taille).get_page(flt.page)
    total = service.spontanees().count()
    context = {
        "flt": flt,
        "page": page,
        "total": total,
        "statut_choices": Candidature.statut_choices_spontanee(),
        "fonction_choices": service.fonction_choices(),
        "secteur_options": secteurs,
        "lieu_options": lieux,
        "pieces_choices": service.PIECES_CHOICES,
        "anomalie_choices": ANOMALIE_CHOICES,
        "page_sizes": PAGE_SIZES,
        "querystring": flt.querystring(include_tri=True),
        "querystring_sans_tri": flt.querystring(include_tri=False),
        "sort_headers": [
            {
                "key": key,
                "label": label,
                "param": flt.sort_param_for(key) if key else "",
                "active": key == flt.tri,
                "desc": flt.desc,
            }
            for key, label in SORT_HEADERS
        ],
    }
    # Journalisé avant le rendu (KAN-14, règle 6) : sans objet, sans nom de candidat.
    log_consultation(
        request,
        None,
        "spontanees_liste",
        object_repr=LISTE_REPR,
        **{**flt.as_metadata(), "page": page.number},
        nb_resultats=page.paginator.count,
    )
    fragment = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    template = (
        "recruitment/_spontanees_resultats.html"
        if fragment
        else "recruitment/spontanees_liste.html"
    )
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request"])
    return response
