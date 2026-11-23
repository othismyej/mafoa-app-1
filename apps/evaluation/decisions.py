"""Règles des décisions humaines (spec KAN-13).

Toutes les règles sont appliquées ici, dans le service, et pas seulement dans l'interface :
valeurs permises, table des transitions, motif, révision, concours terminé, avertissements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from apps.recruitment.models import Candidature, Concours

S = Candidature.Statut

DECISIONS = (S.ELIGIBLE, S.NON_ELIGIBLE, S.PRESELECTIONNEE, S.CONVOQUEE, S.RETENUE, S.REJETEE)

# Statut courant → décisions permises (règle 4).
TRANSITIONS: dict[str, tuple[str, ...]] = {
    S.RECUE: (S.ELIGIBLE, S.NON_ELIGIBLE, S.REJETEE),
    S.EN_ANALYSE: (S.ELIGIBLE, S.NON_ELIGIBLE, S.REJETEE),
    S.A_VERIFIER: (S.ELIGIBLE, S.NON_ELIGIBLE, S.REJETEE),
    S.ELIGIBLE: (S.NON_ELIGIBLE, S.PRESELECTIONNEE, S.CONVOQUEE, S.REJETEE),
    S.NON_ELIGIBLE: (S.ELIGIBLE, S.REJETEE),
    S.PRESELECTIONNEE: (S.CONVOQUEE, S.REJETEE, S.ELIGIBLE),
    S.CONVOQUEE: (S.RETENUE, S.REJETEE, S.PRESELECTIONNEE),
    S.RETENUE: (S.REJETEE, S.CONVOQUEE),
    S.REJETEE: (S.ELIGIBLE, S.NON_ELIGIBLE),
}

# Retours en arrière dans le parcours (règle 6) : confirmation explicite requise.
REVISIONS = {
    (S.PRESELECTIONNEE, S.ELIGIBLE),
    (S.CONVOQUEE, S.PRESELECTIONNEE),
    (S.RETENUE, S.CONVOQUEE),
    (S.RETENUE, S.REJETEE),
    (S.REJETEE, S.ELIGIBLE),
    (S.REJETEE, S.NON_ELIGIBLE),
}
REQUALIFICATIONS = {(S.ELIGIBLE, S.NON_ELIGIBLE), (S.NON_ELIGIBLE, S.ELIGIBLE)}

MOTIF_MIN, MOTIF_MAX = 10, 2000
# Refus journalisés (Q6) : signes d'un envoi direct, pas d'une erreur de saisie.
CODES_REFUS_JOURNALISES = {"transition_interdite", "valeur_interdite"}
AVERTISSEMENT_ANOMALIE = "anomalie_haute"
AVERTISSEMENT_QUOTA = "quota_atteint"


class DecisionError(Exception):
    """Décision refusée : rien n'est enregistré."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class ConfirmationRequise(Exception):
    """Décision possible, mais une confirmation explicite est nécessaire."""

    revision: bool = False
    decision_remplacee_id: int | None = None
    avertissements: list[str] = field(default_factory=list)


def decisions_permises(statut: str) -> tuple[str, ...]:
    return TRANSITIONS.get(statut, ())


def concours_termine(candidature: Candidature) -> bool:
    poste = candidature.poste_concours
    return bool(poste and poste.concours.statut == Concours.Statut.TERMINE)


def est_revision(statut: str, decision: str, a_deja_une_decision: bool) -> bool:
    if (statut, decision) in REVISIONS:
        return True
    return a_deja_une_decision and (statut, decision) in REQUALIFICATIONS


def nettoyer_motif(motif: str | None) -> str:
    motif = (motif or "").strip()
    if len(motif) < MOTIF_MIN:
        raise DecisionError(_("Le motif est obligatoire (10 caractères minimum)."), "motif_court")
    if len(motif) > MOTIF_MAX:
        raise DecisionError(
            _("Le motif ne peut pas dépasser 2 000 caractères (%(n)d saisis).") % {"n": len(motif)},
            "motif_long",
        )
    return motif


