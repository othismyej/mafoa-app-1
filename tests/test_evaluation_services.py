import pytest
from django.db import IntegrityError, transaction

from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.decisions import DecisionError
from apps.evaluation.models import Decision, VerificationEligibilite
from apps.evaluation.services import profil_from_declared, record_decision, run_eligibility
from apps.recruitment.models import Candidature

pytestmark = pytest.mark.django_db


def test_profil_depuis_donnees_declarees():
    p = profil_from_declared(
        {
            "formation": [{"intitule": "Master", "niveau": "Bac+5", "specialite": "Droit"}],
            "experiences": [{"debut": "2020-01-01", "fin": None, "employeur": "X", "poste": "Y"}],
        }
    )
    assert p.diplomes[0].specialite == "Droit"
    assert p.experiences[0].fin is None
    assert profil_from_declared({}).experiences is None
    assert (
        profil_from_declared({"experiences": [{"debut": "pas une date"}]}).experiences[0].debut
        is None
    )


def test_run_eligibility_persiste_et_classe(synced):
    cand = Candidature.objects.first()
    v = run_eligibility(cand)
    cand.refresh_from_db()
    assert VerificationEligibilite.objects.filter(candidature=cand).count() == 1
    assert cand.statut == v.resultat
    assert {d["critere"] for d in v.details} >= {"niveau_etudes", "specialite", "experience"}
    assert v.moteur_version


def test_moteur_ne_reclasse_pas_apres_decision_humaine(synced, direction_user):
    cand = Candidature.objects.first()
    Candidature.objects.filter(pk=cand.pk).update(statut=Candidature.Statut.ELIGIBLE)
    cand.refresh_from_db()
    record_decision(cand, direction_user, Candidature.Statut.CONVOQUEE, "Dossier complet")
    run_eligibility(cand)
    cand.refresh_from_db()
    assert cand.statut == Candidature.Statut.CONVOQUEE


def test_decision_humaine_tracee(synced, direction_user):
    cand = Candidature.objects.first()
    d = record_decision(cand, direction_user, Candidature.Statut.REJETEE, "Hors délai")
    cand.refresh_from_db()
    assert cand.statut == Candidature.Statut.REJETEE
    entry = JournalAudit.objects.get(action=AuditAction.DECISION)
    assert entry.actor == direction_user
    assert entry.after["statut"] == Candidature.Statut.REJETEE
    assert entry.metadata["motif"] == "Hors délai"
    # Une décision ne se modifie pas.
    d.motif = "autre"
    with pytest.raises(ValueError):
        d.save()


def test_decision_sans_motif_refusee(synced, direction_user):
    cand = Candidature.objects.first()
    Candidature.objects.filter(pk=cand.pk).update(statut=Candidature.Statut.CONVOQUEE)
    cand.refresh_from_db()
    with pytest.raises(DecisionError):
        record_decision(cand, direction_user, Candidature.Statut.RETENUE, "   ")
    assert not Decision.objects.filter(candidature=cand).exists()
    with pytest.raises(IntegrityError), transaction.atomic():
        Decision.objects.bulk_create(
            [Decision(candidature=cand, auteur=direction_user, decision="retenue", motif=" ")]
        )


def test_decision_exige_un_humain(synced):
    from django.contrib.auth.models import AnonymousUser

    with pytest.raises(DecisionError):
        record_decision(Candidature.objects.first(), AnonymousUser(), "rejetee", "motif suffisant")


def test_commande_sync_site(db, capsys):
    from django.core.management import call_command

    call_command("sync_site", "--evaluate")
    out = capsys.readouterr().out
    assert "Synchronisation « fixture » terminée" in out
    assert "Éligibilité calculée" in out
    assert not Candidature.objects.filter(statut=Candidature.Statut.RECUE).exists()
