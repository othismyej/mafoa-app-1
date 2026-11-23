"""Mise en forme du récapitulatif et du rapport d'une action en lot (spec KAN-15).

Fonctions pures de la charge utile figée du lot (jeton) et d'un dictionnaire
``identifiant → « NOM Prénom »`` : seule donnée personnelle affichée (règle 36).
"""

from __future__ import annotations

from django.http import QueryDict
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from apps.evaluation import decisions as rules
from apps.evaluation import lots

from .filters import ANOMALIE_CHOICES, ANOMALIES_TOUTES, VERDICT_CHOICES, CandidatureFilter
from .models import Candidature

S = Candidature.Statut
NOMS_PAR_GROUPE = 100

ORDRE_NC = (lots.NC_CONCOURS_TERMINE, lots.NC_TRANSITION, lots.NC_DEJA, lots.NC_REVISION)
STATUT_RANG = {v: i for i, v in enumerate(S.values)}


def libelle_statut(code: str) -> str:
    return str(S(code).label) if code in S.values else code


def libelle_action(operation: str, decision: str) -> str:
    if operation == lots.OPERATION_RELANCE:
        return _("Relancer le calcul d’éligibilité")
    return _("Décision : %(d)s") % {"d": libelle_statut(decision)}


def libelle_non_concernee(raison: str, statut: str) -> str:
    if raison == lots.NC_TRANSITION:
        return _("Transition interdite depuis %(s)s") % {"s": libelle_statut(statut)}
    if raison == lots.NC_DEJA:
        return _("Déjà %(s)s") % {"s": libelle_statut(statut)}
    if raison == lots.NC_REVISION:
        return _("Révision : à faire depuis la fiche")
    return _("Concours terminé")


def libelle_echec(raison: str) -> str:
    return {
        lots.ECHEC_MODIFIEE: _("Modifiée entre-temps"),
        lots.ECHEC_TRANSITION: _("Transition devenue interdite"),
        lots.ECHEC_CONCOURS_TERMINE: _("Concours terminé entre-temps"),
        lots.ECHEC_AVERTISSEMENT: _("Nouvel avertissement non confirmé"),
        lots.ECHEC_TECHNIQUE: _("Erreur technique"),
    }.get(raison, raison)


AVERTISSEMENT_TITRES = {
    rules.AVERTISSEMENT_ANOMALIE: (
        gettext_noop("Anomalie de gravité haute"),
        gettext_noop("Ces dossiers ont au moins une anomalie de gravité haute non résolue."),
    ),
    rules.AVERTISSEMENT_VERDICT: (
        gettext_noop("Verdict du moteur non favorable"),
        rules.AVERTISSEMENT_LIBELLES[rules.AVERTISSEMENT_VERDICT],
    ),
}


def _noms(ids, noms: dict[int, str]) -> dict:
    tries = sorted(ids, key=lambda i: (noms.get(i, ""), i))
    return {
        "nb": len(tries),
        "noms": [(i, noms.get(i, f"#{i}")) for i in tries[:NOMS_PAR_GROUPE]],
        "autres": max(0, len(tries) - NOMS_PAR_GROUPE),
    }


def resume_filtres(payload: dict, recherche: str = "") -> list[str]:
    """Résumé lisible des filtres du mode « résultats du filtre » (écran uniquement).

    ``recherche`` vient du formulaire : le jeton ne porte pas le terme recherché (règle 29).
    """
    if payload["m"] != "filtre":
        return []
    params = QueryDict(payload["r"], mutable=True)
    if recherche:
        params["q"] = recherche
    flt = CandidatureFilter.from_querydict(params)
    resume = []
    if flt.statuts:
        resume.append(
            _("Statut : %(v)s") % {"v": ", ".join(libelle_statut(s) for s in flt.statuts)}
        )
    if flt.verdicts:
        labels = dict(VERDICT_CHOICES)
        resume.append(_("Verdict : %(v)s") % {"v": ", ".join(str(labels[v]) for v in flt.verdicts)})
    if flt.anomalies != ANOMALIES_TOUTES:
        resume.append(_("Anomalies : %(v)s") % {"v": dict(ANOMALIE_CHOICES)[flt.anomalies]})
    if flt.search_tokens:
        resume.append(_("Recherche : « %(v)s »") % {"v": flt.q})
    return resume or [_("Aucun filtre : toutes les candidatures du poste")]


def contexte_recapitulatif(
    payload: dict,
    noms: dict[int, str],
    *,
    jeton: str,
    form: dict | None = None,
    recherche: str = "",
) -> dict:
    form = form or {}
    operation, decision = payload["op"], payload["d"]
    groupes = sorted(
        payload["nc"],
        key=lambda g: (ORDRE_NC.index(g[0]), STATUT_RANG.get(g[1], len(STATUT_RANG))),
    )
    avertissements = []
    for type_ in lots.AVERTISSEMENTS_LOT:
        ids = payload["w"].get(type_)
        if not ids:
            continue
        titre, description = AVERTISSEMENT_TITRES[type_]
        avertissements.append(
            {
                "type": type_,
                "titre": _(titre),
                "description": _(description),
                "choix": (form.get("choix") or {}).get(type_, ""),
                **_noms(ids, noms),
            }
        )
    nb_concernees = len(payload["c"])
    return {
        "etat": "recap",
        "jeton": jeton,
        "operation": operation,
        "decision": decision,
        "action": operation if operation == lots.OPERATION_RELANCE else decision,
        "action_libelle": libelle_action(operation, decision),
        "decision_libelle": libelle_statut(decision) if decision else "",
        "nb_selectionnees": payload["n"],
        "mode": payload["m"],
        "filtres": resume_filtres(payload, recherche),
        "recherche": recherche,
        "repartition": [
            (libelle_statut(code), nb)
            for code, nb in sorted(
                payload["rep"].items(), key=lambda kv: STATUT_RANG.get(kv[0], len(STATUT_RANG))
            )
        ],
        "nb_concernees": nb_concernees,
        "nb_non_concernees": payload["n"] - nb_concernees,
        "groupes": [
            {"raison": raison, "libelle": libelle_non_concernee(raison, statut), **_noms(ids, noms)}
            for raison, statut, ids in groupes
        ],
        "avertissements": avertissements,
        "nb_deja_decidees": len(payload["dd"]),
        "concours_termine": payload["ct"],
        "executable": nb_concernees > 0 and not payload["ct"],
        "motif": form.get("motif", ""),
        "motif_max": rules.MOTIF_MAX,
        "confirmer": form.get("confirmer", False),
        "erreurs": form.get("erreurs", []),
        "lot_court": payload["lot"][:8],
    }


def contexte_rapport(payload: dict, rapport, noms: dict[int, str]) -> dict:
    return {
        "etat": "rapport",
        "operation": payload["op"],
        "action_libelle": libelle_action(payload["op"], payload["d"]),
        "nb_succes": len(rapport.succes),
        "nb_echecs": rapport.nb_echecs,
        "nb_statuts_modifies": len(rapport.statuts_modifies),
        "echecs": [
            {
                "raison": raison,
                "libelle": libelle_echec(raison),
                **_noms(rapport.echecs[raison], noms),
            }
            for raison in lots.ECHECS
            if rapport.echecs.get(raison)
        ],
        "exclues": _noms(rapport.exclues, noms) if rapport.exclues else None,
        "lot_court": payload["lot"][:8],
    }


def ids_rapport(rapport) -> list[int]:
    ids = list(rapport.exclues)
    for v in rapport.echecs.values():
        ids.extend(v)
    return ids
