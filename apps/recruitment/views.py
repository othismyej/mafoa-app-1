from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import DIRECTION_ROLES, role_required
from apps.audit.models import AuditAction
from apps.audit.services import log
from apps.evaluation import decisions as decision_rules
from apps.evaluation.decisions import ConfirmationRequise, DecisionError
from apps.evaluation.models import Anomalie, Decision, VerificationEligibilite
from apps.evaluation.services import record_decision

from . import presenters
from .filters import ANOMALIE_CHOICES, PAGE_SIZES, VERDICT_CHOICES, filter_candidatures
from .models import Candidature, Concours, Piece, PosteConcours
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
        "querystring": flt.querystring(include_tri=True),
        "querystring_sans_tri": flt.querystring(include_tri=False),
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
    # Fragment pour les requêtes HTMX, sauf restauration d'historique (page complète attendue).
    fragment = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    template = (
        "recruitment/_candidatures_resultats.html"
        if fragment
        else "recruitment/poste_candidatures.html"
    )
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request"])
    return response


SORT_HEADERS = [
    ("candidat", _("Candidat")),
    ("date", _("Date de candidature")),
    ("statut", _("Statut")),
    ("verdict", _("Verdict d’éligibilité")),
    ("anomalies", _("Anomalies")),
]


def _fiche_queryset():
    return Candidature.objects.select_related(
        "candidat",
        "poste_concours__concours__organisation",
        "offre__organisation",
    ).prefetch_related(
        Prefetch("pieces", queryset=Piece.objects.order_by("type", "id")),
        Prefetch(
            "anomalies",
            queryset=Anomalie.objects.select_related(
                "piece", "resolue_par", "candidature_liee__candidat"
            ).order_by("resolue", "-created_at", "-id"),
        ),
        Prefetch(
            "verifications_eligibilite",
            queryset=VerificationEligibilite.objects.order_by("-created_at", "-id"),
        ),
        Prefetch(
            "decisions",
            queryset=Decision.objects.select_related("auteur").order_by("-created_at", "-id"),
        ),
    )


def _fiche_context(candidature: Candidature, form: dict | None = None) -> dict:
    verifications = list(candidature.verifications_eligibilite.all())
    decisions_list = list(candidature.decisions.all())
    anomalies = list(candidature.anomalies.all())
    derniere = verifications[0] if verifications else None
    donnees = candidature.donnees_declarees or {}
    return {
        "c": candidature,
        "candidat": candidature.candidat,
        "pieces": list(candidature.pieces.all()),
        "pieces_en_anomalie": {a.piece_id for a in anomalies if a.piece_id and not a.resolue},
        "anomalies": anomalies,
        "nb_anomalies_non_resolues": sum(1 for a in anomalies if not a.resolue),
        "derniere_verification": derniere,
        "verifications_precedentes": verifications[1:],
        "sources": presenters.source_labels(derniere.sources) if derniere else [],
        "ecart": bool(decisions_list and derniere and derniere.resultat != candidature.statut),
        "decisions": decisions_list,
        "formations": presenters.formations(donnees),
        "experiences": presenters.experiences(donnees),
        "decision_form": _decision_form_context(candidature, decisions_list, form or {}),
    }


def _decision_form_context(candidature, decisions_list, form: dict) -> dict:
    statut = candidature.statut
    return {
        "disponible": not decision_rules.concours_termine(candidature),
        "choix": [
            (d, Candidature.Statut(d).label) for d in decision_rules.decisions_permises(statut)
        ],
        "statut_attendu": statut,
        "derniere_decision_id": decisions_list[0].pk if decisions_list else "",
        "decision": form.get("decision", ""),
        "motif": form.get("motif", ""),
        "erreur": form.get("erreur", ""),
        "confirmation": form.get("confirmation"),
        "succes": form.get("succes", ""),
        "motif_max": decision_rules.MOTIF_MAX,
    }


@require_GET
@role_required(*DIRECTION_ROLES)
def candidature_detail(request, pk: int):
    """Fiche candidature en lecture seule (spec KAN-11) ; formulaire de décision (KAN-13)."""
    candidature = get_object_or_404(_fiche_queryset(), pk=pk)
    # Point d'accroche unique de la journalisation des consultations (KAN-14).
    log(AuditAction.VIEW, obj=candidature, request=request)
    return render(request, "recruitment/candidature_detail.html", _fiche_context(candidature))


@require_POST
@role_required(*DIRECTION_ROLES)
def decision_enregistrer(request, pk: int):
    """Enregistre une décision humaine depuis la fiche (spec KAN-13)."""
    candidature = get_object_or_404(Candidature, pk=pk)
    htmx = request.headers.get("HX-Request") == "true"
    data = request.POST
    derniere_raw = data.get("derniere_decision_id", "")
    form = {"decision": data.get("decision", ""), "motif": data.get("motif", "")}
    status = 200
    try:
        record_decision(
            candidature,
            request.user,
            form["decision"],
            form["motif"],
            request,
            statut_attendu=data.get("statut_attendu") or None,
            derniere_decision_attendue=int(derniere_raw) if derniere_raw.isdigit() else None,
            confirmer_revision=data.get("confirmer_revision") == "1",
            confirmer_avertissements=data.get("confirmer_avertissements") == "1",
        )
    except ConfirmationRequise as exc:
        form["confirmation"] = {
            "revision": exc.revision,
            "avertissements": [
                decision_rules.AVERTISSEMENT_LIBELLES[a] for a in exc.avertissements
            ],
            "derniere_date": candidature.decisions.order_by("-created_at", "-id")
            .values_list("created_at", flat=True)
            .first(),
        }
    except DecisionError as exc:
        form["erreur"] = exc.message
        if exc.code == "concurrence":
            form = {"erreur": exc.message}  # formulaire réinitialisé sur l'état à jour
        status = 409 if exc.code == "concurrence" else 400
    else:
        if not htmx:
            messages.success(request, _("Décision enregistrée."))
            return redirect("recruitment:candidature_detail", pk=pk)
        form = {"succes": _("Décision enregistrée.")}

    candidature = _fiche_queryset().get(pk=pk)
    context = _fiche_context(candidature, form)
    template = "recruitment/_decisions.html" if htmx else "recruitment/candidature_detail.html"
    return render(request, template, context, status=status)
