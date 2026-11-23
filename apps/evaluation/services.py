"""Services d'évaluation : branche le moteur déterministe sur les modèles Django."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import log
from apps.recruitment.models import Candidature

from . import decisions
from .decisions import DecisionError
from .eligibility.engine import CriteresPoste, Diplome, ProfilCandidat, evaluate
from .eligibility.experience import Periode
from .eligibility.levels import NiveauEtudes
from .models import Decision, VerificationEligibilite

# Statuts qui peuvent être mis à jour automatiquement par le moteur (classement, pas décision).
AUTO_CLASSABLE = {
    Candidature.Statut.RECUE,
    Candidature.Statut.EN_ANALYSE,
    Candidature.Statut.ELIGIBLE,
    Candidature.Statut.NON_ELIGIBLE,
    Candidature.Statut.A_VERIFIER,
}


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def profil_from_declared(data: dict) -> ProfilCandidat:
    """Convertit ``Candidature.donnees_declarees`` en profil pour le moteur."""
    diplomes = [
        Diplome(
            intitule=d.get("intitule", ""),
            niveau=d.get("niveau"),
            specialite=d.get("specialite", ""),
            annee_obtention=d.get("annee_obtention"),
        )
        for d in data.get("formation", [])
    ]
    raw_exp = data.get("experiences")
    experiences = (
        None
        if raw_exp is None
        else [
            Periode(
                debut=_parse_date(e.get("debut")),
                fin=_parse_date(e.get("fin")),
                employeur=e.get("employeur", ""),
                intitule=e.get("poste", ""),
            )
            for e in raw_exp
        ]
    )
    return ProfilCandidat(diplomes=diplomes, experiences=experiences)


def criteres_from_cible(cible) -> CriteresPoste:
    reference = cible.date_reference or timezone.localdate()
    return CriteresPoste(
        date_reference=reference,
        niveau_min=NiveauEtudes(cible.niveau_etudes_min)
        if cible.niveau_etudes_min is not None
        else None,
        specialites=list(cible.specialites_acceptees or []),
        experience_min=cible.experience_min_annees,
        experience_max=cible.experience_max_annees,
        autres_criteres=cible.autres_criteres or "",
    )


class CandidatureSpontaneeError(ValueError):
    """Le moteur d'éligibilité ne s'applique pas à une candidature spontanée (KAN-39, règle 20)."""


@dataclass(frozen=True)
class ContexteLot:
    """Appel du moteur depuis une relance en lot (KAN-15, règles 24 et 28).

    ``request`` peut être une vraie requête ou un substitut (tâche Celery) portant ``META``
    (adresse IP, navigateur) et ``user``.
    """

    lot_id: str
    acteur: object
    request: object = None


@transaction.atomic
def run_eligibility(
    candidature: Candidature, *, lot: ContexteLot | None = None
) -> VerificationEligibilite:
    """Calcule un verdict et reclasse la candidature si elle n'a aucune décision humaine.

    Hors lot : une entrée ``update`` « moteur d'éligibilité » n'est écrite que si le statut
    change. Depuis un lot (``lot``) : exactement une entrée ``update`` au nom de l'utilisateur
    qui a lancé le lot, que le statut change ou non (pas de double entrée).
    """
    if candidature.type == Candidature.Type.SPONTANEE or candidature.cible is None:
        # Rien n'est créé ni modifié : aucune vérification, aucun statut, aucun audit.
        raise CandidatureSpontaneeError(
            f"Candidature #{candidature.pk} : candidature spontanée, sans critères "
            "d'éligibilité (aucune vérification possible)."
        )
    resultat = evaluate(
        profil_from_declared(candidature.donnees_declarees or {}),
        criteres_from_cible(candidature.cible),
    )
    verification = VerificationEligibilite.objects.create(
        candidature=candidature,
        resultat=resultat.verdict.value,
        details=resultat.details(),
        justification=resultat.justification,
        sources=[{"type": "donnees_declarees"}],
        moteur_version=resultat.version,
    )
    # Verrou sur la ligne puis relecture : une décision humaine concurrente est soit déjà validée
    # (visible ici), soit bloquée jusqu'à la fin de cette transaction (règle 9).
    c = Candidature.objects.select_for_update().get(pk=candidature.pk)
    before = c.statut
    if (
        not c.decisions.exists()
        and c.statut in AUTO_CLASSABLE
        and c.statut != resultat.verdict.value
    ):
        c.statut = resultat.verdict.value
        c.save(update_fields=["statut", "updated_at"])
    statut_modifie = c.statut != before
    metadata = {"verification_id": verification.pk, "moteur": resultat.version}
    if lot is not None:
        acteur = lot.acteur
        log(
            AuditAction.UPDATE,
            actor=acteur,
            obj=c,
            object_repr=f"Candidature #{c.pk}",
            before={"statut": before},
            after={"statut": c.statut},
            request=lot.request,
            actor_repr=f"{acteur.email} via moteur d'éligibilité",
            metadata={
                **metadata,
                "lot_id": str(lot.lot_id),
                "operation": "relance_eligibilite",
                "verdict": resultat.verdict.value,
                "statut_modifie": statut_modifie,
            },
        )
    elif statut_modifie:
        log(
            AuditAction.UPDATE,
            obj=c,
            object_repr=f"Candidature #{c.pk}",
            before={"statut": before},
            after={"statut": c.statut},
            actor_repr="moteur d'éligibilité",
            metadata=metadata,
        )
    candidature.statut = c.statut
    verification.statut_modifie = statut_modifie
    return verification


_INCONNUE = object()


def record_decision(
    candidature: Candidature,
    auteur,
    decision: str,
    motif: str,
    request=None,
    *,
    statut_attendu: str | None = None,
    derniere_decision_attendue=_INCONNUE,
    confirmer_revision: bool = False,
    confirmer_avertissements: bool = False,
    lot_id=None,
    avertissements_lot: tuple[str, ...] | list[str] = (),
) -> Decision:
    """Enregistre une décision humaine (spec KAN-13). Seul chemin d'enregistrement.

    Lève ``DecisionError`` (refus, rien n'est enregistré) ou ``ConfirmationRequise``
    (révision ou avertissement à confirmer). Décision, statut et audit sont écrits dans une
    seule transaction, sous verrou de la candidature.

    ``lot_id`` (KAN-15) : décision prise en lot. Seules les décisions et transitions de
    ``decisions.TRANSITIONS_LOT`` sont alors acceptées, jamais une révision ; l'identifiant est
    inscrit sur la décision et dans l'audit (``mode = "lot"``). ``avertissements_lot`` : les
    avertissements propres au lot confirmés par l'utilisateur (ex. verdict non favorable).
    """
    if not getattr(auteur, "is_authenticated", False):
        raise DecisionError(
            _("Une décision est toujours prise par un utilisateur identifié."), "auteur_absent"
        )
    with transaction.atomic():
        c = (
            Candidature.objects.select_for_update(of=("self",))
            .select_related("poste_concours__concours", "offre")
            .get(pk=candidature.pk)
        )
        derniere = c.decisions.order_by("-created_at", "-id").first()
        # Contrôle de concurrence (règle 7).
        if (statut_attendu is not None and c.statut != statut_attendu) or (
            derniere_decision_attendue is not _INCONNUE
            and (derniere.pk if derniere else None) != derniere_decision_attendue
        ):
            raise DecisionError(
                _("Une autre décision a été enregistrée entre-temps. La fiche a été actualisée."),
                "concurrence",
            )
        if decisions.concours_termine(c):
            raise DecisionError(
                _("Le concours est terminé : aucune décision n'est possible."), "concours_termine"
            )
        decisions.verifier_transition(c.statut, decision, c.type)
        if lot_id is not None:
            decisions.verifier_transition_lot(c.statut, decision, derniere is not None)
        motif = decisions.nettoyer_motif(motif)

        revision = decisions.est_revision(c.statut, decision, derniere is not None, c.type)
        alertes = decisions.avertissements(c, decision)
        if (revision and not confirmer_revision) or (alertes and not confirmer_avertissements):
            raise decisions.ConfirmationRequise(
                revision=revision,
                decision_remplacee_id=derniere.pk if derniere else None,
                avertissements=alertes,
            )

        avant = c.statut
        obj = Decision.objects.create(
            candidature=c, auteur=auteur, decision=decision, motif=motif, lot_id=lot_id
        )
        c.statut = decision
        c.save(update_fields=["statut", "updated_at"])
        metadata = {"decision_id": obj.pk, "motif": motif, "revision": revision}
        if revision:
            metadata["decision_remplacee_id"] = derniere.pk if derniere else None
        confirmes = alertes + [a for a in avertissements_lot if a not in alertes]
        if confirmes:
            metadata["avertissements_confirmes"] = confirmes
        if lot_id is not None:
            metadata["lot_id"] = str(lot_id)
            metadata["mode"] = "lot"
        if c.type == Candidature.Type.SPONTANEE:
            metadata["type_candidature"] = c.type  # KAN-39, règle 39
            metadata["consentement_vivier"] = decisions.etat_consentement(c)  # règle 30 bis
        log(
            AuditAction.DECISION,
            actor=auteur,
            obj=c,
            object_repr=f"Candidature #{c.pk}",
            before={"statut": avant},
            after={"statut": c.statut},
            request=request,
            metadata=metadata,
        )
    candidature.statut = c.statut
    return obj
