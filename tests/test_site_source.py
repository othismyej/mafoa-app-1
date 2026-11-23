import hashlib
from collections import Counter

import pytest

from apps.audit.models import AuditAction, JournalAudit
from apps.recruitment.models import Candidat, Candidature, Concours, Piece, PosteConcours
from integrations.site_source.base import get_source
from integrations.site_source.fixture_source import FixtureSource
from integrations.site_source.sync import sync_from_source


def test_volumes_attendus():
    s = FixtureSource()
    assert len(s.list_concours()) == 2
    assert len(s.list_postes()) == 10
    assert len(s.list_offres()) == 5
    # 50 candidats + 2 doublons, une candidature de concours ou d'offre chacun ; plus 4
    # nouveaux candidats et 6 candidatures spontanées (KAN-39, comptes par type).
    assert len(s.list_candidats()) == 56
    par_type = Counter(a.type for a in s.list_candidatures())
    assert par_type["concours"] + par_type["offre"] == 52
    assert par_type["spontanee"] == 6


def test_generation_deterministe():
    a, b = FixtureSource(seed=7), FixtureSource(seed=7)
    assert a.list_candidats() == b.list_candidats()
    key = a.list_pieces()[0].storage_key
    assert a.read_piece(key) == b.read_piece(key)
    assert FixtureSource(seed=8).list_candidats() != a.list_candidats()


def test_aucune_donnee_reelle():
    s = FixtureSource()
    for c in s.list_candidats():
        assert c.email.lower().endswith(("@example.com", "@example.org"))
        assert c.cin.startswith("ZZ")
    assert all("Fictive" in o.nom or "Fictif" in o.nom for o in s.list_organisations())


def test_pieces_sont_des_pdf_lisibles():
    from io import BytesIO

    from pypdf import PdfReader

    s = FixtureSource()
    types = Counter(p.type for p in s.list_pieces())
    assert {"cv", "diplome", "attestation_travail", "lettre"} <= set(types)
    piece = next(p for p in s.list_pieces() if p.type == "diplome")
    content = s.read_piece(piece.storage_key)
    assert content.startswith(b"%PDF")
    assert "DIPLÔME" in PdfReader(BytesIO(content)).pages[0].extract_text()


def test_cas_pieges_presents():
    s = FixtureSource()
    candidatures = s.list_candidatures()
    pieces = s.list_pieces()
    sans_diplome = {a.source_id for a in candidatures} - {
        p.candidature_source_id for p in pieces if p.type == "diplome"
    }
    assert sans_diplome, "au moins un dossier sans diplôme"
    cv_hashes = Counter(
        hashlib.sha256(s.read_piece(p.storage_key)).hexdigest() for p in pieces if p.type == "cv"
    )
    assert max(cv_hashes.values()) >= 2, "au moins un CV identique partagé (doublon)"


def test_get_source_inconnue():
    with pytest.raises(ValueError):
        get_source("ftp")


def test_sources_non_disponibles():
    from integrations.site_source.api_source import ApiSource

    with pytest.raises(NotImplementedError):
        ApiSource().list_concours()


@pytest.mark.django_db
def test_sync_import_et_idempotence():
    source = FixtureSource(seed=42)
    report = sync_from_source(source)
    assert report.created["Concours"] == 2 and report.created["PosteConcours"] == 10
    assert Piece.objects.exclude(sha256="").count() == Piece.objects.count()
    assert all(p.source_synced_at for p in PosteConcours.objects.all())
    counts = (Candidat.objects.count(), Candidature.objects.count(), Piece.objects.count())

    report2 = sync_from_source(source)
    assert not report2.created
    assert (Candidat.objects.count(), Candidature.objects.count(), Piece.objects.count()) == counts
    assert JournalAudit.objects.filter(action=AuditAction.SYNC).count() == 2


@pytest.mark.django_db
def test_doublons_detectables_par_les_cles(synced):
    from django.db.models import Count

    dup_phone = (
        Candidat.objects.values("telephone_normalise").annotate(n=Count("id")).filter(n__gt=1)
    )
    dup_identity = (
        Candidat.objects.exclude(cle_identite="")
        .values("cle_identite")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    dup_email = Candidat.objects.values("email_normalise").annotate(n=Count("id")).filter(n__gt=1)
    assert dup_phone.exists() and dup_identity.exists() and dup_email.exists()


@pytest.mark.django_db
def test_criteres_structures_importes(synced):
    poste = PosteConcours.objects.get(reference="N-03")
    assert poste.niveau_etudes_min == 5
    assert poste.specialites_acceptees == ["Génie électrique"]
    assert poste.experience_max_annees == 10
    assert Concours.objects.get(source_id="C-NORD-2026").nombre_postes_total == 1 + 1 + 3 + 1 + 4