def verifier_transition(statut: str, decision: str) -> None:
    if decision not in DECISIONS:
        raise DecisionError(
            _("« %(d)s » n'est pas une décision possible.") % {"d": decision}, "valeur_interdite"
        )
    if decision == statut:
        raise DecisionError(
            _("La candidature est déjà %(s)s.") % {"s": S(decision).label}, "deja_dans_ce_statut"
        )
    if decision not in decisions_permises(statut):
        raise DecisionError(
            _("Transition interdite : %(a)s → %(b)s.")
            % {"a": S(statut).label if statut in S.values else statut, "b": S(decision).label},
            "transition_interdite",
        )


def avertissements(candidature: Candidature, decision: str) -> list[str]:
    """Avertissements non bloquants (règle 10) : confirmation requise."""
    result = []
    if (
        decision in (S.PRESELECTIONNEE, S.CONVOQUEE, S.RETENUE)
        and candidature.anomalies.filter(resolue=False, gravite="haute").exists()
    ):
        result.append(AVERTISSEMENT_ANOMALIE)
    if decision == S.RETENUE:
        cible = candidature.poste_concours or candidature.offre
        filtre = {"poste_concours": cible} if candidature.poste_concours else {"offre": cible}
        retenues = (
            Candidature.objects.filter(statut=S.RETENUE, **filtre)
            .exclude(pk=candidature.pk)
            .count()
        )
        if cible is not None and retenues >= cible.nombre_postes:
            result.append(AVERTISSEMENT_QUOTA)
    # Le verrou ne porte que sur la candidature : deux décisions « retenue » simultanées sur le
    # même poste peuvent passer ce contrôle. Acceptable, ce n'est qu'un avertissement (Q7).
    return result


# --------------------------------------------------------------------------------------
# Décisions en lot (spec KAN-15, règles 2 à 4) : sous-ensemble fermé de la table ci-dessus,
# décisions d'avancement uniquement, révisions exclues. Appliqué dans ``record_decision``.
# --------------------------------------------------------------------------------------

DECISIONS_LOT = (S.ELIGIBLE, S.PRESELECTIONNEE, S.CONVOQUEE)
# Décision en lot → statuts courants acceptés.
TRANSITIONS_LOT: dict[str, tuple[str, ...]] = {
    S.ELIGIBLE: (S.RECUE, S.EN_ANALYSE, S.A_VERIFIER, S.NON_ELIGIBLE),
    S.PRESELECTIONNEE: (S.ELIGIBLE,),
    S.CONVOQUEE: (S.ELIGIBLE, S.PRESELECTIONNEE),
}
# Avertissement propre aux lots (règle 14) : « éligible » sur un verdict moteur non favorable.
AVERTISSEMENT_VERDICT = "verdict_non_favorable"


def transition_lot_permise(statut: str, decision: str, a_deja_une_decision: bool) -> bool:
    """Vrai si la transition peut être traitée en lot (jamais une révision, règle 4)."""
    return statut in TRANSITIONS_LOT.get(decision, ()) and not est_revision(
        statut, decision, a_deja_une_decision
    )


def verifier_transition_lot(statut: str, decision: str, a_deja_une_decision: bool) -> None:
    if decision not in DECISIONS_LOT:
        raise DecisionError(
            _("« %(d)s » ne peut pas être décidé en lot.") % {"d": decision},
            "valeur_interdite_en_lot",
        )
    if not transition_lot_permise(statut, decision, a_deja_une_decision):
        raise DecisionError(
            _("Cette transition ne peut pas être traitée en lot : à faire depuis la fiche."),
            "transition_interdite_en_lot",
        )


AVERTISSEMENT_LIBELLES = {
    AVERTISSEMENT_ANOMALIE: gettext_noop(
        "Ce dossier a au moins une anomalie de gravité haute non résolue."
    ),
    AVERTISSEMENT_QUOTA: gettext_noop(
        "Le nombre de postes à pourvoir est déjà atteint pour cette cible."
    ),
    AVERTISSEMENT_VERDICT: gettext_noop(
        "Ces dossiers n’ont pas été jugés éligibles par le moteur. En les déclarant éligibles "
        "en lot, vous attestez les avoir examinés."
    ),
}
