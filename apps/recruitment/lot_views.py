"""Actions en lot sur la liste des candidatures d'un poste (spec KAN-15).

Deux points d'entrée ``POST`` : le récapitulatif (aucune écriture métier) et l'exécution.
Réponse HTMX : fragment inséré dans ``#lot-panneau`` ; sans JavaScript : page complète.
Les refus sont **renvoyés** (jamais levés) pour que leur trace persiste.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import QueryDict
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST
from django.views.defaults import permission_denied

from apps.accounts.permissions import DIRECTION_ROLES
from apps.audit.models import AuditAction
from apps.audit.services import log, log_consultation
from apps.evaluation import decisions as rules
from apps.evaluation import lots
from apps.evaluation.decisions import DecisionError

from . import lot_presenters as presenters
from .filters import CandidatureFilter, filter_candidatures
from .models import Candidature, Concours, PosteConcours

MESSAGE_JETON = gettext_lazy(
    "Le récapitulatif a expiré ou n’est plus valide. Recommencez la sélection."
)


def _journaliser_refus(request, poste_pk: int, refus: str, etape: str, **metadata) -> None:
    """Refus anormal (règle 30) : identifiant du poste, sans nom ni motif saisi."""
    log(
        AuditAction.UPDATE,
        object_repr=f"Poste de concours #{poste_pk}",
        request=request,
        metadata={"refus": refus, "poste_id": poste_pk, "etape": etape, **metadata},
    )


def _autorise(request) -> bool:
    return request.user.is_authenticated and request.user.has_role(*DIRECTION_ROLES)


def _poste(concours_pk: int, poste_pk: int) -> PosteConcours:
    return get_object_or_404(
        PosteConcours.objects.select_related("concours"), pk=poste_pk, concours_id=concours_pk
    )


def _url_liste(poste: PosteConcours, querystring: str = "") -> str:
    url = reverse("recruitment:poste_candidatures", args=[poste.concours_id, poste.pk])
    return f"{url}?{querystring}" if querystring else url


def _rendre(request, poste, context: dict, status: int = 200, *, rafraichir: bool = False):
    htmx = request.headers.get("HX-Request") == "true"
    context = {"poste": poste, "concours": poste.concours, **context}
    template = "recruitment/_lot_panneau.html" if htmx else "recruitment/lot_page.html"
    response = render(request, template, context, status=status)
    patch_vary_headers(response, ["HX-Request"])
    if htmx and rafraichir:
        # La liste (#resultats) se recharge avec les filtres courants (règle 33).
        response["HX-Trigger"] = "lot-execute"
    return response


def _erreur(request, poste, message: str, status: int = 400, *, retour: str = ""):
    return _rendre(
        request,
        poste,
        {"etat": "erreur", "message": message, "retour_url": _url_liste(poste, retour)},
        status,
    )


def _lire_action(valeur: str) -> tuple[str, str]:
    """``action`` du formulaire → (opération, décision). Liste fermée (règles 1 à 3)."""
    if valeur == lots.OPERATION_RELANCE:
        return lots.OPERATION_RELANCE, ""
    if valeur in rules.DECISIONS_LOT:
        return lots.OPERATION_DECISION, valeur
    if valeur in Candidature.Statut.values:
        raise lots.LotError(
            "valeur_interdite_en_lot",
            _(
                "La décision « %(d)s » ne peut pas être prise en lot : elle se prend dossier "
                "par dossier, depuis la fiche."
            )
            % {"d": Candidature.Statut(valeur).label},
        )
    if not valeur:
        raise lots.LotError("action_absente", _("Choisissez une action."))
    raise lots.LotError("action_inconnue", _("Action inconnue."))


def _noms(ids) -> dict[int, str]:
    return {
        c.pk: str(c.candidat)
        for c in Candidature.objects.filter(pk__in=list(ids))
        .select_related("candidat")
        .only("id", "candidat__nom", "candidat__prenom")
    }


PREFIXE_FILTRE = "lot_"


def _params_filtre(data) -> QueryDict:
    """Paramètres de filtre de la barre (préfixés pour ne pas se confondre avec ``#filtres``)."""
    params = QueryDict(mutable=True)
    for key in data:
        if key.startswith(PREFIXE_FILTRE):
            params.setlist(key[len(PREFIXE_FILTRE) :], data.getlist(key))
    return params


def _refus_action(request, poste_pk, exc: lots.LotError, etape: str, valeur: str):
    if exc.code == "valeur_interdite_en_lot":
        _journaliser_refus(request, poste_pk, exc.code, etape, decision=valeur[:50])


@require_POST
def lot_recapitulatif(request, concours_pk: int, poste_pk: int):
    """Récapitulatif d'une action en lot (règles 5 à 16) : aucune écriture métier."""
    if not _autorise(request):
        _journaliser_refus(request, poste_pk, "droits", "recapitulatif")
        return permission_denied(request, PermissionDenied())
    poste = _poste(concours_pk, poste_pk)
    data = request.POST
    flt = CandidatureFilter.from_querydict(_params_filtre(data))
    retour = flt.querystring(include_tri=True)
    valeur = data.get("action", "")
    try:
        operation, decision = _lire_action(valeur)
    except lots.LotError as exc:
        _refus_action(request, poste.pk, exc, "recapitulatif", valeur)
        return _erreur(request, poste, exc.message, retour=retour)
    mode = data.get("mode", "")
    if mode not in lots.MODES:
        return _erreur(request, poste, _("Mode de sélection inconnu."), retour=retour)

    queryset, flt_lot = filter_candidatures(poste, flt if mode == "filtre" else CandidatureFilter())
    if mode == "ids":
        bruts = data.getlist("ids")
        if any(not (b.isascii() and b.isdigit()) for b in bruts):
            _journaliser_refus(request, poste.pk, "ids_hors_poste", "recapitulatif")
            return _erreur(request, poste, _("Sélection invalide."), retour=retour)
        ids = list(dict.fromkeys(int(b) for b in bruts))
        if ids and Candidature.objects.filter(poste_concours=poste, pk__in=ids).count() != len(ids):
            _journaliser_refus(request, poste.pk, "ids_hors_poste", "recapitulatif")
            return _erreur(request, poste, _("Sélection invalide."), retour=retour)
        queryset = queryset.filter(pk__in=ids)
        nombre = len(ids)
    else:
        nombre = queryset.count()
    if nombre == 0:
        return _erreur(request, poste, _("Sélectionnez au moins une candidature."), retour=retour)
    if nombre > lots.taille_max():
        return _erreur(
            request,
            poste,
            _(
                "La sélection compte %(n)d candidatures, au-delà du plafond de %(max)d par lot. "
                "Affinez les filtres."
            )
            % {"n": nombre, "max": lots.taille_max()},
            retour=retour,
        )

    candidatures = list(lots.annoter_pour_lot(queryset))
    payload = lots.preparer_lot(
        poste=poste,
        user=request.user,
        operation=operation,
        decision=decision,
        mode=mode,
        flt=flt,
        candidatures=candidatures,
    )
    # Liste nominative affichée : c'est une consultation (règle 31).
    log_consultation(
        request,
        poste,
        "lot_recapitulatif",
        lot_id=payload["lot"],
        operation=operation,
        nb_selectionnees=payload["n"],
    )
    noms = {c.pk: str(c.candidat) for c in candidatures}
    context = presenters.contexte_recapitulatif(payload, noms, jeton=lots.signer_jeton(payload))
    context["retour_url"] = _url_liste(poste, payload["r"])
    return _rendre(request, poste, context)


def _reafficher(request, poste, payload: dict, jeton: str, form: dict):
    """Récapitulatif réaffiché avec les messages et le motif saisi (règle 17)."""
    log_consultation(
        request,
        poste,
        "lot_recapitulatif",
        lot_id=payload["lot"],
        operation=payload["op"],
        nb_selectionnees=payload["n"],
        reaffichage=True,
    )
    context = presenters.contexte_recapitulatif(
        payload, _noms(lots.ids_selection(payload)), jeton=jeton, form=form
    )
    context["retour_url"] = _url_liste(poste, payload["r"])
    return _rendre(request, poste, context, status=400)


@transaction.non_atomic_requests
@require_POST
def lot_executer(request, concours_pk: int, poste_pk: int):
    """Exécution d'un lot figé par son jeton (règles 17 à 30).

    Hors ``ATOMIC_REQUESTS`` : chaque candidature est validée dans sa propre transaction.
    """
    if not _autorise(request):
        _journaliser_refus(request, poste_pk, "droits", "execution")
        return permission_denied(request, PermissionDenied())
    poste = _poste(concours_pk, poste_pk)
    data = request.POST
    valeur = data.get("action", "")
    attendu = None
    if valeur:
        try:
            attendu = _lire_action(valeur)
        except lots.LotError as exc:
            _refus_action(request, poste.pk, exc, "execution", valeur)
            return _erreur(request, poste, exc.message)
    jeton = data.get("jeton", "")
    try:
        payload = lots.lire_jeton(jeton, user=request.user, poste=poste)
    except lots.JetonInvalide as exc:
        if exc.code == "jeton_invalide":
            _journaliser_refus(request, poste.pk, "jeton_invalide", "execution")
        return _erreur(request, poste, MESSAGE_JETON)
    if attendu is not None and attendu != (payload["op"], payload["d"]):
        _journaliser_refus(request, poste.pk, "jeton_invalide", "execution")
        return _erreur(request, poste, MESSAGE_JETON)
    retour = payload["r"]
    deja = _("Ce lot a déjà été exécuté.")
    if lots.lot_deja_journalise(payload["lot"]):
        return _erreur(request, poste, deja, 409, retour=retour)
    termine = Concours.objects.filter(pk=poste.concours_id, statut=Concours.Statut.TERMINE).exists()
    if payload["ct"] or termine:
        return _erreur(
            request,
            poste,
            _("Le concours est terminé : aucune action en lot n’est possible."),
            retour=retour,
        )
    if not payload["c"]:
        return _erreur(
            request, poste, _("Aucune candidature concernée : rien à exécuter."), retour=retour
        )

    try:
        if payload["op"] == lots.OPERATION_DECISION:
            return _executer_decisions(request, poste, payload, jeton)
        return _executer_relance(request, poste, payload)
    except lots.LotDejaExecute:
        return _erreur(request, poste, deja, 409, retour=retour)


def _executer_decisions(request, poste, payload: dict, jeton: str):
    data = request.POST
    choix = {t: data.get(f"choix_{t}", "") for t in payload["w"]}
    form = {
        "motif": data.get("motif", ""),
        "choix": choix,
        "confirmer": data.get("confirmer") == "1",
        "erreurs": [],
    }
    try:
        motif = rules.nettoyer_motif(form["motif"])
    except DecisionError as exc:
        form["erreurs"].append(exc.message)
    if any(v not in (lots.CHOIX_EXCLURE, lots.CHOIX_INCLURE) for v in choix.values()):
        form["erreurs"].append(_("Indiquez si les dossiers signalés sont exclus ou inclus."))
    if not form["confirmer"]:
        form["erreurs"].append(_("Cochez la case de confirmation pour enregistrer les décisions."))
    if form["erreurs"]:
        return _reafficher(request, poste, payload, jeton, form)
    rapport = lots.executer_decisions(
        payload, poste=poste, user=request.user, motif=motif, choix=choix, request=request
    )
    context = presenters.contexte_rapport(payload, rapport, _noms(presenters.ids_rapport(rapport)))
    context["retour_url"] = _url_liste(poste, payload["r"])
    return _rendre(request, poste, context, rafraichir=True)


def _executer_relance(request, poste, payload: dict):
    resultat = lots.executer_relance(payload, poste=poste, user=request.user, request=request)
    if resultat.asynchrone:
        context = {
            "etat": "lance",
            "nb": resultat.nb,
            "lot_court": payload["lot"][:8],
            "action_libelle": presenters.libelle_action(payload["op"], ""),
        }
    else:
        rapport = resultat.rapport
        context = presenters.contexte_rapport(
            payload, rapport, _noms(presenters.ids_rapport(rapport))
        )
    context["retour_url"] = _url_liste(poste, payload["r"])
    return _rendre(request, poste, context, rafraichir=True)
