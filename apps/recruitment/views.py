from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from apps.accounts.permissions import DIRECTION_ROLES, role_required
from apps.audit.models import AuditAction
from apps.audit.services import log

from .filters import ANOMALIE_CHOICES, PAGE_SIZES, VERDICT_CHOICES, filter_candidatures
from .models import Candidature, Concours, PosteConcours
from .selectors import concours_stats


@require_GET
@role_required(*DIRECTION_ROLES)
def concours_detail(request, pk: int):
    """Fiche d'un concours : postes et répartition des candidatures (spec KAN-9)."""
    concours = get_object_or_404(Concours.objects.select_related("organisation"), pk=pk)
    stats = concours_stats(concours)
    log(AuditAction.VIEW, obj=concours, request=request)
    return render(request, "recruitment/concours_detail.html", {"stats": stats})


@require_GET
@role_required(*DIRECTION_ROLES)
def poste_candidatures(request, concours_pk: int, poste_pk: int):
    """Liste des candidatures d'un poste : filtres, recherche, tri, pagination (spec KAN-10)."""
    poste = get_object_or_404(
        PosteConcours.objects.select_related("concours"), pk=poste_pk, concours_id=concours_pk
    )
    queryset, flt = filter_candidatures(poste, request.GET)
    page = Paginator(queryset, flt.taille).get_page(flt.page)
    total = poste.candidatures.count()
    context = {
        "poste": poste,
        "concours": poste.concours,
        "flt": flt,
        "page": page,
        "total": total,
        "statut_choices": Candidature.Statut.choices,
        "verdict_choices": VERDICT_CHOICES,
        "anomalie_choices": ANOMALIE_CHOICES,
        "page_sizes": PAGE_SIZES,
        "querystring": _querystring(request.GET),
        "querystring_sans_tri": _querystring(request.GET, drop=("page", "tri")),
        "sort_headers": [
            {
                "key": key,
                "label": label,
                "param": flt.sort_param_for(key),
                "active": key == flt.tri,
                "desc": flt.desc,
            }
            for key, label in SORT_HEADERS
        ],
    }
    log(
        AuditAction.VIEW,
        obj=poste,
        request=request,
        metadata={**flt.as_metadata(), "page": page.number, "nb_resultats": page.paginator.count},
    )
    template = (
        "recruitment/_candidatures_resultats.html"
        if request.headers.get("HX-Request") == "true"
        else "recruitment/poste_candidatures.html"
    )
    return render(request, template, context)


SORT_HEADERS = [
    ("candidat", _("Candidat")),
    ("date", _("Date de candidature")),
    ("statut", _("Statut")),
    ("verdict", _("Verdict d’éligibilité")),
    ("anomalies", _("Anomalies")),
]


def _querystring(params, drop=("page",)) -> str:
    """Paramètres courants (sans ``page``) pour construire les liens de pagination et de tri."""
    q = params.copy()
    for key in drop:
        q.pop(key, None)
    return q.urlencode()
