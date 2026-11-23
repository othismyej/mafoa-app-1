from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST
from django.views.defaults import permission_denied

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


HISTORIQUE_VERIFICATIONS = 20


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
            "decisions",
            queryset=Decision.objects.select_related("auteur").order_by("-created_at", "-id"),
        ),
    )


def _fiche_context(candidature: Candidature, form: dict | None = None) -> dict:
    # Historique borné : la dernière vérification + les 20 précédentes (la page ne grossit pas
    # indéfiniment avec les réévaluations), et le nombre total pour information.
    verifs_qs = VerificationEligibilite.objects.filter(candidature=candidature).order_by(
        "-created_at", "-id"
    )
    verifications = list(verifs_qs[: HISTORIQUE_VERIFICATIONS + 1])
    total_verifications = (
        verifs_qs.count() if len(verifications) > HISTORIQUE_VERIFICATIONS else len(verifications)
    )
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
        "verifications_masquees": max(0, total_verifications - len(verifications)),
        "sources": presenters.source_labels(derniere.sources) if derniere else [],
        "criteres": presenters.criteres(derniere.details) if derniere else [],
        "ecart": presenters.ecart_verdict_decision(
            candidature.statut,
            derniere.resultat if derniere else None,
            bool(decisions_list),
            decisions_list[0].decision if decisions_list else None,
        ),
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
    # Libellé sans nom : le journal, en ajout seul, ne doit pas multiplier les données nominatives.
    log(
        AuditAction.VIEW,
        obj=candidature,
        request=request,
        object_repr=f"Candidature #{candidature.pk}",
    )
    return render(request, "recruitment/candidature_detail.html", _fiche_context(candidature))


def _journaliser_refus(request, candidature_pk: int, refus: str, **metadata) -> None:
    """Trace un refus anormal (Q6) : sans nom ni motif saisi."""
    log(
        AuditAction.UPDATE,
        object_repr=f"Candidature #{candidature_pk}",
        request=request,
        metadata={"refus": refus, "candidature_id": candidature_pk, **metadata},
    )


@require_POST
def decision_enregistrer(request, pk: int):
    """Enregistre une décision humaine depuis la fiche (spec KAN-13)."""
    if not (request.user.is_authenticated and request.user.has_role(*DIRECTION_ROLES)):
        # Réponse 403 renvoyée (et non levée) pour que la trace survive à ATOMIC_REQUESTS.
        _journaliser_refus(request, pk, "droits")
        return permission_denied(request, PermissionDenied())
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
            derniere_decision_attendue=(
                int(derniere_raw) if derniere_raw.isascii() and derniere_raw.isdigit() else None
            ),
            confirmer_revision=data.get("confirmer_revision") == "1",
            confirmer_avertissements=data.get("confirmer_avertissements") == "1",
        )
    except ConfirmationRequise as exc:
        form["confirmation"] = {
            "revision": exc.revision,
            "avertissements": [
                _(decision_rules.AVERTISSEMENT_LIBELLES[a]) for a in exc.avertissements
            ],
            "derniere_date": candidature.decisions.order_by("-created_at", "-id")
            .values_list("created_at", flat=True)
            .first(),
        }
    except DecisionError as exc:
        if exc.code in decision_rules.CODES_REFUS_JOURNALISES:
            _journaliser_refus(
                request,
                pk,
                exc.code,
                statut=candidature.statut,
                decision=form["decision"][:50],
            )
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
    if htmx:
        return render(request, "recruitment/_decisions.html", context, status=status)
    # Sans JavaScript, la fiche complète est réaffichée : c'est une consultation (§5).
    log(AuditAction.VIEW, obj=candidature, request=request, object_repr=f"Candidature #{pk}")
    return render(request, "recruitment/candidature_detail.html", context, status=status)
