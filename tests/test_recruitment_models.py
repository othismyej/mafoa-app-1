from datetime import UTC, datetime

import pytest
from django.db import IntegrityError, transaction

from apps.recruitment.models import Candidat, Candidature, PosteConcours

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_un_seul_poste_par_concours(synced):
    poste_a, poste_b = PosteConcours.objects.filter(concours__source_id="C-NORD-2026")[:2]
    cand = Candidat.objects.create(nom="Test", prenom="Unique")
    Candidature.objects.create(candidat=cand, poste_concours=poste_a, date_candidature=NOW)
    with pytest.raises(IntegrityError), transaction.atomic():
        Candidature.objects.create(candidat=cand, poste_concours=poste_b, date_candidature=NOW)


def test_postes_de_concours_differents_autorises(synced):
    nord = PosteConcours.objects.filter(concours__source_id="C-NORD-2026").first()
    sud = PosteConcours.objects.filter(concours__source_id="C-SUD-2026").first()
    cand = Candidat.objects.create(nom="Test", prenom="Deux")
    Candidature.objects.create(candidat=cand, poste_concours=nord, date_candidature=NOW)
    c2 = Candidature.objects.create(candidat=cand, poste_concours=sud, date_candidature=NOW)
    assert c2.concours == sud.concours


def test_candidature_vise_un_poste_ou_une_offre(synced):
    cand = Candidat.objects.create(nom="Test", prenom="Vide")
    with pytest.raises(IntegrityError), transaction.atomic():
        Candidature.objects.create(candidat=cand, date_candidature=NOW)
