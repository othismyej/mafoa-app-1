"""Services d'évaluation : branche le moteur déterministe sur les modèles Django."""

from __future__ import annotations

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


@transaction.atomic
def run_eligibility(candidature: Candidature) -> VerificationEligibilite:
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
    deja_decidee = candidature.decisions.exists()
    if (
        not deja_decidee
        and candidature.statut in AUTO_CLASSABLE
        and candidature.statut != resultat.verdict.value
    ):
        before = candidature.statut
        candidature.statut = resultat.verdict.value
        candidature.save(update_fields=["statut", "updated_at"])
        log(
            AuditAction.UPDATE,
            obj=candidature,
            before={"statut": before},
            after={"statut": candidature.statut},
            actor_repr="moteur d'éligibilité",
            metadata={"verification_id": verification.pk, "moteur": resultat.version},
        )
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
) -> Decision:
    """Enregistre une décision humaine (spec KAN-13). Seul chemin d'enregistrement.

    Lève ``DecisionError`` (refus, rien n'est enregistré) ou ``ConfirmationRequise``
    (révision ou avertissement à confirmer). Décision, statut et audit sont écrits dans une
    seule transaction, sous verrou de la candidature.
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
        decisions.verifier_transition(c.statut, decision)
        motif = decisions.nettoyer_motif(motif)

        revision = decisions.est_revision(c.statut, decision, derniere is not None)
        alertes = decisions.avertissements(c, decision)
        if (revision and not confirmer_revision) or (alertes and not confirmer_avertissements):
            raise decisions.ConfirmationRequise(
                revision=revision,
                decision_remplacee_id=derniere.pk if derniere else None,
                avertissements=alertes,
            )

        avant = c.statut
        obj = Decision.objects.create(candidature=c, auteur=auteur, decision=decision, motif=motif)
        c.statut = decision
        c.save(update_fields=["statut", "updated_at"])
        metadata = {"decision_id": obj.pk, "motif": motif, "revision": revision}
        if revision:
            metadata["decision_remplacee_id"] = derniere.pk if derniere else None
        if alertes:
            metadata["avertissements_confirmes"] = alertes
        log(
            AuditAction.DECISION,
            actor=auteur,
            obj=c,
            before={"statut": avant},
            after={"statut": c.statut},
            request=request,
            metadata=metadata,
        )
    candidature.statut = c.statut
    return obj
