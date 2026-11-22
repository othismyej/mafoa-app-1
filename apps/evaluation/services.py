"""Services d'évaluation : branche le moteur déterministe sur les modèles Django."""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log, snapshot
from apps.recruitment.models import Candidature

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
    if candidature.statut in AUTO_CLASSABLE and candidature.statut != resultat.verdict.value:
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


@transaction.atomic
def record_decision(candidature: Candidature, auteur, decision: str, motif: str, request=None):
    """Enregistre une décision humaine et met à jour le statut de la candidature."""
    if not getattr(auteur, "is_authenticated", False):
        raise ValueError("Une décision est toujours prise par un utilisateur identifié.")
    before = snapshot(candidature)
    obj = Decision.objects.create(
        candidature=candidature, auteur=auteur, decision=decision, motif=motif
    )
    candidature.statut = decision
    candidature.save(update_fields=["statut", "updated_at"])
    log(
        AuditAction.DECISION,
        actor=auteur,
        obj=candidature,
        before=before,
        after=snapshot(candidature),
        request=request,
        metadata={"decision_id": obj.pk, "motif": motif},
    )
    return obj
