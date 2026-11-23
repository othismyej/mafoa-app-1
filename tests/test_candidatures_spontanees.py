"""Tests d'acceptation de KAN-39 — Candidatures spontanées (vivier).

Les attentes sont fondées sur ``docs/specs/KAN-39-candidatures-spontanees.md`` (CA1 à CA63).
Toutes les données sont fictives (domaines example.com / example.org, CIN « ZZ »). Aucun appel IA.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import QueryDict
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from pypdf import PdfReader

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.decisions import ConfirmationRequise, DecisionError
from apps.evaluation.models import Anomalie, Decision, VerificationEligibilite
from apps.evaluation.services import CandidatureSpontaneeError, record_decision, run_eligibility
from apps.exports.candidatures import candidatures_concours, classeur_concours
from apps.recruitment import spontanees as service
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    DetailsSpontanee,
    OffreEmploi,
    Organisation,
    Piece,
    PosteConcours,
)
from integrations.site_source.base import SiteSource
from integrations.site_source.dto import (
    CandidatDTO,
    CandidatureDTO,
    ConcoursDTO,
    OffreEmploiDTO,
    OrganisationDTO,
    PieceDTO,
    PosteConcoursDTO,
    SouhaitsDTO,
)
from integrations.site_source.fixture_source import FixtureSource
from integrations.site_source.sync import SyncError, sync_from_source

from .conftest import make_user
from .test_poste_candidatures import HX, at, compteur, inputs, parse, po_msgids, row_ids

pytestmark = pytest.mark.django_db

S = Candidature.Statut
T = Candidature.Type
BASE_DIR = Path(settings.BASE_DIR)
MOTIF = "Profil intéressant pour de futurs recrutements (motif fictif)."
LISTE = reverse("recruitment:spontanees_liste")

GABARITS_KAN39 = [
    BASE_DIR / "templates" / p
    for p in (
        "recruitment/spontanees_liste.html",
        "recruitment/_spontanees_filtres.html",
        "recruitment/_spontanees_resultats.html",
        "recruitment/_fiche_spontanee.html",
        "recruitment/candidature_detail.html",
        "components/breadcrumb.html",
        "dashboard/index.html",
        "base.html",
    )
]

# --------------------------------------------------------------------------------------
# Utilisateurs et jeu de données de référence (spec, « Critères d'acceptation »)
# --------------------------------------------------------------------------------------


@pytest.fixture
def dir1(db):
    return make_user("dir1@mafoa.test", Role.DIRECTION)


@pytest.fixture
def admin1(db):
    return make_user("admin1@mafoa.test", Role.ADMIN)


@pytest.fixture
def cons1(db):
    return make_user("cons1@mafoa.test", Role.CONSULTANT)


def candidat(nom, prenom, n):
    return Candidat.objects.create(
        nom=nom,
        prenom=prenom,
        email=f"{prenom.lower()}.fictif{n}@example.org",
        telephone=f"06 00 11 22 {n:02d}",
        cin=f"ZZ90000{n}",
        date_naissance=date(1990, 1, n),
        ville="Villefictive",
        region="Région Fictive Est",
        sexe=Candidat.Sexe.FEMME,
    )


def spontanee(cand, when, statut=S.RECUE, souhaits=None, pieces=(), message=""):
    c = Candidature.objects.create(
        candidat=cand, type=T.SPONTANEE, date_candidature=when, statut=statut
    )
    DetailsSpontanee.objects.create(candidature=c, message=message, **(souhaits or {}))
    for t in pieces:
        add_piece(c, t)
    return c


def add_piece(c, t, key=None):
    return Piece.objects.create(
        candidature=c,
        type=t,
        nom_fichier=f"{t}.pdf",
        storage_key=key or f"fixture://test/{c.pk}/{t}.pdf",
        content_type="application/pdf",
    )


class Ref(dict):
    __getattr__ = dict.__getitem__


@pytest.fixture
def ref(dir1):
    public = Organisation.objects.create(nom="SRM fictive", type=Organisation.Type.PUBLIC)
    prive = Organisation.objects.create(nom="Société fictive", type=Organisation.Type.PRIVE)
    concours = Concours.objects.create(
        organisation=public, intitule="Concours fictif C", statut=Concours.Statut.OUVERT
    )
    p1 = PosteConcours.objects.create(
        concours=concours, reference="P-01", intitule="Ingénieur hydraulique fictif"
    )
    o1 = OffreEmploi.objects.create(organisation=prive, intitule="Comptable fictif O1")
    salma = candidat("Hamdi", "Salma", 1)
    hajar = candidat("Naciri", "Hajar", 5)
    s1 = spontanee(
        salma,
        at(2026, 11, 2, 10),
        souhaits={
            "fonction_souhaitee": "ingenieur",
            "secteur_souhaite": "Eau et assainissement",
            "lieu_souhaite": "Agadir",
            "disponibilite": "Immédiate",
            "consentement_vivier": True,
            "consentement_le": at(2026, 11, 2, 10),
        },
        pieces=("cv", "lettre"),
        message="Message libre fictif de Salma.\nSeconde ligne.",
    )
    s2 = spontanee(
        candidat("Berrada", "Anas", 2),
        at(2026, 10, 15, 9),
        souhaits={
            "fonction_souhaitee": "technicien",
            "secteur_souhaite": "Électricité",
            "lieu_souhaite": "Tanger",
        },
        pieces=("cv",),
    )
    s3 = spontanee(
        candidat("Tazi", "Rim", 3),
        at(2026, 9, 1, 14),
        souhaits={
            "fonction_souhaitee": "responsable",
            "secteur_souhaite": "Finance / Comptabilité",
            "lieu_souhaite": "Casablanca",
        },
        pieces=("cv", "lettre"),
    )
    s4 = spontanee(candidat("El Idrissi", "Omar", 4), at(2026, 11, 20, 8, 30))
    s5 = spontanee(
        hajar,
        at(2026, 11, 10, 16),
        souhaits={"fonction_souhaitee_brute": "Data scientist", "lieu_souhaite": "Agadir"},
        pieces=("lettre",),
    )
    Anomalie.objects.create(
        candidature=s5,
        type=Anomalie.Type.AUTRE,
        gravite=Anomalie.Gravite.HAUTE,
        description="Anomalie fictive",
    )
    s6 = spontanee(
        salma, at(2026, 6, 1, 9), souhaits={"fonction_souhaitee": "chef_service"}, pieces=("cv",)
    )
    c1 = Candidature.objects.create(
        candidat=hajar, poste_concours=p1, date_candidature=at(2026, 10, 1, 11), statut=S.ELIGIBLE
    )
    add_piece(c1, "cv")
    add_piece(c1, "diplome")
    o1c = Candidature.objects.create(
        candidat=candidat("Alaoui", "Karim", 6), offre=o1, date_candidature=at(2026, 11, 5, 12)
    )
    add_piece(o1c, "cv")
    d1 = record_decision(s2, dir1, S.VIVIER, MOTIF)
    d0 = record_decision(s3, dir1, S.REJETEE, MOTIF)
    return Ref(
        S1=s1, S2=s2, S3=s3, S4=s4, S5=s5, S6=s6, C1=c1, O1=o1c, p1=p1, o1=o1,
        concours=concours, D0=d0, D1=d1, salma=salma,
    )  # fmt: skip


def get(client, user, url, **extra):
    client.force_login(user)
    return client.get(url, **extra)


def liste_url(**params) -> str:
    q = QueryDict(mutable=True)
    for k, v in params.items():
        q.setlist(k, v if isinstance(v, list) else [v])
    return LISTE + (f"?{q.urlencode()}" if q else "")


def listed(client, user, ref, **params) -> list[str]:
    root = parse(get(client, user, liste_url(**params)))
    by_pk = {c.pk: k for k, c in ref.items() if isinstance(c, Candidature)}
    return [by_pk[i] for i in row_ids(root)]


def row(root, candidature):
    (r,) = root.find_all(
        lambda n: n.tag == "tr" and n.attrs.get("data-candidature-id") == str(candidature.pk)
    )
    return r


def cell(r, name):
    (c,) = r.find_all(lambda n: n.attrs.get("data-col") == name)
    return c


def by_attr(root, name, value=None):
    return root.find_all(
        lambda n: name in n.attrs and (value is None or n.attrs.get(name) == value)
    )


def fiche(c):
    return reverse("recruitment:candidature_detail", args=[c.pk])


def nb_queries(client, url, **extra) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url, **extra)
    assert response.status_code == 200
    return len(ctx.captured_queries)


def strict(fn):
    with pytest.raises(IntegrityError), transaction.atomic():
        fn()


# --------------------------------------------------------------------------------------
# Modèle et migration (CA1 à CA6)
# --------------------------------------------------------------------------------------

AVANT = [("recruitment", "0002_offre_entreprise_confidentielle_help_text")]
APRES = [("recruitment", "0003_kan39_candidatures_spontanees"), ("evaluation", "0005_kan39_candidatures_spontanees")]  # fmt: skip


def _migrer(cibles):
    executor = MigrationExecutor(connection)
    executor.migrate(cibles)
    return executor


def test_ca1_migration_sans_perte(dir1):
    executor = _migrer(AVANT)
    apps = executor.loader.project_state(
        [*AVANT, ("evaluation", "0004_kan15_decision_lot_id")]
    ).apps
    Org, Conc, Poste, Offre = (
        apps.get_model("recruitment", m)
        for m in ("Organisation", "Concours", "PosteConcours", "OffreEmploi")
    )
    Cand, Cdt, Pce = (
        apps.get_model("recruitment", m) for m in ("Candidat", "Candidature", "Piece")
    )
    Dec, Ver, Ano = (
        apps.get_model("evaluation", m) for m in ("Decision", "VerificationEligibilite", "Anomalie")
    )
    org = Org.objects.create(nom="Org fictive", type="public")
    conc = Conc.objects.create(organisation=org, intitule="Concours fictif", statut="ouvert")
    poste = Poste.objects.create(concours=conc, intitule="Poste fictif")
    offre = Offre.objects.create(organisation=org, intitule="Offre fictive")
    statuts = ["eligible", "rejetee", "recue", "a_verifier"]
    for i, statut in enumerate(statuts):
        cand = Cand.objects.create(nom=f"Fictif{i}", prenom="Test")
        kwargs = (
            {"poste_concours_id": poste.pk, "concours_id": conc.pk}
            if i % 2 == 0
            else {"offre_id": offre.pk}
        )
        c = Cdt.objects.create(
            candidat=cand,
            date_candidature=datetime(2026, 1, 1, tzinfo=UTC),
            statut=statut,
            **kwargs,
        )
        Pce.objects.create(candidature=c, type="cv", nom_fichier="cv.pdf", storage_key="k")
        Ver.objects.create(candidature=c, resultat="eligible", moteur_version="t")
        Ano.objects.create(candidature=c, type="autre", gravite="info", description="fictive")
        Dec.objects.create(candidature=c, auteur_id=dir1.pk, decision=statut, motif=MOTIF)
    avant = {
        "candidatures": dict(Cdt.objects.values_list("id", "statut")),
        "decisions": Dec.objects.count(),
        "verifications": Ver.objects.count(),
        "pieces": Pce.objects.count(),
        "anomalies": Ano.objects.count(),
    }
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    _migrer(APRES)
    apres = {
        "candidatures": dict(Candidature.objects.values_list("id", "statut")),
        "decisions": Decision.objects.count(),
        "verifications": VerificationEligibilite.objects.count(),
        "pieces": Piece.objects.count(),
        "anomalies": Anomalie.objects.count(),
    }
    assert apres == avant
    for c in Candidature.objects.all():
        assert c.type == (T.CONCOURS if c.poste_concours_id else T.OFFRE)


def test_ca2_contraintes_type_cible(ref):
    p1, o1, cand = ref.p1, ref.o1, ref.salma
    base = {"candidat": cand, "date_candidature": at(2026, 11, 1)}
    strict(lambda: Candidature.objects.create(type=T.SPONTANEE, poste_concours=p1, **base))
    strict(lambda: Candidature.objects.create(type=T.CONCOURS, **base))
    strict(lambda: Candidature.objects.create(type=T.OFFRE, poste_concours=p1, offre=o1, **base))
    strict(lambda: Candidature.objects.create(**base))
    # En SQL : type vidé ou cible ajoutée à une spontanée.
    strict(lambda: Candidature.objects.filter(pk=ref.C1.pk).update(type=""))
    strict(lambda: Candidature.objects.filter(pk=ref.S1.pk).update(offre=o1))

    def sql():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE recruitment_candidature SET type = 'spontanee' WHERE id = %s", [ref.C1.pk]
            )

    strict(sql)


def test_ca3_type_par_defaut_jamais_spontanee(ref):
    base = {"date_candidature": at(2026, 11, 1)}
    c = Candidature.objects.create(
        candidat=candidat("Fictif", "Un", 7), poste_concours=ref.p1, **base
    )
    assert c.type == T.CONCOURS
    o = Candidature.objects.create(candidat=ref.salma, offre=ref.o1, **base)
    assert o.type == T.OFFRE
    strict(lambda: Candidature.objects.create(candidat=ref.salma, **base))
    # bulk_create suit la même règle.
    (b,) = Candidature.objects.bulk_create(
        [Candidature(candidat=candidat("Fictif", "Deux", 8), offre=ref.o1, **base)]
    )
    assert Candidature.objects.get(pk=b.pk).type == T.OFFRE
    assert (
        not Candidature.objects.filter(type=T.SPONTANEE)
        .exclude(pk__in=[ref[k].pk for k in ("S1", "S2", "S3", "S4", "S5", "S6")])
        .exists()
    )


@pytest.mark.parametrize("statut", ["eligible", "non_eligible", "a_verifier", "preselectionnee", "convoquee", "retenue"])  # fmt: skip
def test_ca4_statuts_interdits_pour_une_spontanee(ref, statut):
    strict(lambda: Candidature.objects.filter(pk=ref.S1.pk).update(statut=statut))


def test_ca4_vivier_interdit_pour_concours_et_offre(ref):
    strict(lambda: Candidature.objects.filter(pk=ref.C1.pk).update(statut=S.VIVIER))
    strict(lambda: Candidature.objects.filter(pk=ref.O1.pk).update(statut=S.VIVIER))


def test_ca5_migration_inverse_refusee_avec_spontanees(ref):
    n = Candidature.objects.count()
    with pytest.raises(RuntimeError, match="candidature\\(s\\) spontanée\\(s\\)"):
        _migrer(AVANT)
    assert Candidature.objects.count() == n
    assert Candidature.objects.filter(type=T.SPONTANEE).count() == 6
    assert DetailsSpontanee.objects.count() == 6


def _contraintes():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'recruitment_candidature'::regclass"
        )
        return {r[0] for r in cursor.fetchall()}


def test_ca5_migration_inverse_sans_spontanee(db):
    assert {"candidature_type_cible", "candidature_statut_selon_type"} <= _contraintes()
    _migrer(AVANT)
    noms = _contraintes()
    assert "candidature_poste_xor_offre" in noms
    assert "candidature_type_cible" not in noms
    _migrer(APRES)
    assert "candidature_type_cible" in _contraintes()


def test_ca6_libelle_et_administration(client, admin1, ref):
    assert str(ref.S1) == "HAMDI Salma → Candidature spontanée"
    assert ref.S1.concours is None and ref.S1.cible is None
    assert str(ref.C1).endswith("→ P-01 — Ingénieur hydraulique fictif")
    response = get(client, admin1, reverse("admin:recruitment_candidature_changelist"))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Par type" in html or "By type" in html
    assert "Candidature spontanée" in html
    response = client.get(reverse("admin:recruitment_candidature_change", args=[ref.S1.pk]))
    assert response.status_code == 200 and "Eau et assainissement" in response.content.decode()


# --------------------------------------------------------------------------------------
# Contrat SiteSource et synchronisation (CA7 à CA16)
# --------------------------------------------------------------------------------------

DATE = datetime(2026, 11, 2, 10, tzinfo=UTC)


class MiniSource(SiteSource):
    """Source fictive minimale : un poste, une offre, deux candidats."""

    name = "mini"

    def __init__(self, candidatures, pieces=()):
        self._candidatures = list(candidatures)
        self._pieces = list(pieces)

    def list_organisations(self):
        return [OrganisationDTO("ORG-M", "Organisation fictive mini", "public")]

    def list_concours(self):
        return [
            ConcoursDTO("C-M", "ORG-M", "Concours fictif mini", "c-m", "", None, None, "ouvert")
        ]

    def list_postes(self):
        return [
            PosteConcoursDTO(
                "P-M", "C-M", "M-01", "Poste fictif mini", "p-m", "", "ingenieur", 1,
                "presentiel", "", "", "", None, None, 5,
            )
        ]  # fmt: skip

    def list_offres(self):
        return [
            OffreEmploiDTO(
                "O-M", "ORG-M", "Offre fictive mini", "o-m", False, "", "CDI", "presentiel", "",
                "", 1, "", "", [], None, None, "ouverte", None,
            )
        ]  # fmt: skip

    def list_candidats(self, since=None):
        return [
            CandidatDTO("U-1", "Hamdi", "Salma", "salma.mini@example.org", "0600000001", None, "ZZ1", "Agadir"),
            CandidatDTO("U-2", "Berrada", "Anas", "anas.mini@example.org", "0600000002", None, "ZZ2", "Tanger"),
        ]  # fmt: skip

    def list_candidatures(self, since=None):
        return list(self._candidatures)

    def list_pieces(self, since=None):
        return list(self._pieces)

    def read_piece(self, storage_key, *, max_bytes=None, timeout=None):
        return f"%PDF-fictif {storage_key}".encode()


SOUHAITS_S1 = SouhaitsDTO(
    fonction_souhaitee="Ingénieur",
    secteur_souhaite="Eau et assainissement",
    lieu_souhaite="Agadir",
    disponibilite="Immédiate",
    message="Message fictif",
    consentement_vivier=True,
    consentement_le=DATE,
)


def dto(source_id="SP-1", candidat="U-1", **kwargs):
    return CandidatureDTO(source_id, candidat, kwargs.pop("date", DATE), {}, **kwargs)


def audit_sync():
    return JournalAudit.objects.filter(action=AuditAction.SYNC).latest("id")


def test_ca7_import_idempotent_d_une_spontanee():
    source = MiniSource([dto(type="spontanee", souhaits=SOUHAITS_S1)])
    r1 = sync_from_source(source, compute_hashes=False)
    assert r1.candidatures_par_type["spontanee"] == {"created": 1, "updated": 0}
    r2 = sync_from_source(source, compute_hashes=False)
    assert r2.candidatures_par_type["spontanee"] == {"created": 0, "updated": 1}
    (c,) = Candidature.objects.filter(source_id="SP-1")
    assert (c.type, c.statut, c.poste_concours, c.offre, c.concours) == (
        T.SPONTANEE, S.RECUE, None, None, None,
    )  # fmt: skip
    d = c.details_spontanee
    assert d.fonction_souhaitee == "ingenieur" and d.secteur_souhaite == "Eau et assainissement"
    assert d.lieu_souhaite == "Agadir" and d.disponibilite == "Immédiate"
    assert d.consentement_vivier is True and d.consentement_le == DATE


def test_ca8_dto_incoherents_refuses():
    source = MiniSource(
        [
            dto("SP-A", type="spontanee", poste_source_id="P-M"),
            dto("SP-B"),
            dto("SP-C", type="pirate"),
            dto("OK-1", type="spontanee"),
            dto("OK-2", type="concours", poste_source_id="P-M"),
        ],
        pieces=[PieceDTO("F-A", "SP-A", "cv", "cv.pdf", "k-a", "application/pdf")],
    )
    report = sync_from_source(source, compute_hashes=False)
    assert set(Candidature.objects.values_list("source_id", flat=True)) == {"OK-1", "OK-2"}
    assert [r["source_id"] for r in report.refusees] == ["SP-A", "SP-B", "SP-C"]
    assert not Piece.objects.exists()
    meta = audit_sync().metadata
    assert meta["refusees"] == 3
    assert {r["source_id"] for r in meta["refusees_detail"]} == {"SP-A", "SP-B", "SP-C"}
    assert set(meta["refusees_detail"][0]) == {"source_id", "motif"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"type": "concours"},
        {"type": "offre"},
        {"type": "offre", "offre_source_id": "O-M", "poste_source_id": "P-M"},
    ],
)
def test_ca8_autres_incoherences_refusees(kwargs):
    report = sync_from_source(MiniSource([dto("X-1", **kwargs)]), compute_hashes=False)
    assert len(report.refusees) == 1 and not Candidature.objects.exists()


def test_ca9_type_absent_deduit_du_poste():
    sync_from_source(MiniSource([dto("A-1", poste_source_id="P-M")]), compute_hashes=False)
    sync_from_source(MiniSource([dto("A-2", offre_source_id="O-M")]), compute_hashes=False)
    assert Candidature.objects.get(source_id="A-1").type == T.CONCOURS
    assert Candidature.objects.get(source_id="A-2").type == T.OFFRE


def test_ca10_cible_inconnue_jamais_spontanee():
    source = MiniSource([dto("OK-1", type="spontanee"), dto("A-9", type="concours", poste_source_id="P-INCONNU")])  # fmt: skip
    with pytest.raises(SyncError):
        sync_from_source(source, compute_hashes=False)
    assert not Candidature.objects.exists()
    assert not JournalAudit.objects.filter(action=AuditAction.SYNC).exists()


def test_ca11_changement_de_type_a_la_source(dir1):
    sync_from_source(MiniSource([dto("SP-2", "U-2", type="spontanee")]), compute_hashes=False)
    s2 = Candidature.objects.get(source_id="SP-2")
    decision = record_decision(s2, dir1, S.VIVIER, MOTIF)
    nouvelle_date = datetime(2026, 10, 20, tzinfo=UTC)
    report = sync_from_source(
        MiniSource([dto("SP-2", "U-2", type="offre", offre_source_id="O-M", date=nouvelle_date)]),
        compute_hashes=False,
    )
    s2.refresh_from_db()
    assert (s2.type, s2.offre, s2.statut) == (T.SPONTANEE, None, S.VIVIER)
    assert s2.date_candidature == nouvelle_date  # autres champs mis à jour
    assert list(s2.decisions.all()) == [decision]
    attendu = [{"candidature_id": s2.pk, "source_id": "SP-2"}]
    assert report.conflits_type == attendu
    meta = audit_sync().metadata
    assert meta["conflits_type"] == 1 and meta["conflits_type_detail"] == attendu


def test_ca12_souhaits_mis_a_jour_statut_inchange(dir1):
    sync_from_source(
        MiniSource([dto(type="spontanee", souhaits=SOUHAITS_S1)]), compute_hashes=False
    )
    c = Candidature.objects.get(source_id="SP-1")
    Candidature.objects.filter(pk=c.pk).update(statut=S.EN_ANALYSE)
    nouveaux = replace(SOUHAITS_S1, secteur_souhaite="Électricité")
    sync_from_source(MiniSource([dto(type="spontanee", souhaits=nouveaux)]), compute_hashes=False)
    c.refresh_from_db()
    assert c.details_spontanee.secteur_souhaite == "Électricité"
    assert c.statut == S.EN_ANALYSE
    # Souhaits absents : tous les champs vides (règle 15).
    sync_from_source(MiniSource([dto(type="spontanee")]), compute_hashes=False)
    d = DetailsSpontanee.objects.get(candidature=c)
    assert (d.secteur_souhaite, d.fonction_souhaitee, d.consentement_vivier) == ("", "", None)


def test_ca13_fonction_hors_referentiel():
    souhaits = SouhaitsDTO(fonction_souhaitee="Data scientist")
    sync_from_source(MiniSource([dto(type="spontanee", souhaits=souhaits)]), compute_hashes=False)
    d = DetailsSpontanee.objects.get()
    assert d.fonction_souhaitee == "" and d.fonction_souhaitee_brute == "Data scientist"


def test_message_long_tronque():
    souhaits = SouhaitsDTO(message="x" * 6000)
    sync_from_source(MiniSource([dto(type="spontanee", souhaits=souhaits)]), compute_hashes=False)
    d = DetailsSpontanee.objects.get()
    assert len(d.message) == 5000 and d.message_tronque


def test_ca14_souhaits_d_une_candidature_de_concours_ignores():
    source = MiniSource([dto("A-1", type="concours", poste_source_id="P-M", souhaits=SOUHAITS_S1)])
    report = sync_from_source(source, compute_hashes=False)
    c = Candidature.objects.get(source_id="A-1")
    assert c.type == T.CONCOURS
    assert not DetailsSpontanee.objects.exists()
    assert report.souhaits_ignores == ["A-1"]
    assert audit_sync().metadata["souhaits_ignores"] == 1


def test_ca15_pieces_d_une_spontanee():
    source = MiniSource(
        [dto(type="spontanee")],
        pieces=[
            PieceDTO("F-1", "SP-1", "cv", "cv.pdf", "mini://cv", "application/pdf"),
            PieceDTO("F-2", "SP-1", "lettre", "lettre.pdf", "mini://lettre", "application/pdf"),
        ],
    )
    sync_from_source(source)
    pieces = Piece.objects.filter(candidature__source_id="SP-1").order_by("type")
    assert [p.type for p in pieces] == ["cv", "lettre"]
    for p in pieces:
        contenu = source.read_piece(p.storage_key)
        assert p.sha256 == hashlib.sha256(contenu).hexdigest() and p.taille == len(contenu)
        assert p.storage_key.startswith("mini://")  # référence seulement, aucune copie


def test_ca16_audit_sync_par_type_sans_donnee_personnelle():
    source = FixtureSource(seed=1234)
    sync_from_source(source, compute_hashes=False)
    entree = audit_sync()
    meta = entree.metadata
    assert meta["candidatures_par_type"]["spontanee"]["created"] == 6
    par_type = meta["candidatures_par_type"]
    assert par_type["concours"]["created"] + par_type["offre"]["created"] == 52
    assert meta["refusees"] == 0 and meta["conflits_type"] == 0
    texte = json.dumps(meta, ensure_ascii=False) + entree.object_repr
    for c in source.list_candidats():
        for valeur in (c.nom, c.email, c.telephone, c.cin):
            assert valeur not in texte


# --------------------------------------------------------------------------------------
# FixtureSource (CA17 à CA20)
# --------------------------------------------------------------------------------------


def test_ca17_six_spontanees_fictives():
    s = FixtureSource()
    sp = [a for a in s.list_candidatures() if a.type == "spontanee"]
    assert len(sp) == 6
    assert all(a.source_id.startswith("SP-") for a in sp)
    assert all(a.poste_source_id is None and a.offre_source_id is None for a in sp)
    concours = {a.candidat_source_id for a in s.list_candidatures() if a.type == "concours"}
    assert any(a.candidat_source_id in concours for a in sp)
    assert max(sum(1 for b in sp if b.candidat_source_id == a.candidat_source_id) for a in sp) == 2


# Empreintes des candidats, candidatures et pièces existants (non spontanés) et des octets de
# leurs PDF, calculées AVANT KAN-39 (même graine), avec les seuls champs qui existaient alors.
EMPREINTES_AVANT_KAN39 = {
    20260101: "4b0bff7d26dab6743c5403a922b757abc182fc7e3e7a140c74530676fd8b6086",
    1234: "d3fd266dd0e9164ac4c91f5660e07fc066be165a76a73117fe570e7998a8d3da",
}
CHAMPS_CANDIDATURE_AVANT = (
    "source_id", "candidat_source_id", "date_candidature", "donnees_declarees",
    "poste_source_id", "offre_source_id",
)  # fmt: skip


@pytest.mark.parametrize("seed", sorted(EMPREINTES_AVANT_KAN39))
def test_ca18_donnees_existantes_inchangees(seed):
    s = FixtureSource(seed=seed)
    anciennes = [a for a in s.list_candidatures() if not a.source_id.startswith("SP-")]
    legacy = {a.candidat_source_id for a in anciennes}
    cands = [asdict(c) for c in s.list_candidats() if c.source_id in legacy]
    apps_ = [{k: asdict(a)[k] for k in CHAMPS_CANDIDATURE_AVANT} for a in anciennes]
    pieces = [asdict(p) for p in s.list_pieces() if not p.candidature_source_id.startswith("SP-")]
    pdfs = [hashlib.sha256(s.read_piece(p["storage_key"])).hexdigest() for p in pieces]
    blob = json.dumps([cands, apps_, pieces, pdfs], default=str, sort_keys=True, ensure_ascii=False)
    assert (len(cands), len(apps_), len(pieces)) == (52, 52, 200)
    assert hashlib.sha256(blob.encode()).hexdigest() == EMPREINTES_AVANT_KAN39[seed]
    # Les candidatures existantes portent désormais un type explicite.
    assert {a.type for a in anciennes} == {"concours", "offre"}


def _texte_pdf(s, key):
    return PdfReader(BytesIO(s.read_piece(key))).pages[0].extract_text()


def test_ca19_variete_et_donnees_fictives():
    s = FixtureSource()
    sp = {a.source_id: a for a in s.list_candidatures() if a.type == "spontanee"}
    pieces = {}
    for p in s.list_pieces():
        if p.candidature_source_id in sp:
            pieces.setdefault(p.candidature_source_id, []).append(p)
    types = {k: {p.type for p in v} for k, v in pieces.items()}
    assert any(t == {"cv", "lettre"} for t in types.values())
    assert any(t == {"cv"} for t in types.values())
    assert any("cv" not in t for t in types.values())
    souhaits = [a.souhaits for a in sp.values()]
    assert None in souhaits
    fonctions = {x.fonction_souhaitee for x in souhaits if x}
    assert "Data scientist" in fonctions and len(fonctions) >= 3
    assert any(x and x.secteur_souhaite for x in souhaits)
    assert any(x and x.lieu_souhaite for x in souhaits)
    assert any(x and x.disponibilite for x in souhaits)
    consentements = {x.consentement_vivier if x else None for x in souhaits}
    assert consentements == {True, False, None}
    candidats = {c.source_id: c for c in s.list_candidats()}
    for a in sp.values():
        c = candidats[a.candidat_source_id]
        assert c.email.lower().endswith(("@example.com", "@example.org"))
        assert c.cin.startswith("ZZ") and c.telephone.startswith("06 00")
    for liste in pieces.values():
        for p in liste:
            texte = _texte_pdf(s, p.storage_key)
            assert "DOCUMENT FICTIF" in texte
            if p.type == "lettre":
                assert "Candidature spontanée" in texte


def test_ca20_generation_deterministe():
    a, b = FixtureSource(seed=7), FixtureSource(seed=7)
    sp_a = [x for x in a.list_candidatures() if x.type == "spontanee"]
    assert sp_a == [x for x in b.list_candidatures() if x.type == "spontanee"]
    for p in a.list_pieces():
        if p.candidature_source_id.startswith("SP-"):
            assert a.read_piece(p.storage_key) == b.read_piece(p.storage_key)


# --------------------------------------------------------------------------------------
# Moteur d'éligibilité (CA21, CA22)
# --------------------------------------------------------------------------------------


def test_ca21_moteur_refuse_une_spontanee(ref):
    n_update = JournalAudit.objects.filter(action=AuditAction.UPDATE).count()
    with pytest.raises(CandidatureSpontaneeError):
        run_eligibility(ref.S1)
    assert not VerificationEligibilite.objects.filter(candidature=ref.S1).exists()
    assert Candidature.objects.get(pk=ref.S1.pk).statut == S.RECUE
    assert JournalAudit.objects.filter(action=AuditAction.UPDATE).count() == n_update


def test_ca22_sync_site_evaluate_ignore_les_spontanees(ref, capsys):
    call_command("sync_site", "--evaluate")
    out = capsys.readouterr().out
    assert "Éligibilité calculée" in out
    assert "Candidatures spontanées ignorées (aucun critère d'éligibilité) : 12" in out
    assert not VerificationEligibilite.objects.filter(candidature__type=T.SPONTANEE).exists()
    assert VerificationEligibilite.objects.filter(candidature=ref.C1).exists()
    assert VerificationEligibilite.objects.filter(candidature=ref.O1).exists()


# --------------------------------------------------------------------------------------
# Liste : accès et affichage (CA23 à CA28)
# --------------------------------------------------------------------------------------


def test_ca23_liste_par_defaut(client, dir1, ref):
    response = get(client, dir1, LISTE)
    assert response.status_code == 200
    root = parse(response)
    assert compteur(root) == "6 candidatures affichées sur 6"
    assert listed(client, dir1, ref) == ["S4", "S5", "S1", "S2", "S3", "S6"]


def test_ca24_acces(client, admin1, cons1, ref):
    assert get(client, admin1, LISTE).status_code == 200
    client.logout()
    for extra in ({}, HX):
        response = get(client, cons1, liste_url(statut="recue"), **extra)
        assert response.status_code == 403
        assert "HAMDI" not in response.content.decode()
    refus = JournalAudit.objects.filter(action=AuditAction.ACCESS_DENIED)
    assert refus.count() == 2
    assert {e.metadata["chemin"] for e in refus} == {LISTE}
    client.logout()
    response = client.get(LISTE)
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == reverse("account_login") and parse_qs(parsed.query)["next"] == [LISTE]
    client.force_login(admin1)
    assert client.post(LISTE).status_code == 405


def test_ca24_sans_totp_et_inactif(client, user_without_mfa, ref):
    client.force_login(user_without_mfa)
    response = client.get(LISTE)
    assert response.status_code == 302
    assert urlparse(response.url).path == reverse("mfa_activate_totp")


def test_ca25_colonnes(client, dir1, ref):
    root = parse(get(client, dir1, LISTE))
    s1 = row(root, ref.S1)
    lien = cell(s1, "candidat").find_all(lambda n: n.tag == "a")[0]
    assert lien.text() == "HAMDI Salma" and lien.attrs["href"] == fiche(ref.S1)
    assert cell(s1, "date").text() == "02/11/2026 10:00"
    assert cell(s1, "fonction").text() == "Ingénieur"
    assert cell(s1, "secteur").text() == "Eau et assainissement"
    assert cell(s1, "lieu").text() == "Agadir"
    pieces = cell(s1, "pieces")
    assert pieces.text().startswith("CV") and "2 pièces" in pieces.text()
    assert cell(s1, "statut").text() == "Reçue"
    assert not by_attr(cell(s1, "anomalies"), "data-gravite")
    s4 = row(root, ref.S4)
    for name in ("fonction", "secteur", "lieu"):
        assert cell(s4, name).text() == "—"
    assert cell(s4, "pieces").text().startswith("Sans CV")
    s5 = row(root, ref.S5)
    (brute,) = by_attr(cell(s5, "fonction"), "data-fonction-brute")
    assert brute.text() == "Data scientist"
    anomalies = cell(s5, "anomalies")
    assert anomalies.text().startswith("1") and by_attr(anomalies, "data-gravite", "haute")


def test_ca26_indicateur_dossiers_spontanes(client, dir1, ref):
    root = parse(get(client, dir1, LISTE))
    for c in (ref.S1, ref.S6):
        (ind,) = by_attr(row(root, c), "data-dossiers-spontanes")
        assert ind.text() == "2 dossiers spontanés"
    for c in (ref.S2, ref.S3, ref.S4, ref.S5):
        assert not by_attr(row(root, c), "data-dossiers-spontanes")
    # Un dossier rejeté ne compte pas.
    Candidature.objects.filter(pk=ref.S6.pk).update(statut=S.REJETEE)
    root = parse(client.get(LISTE))
    assert not by_attr(row(root, ref.S1), "data-dossiers-spontanes")


def test_ca27_minimisation(client, dir1, ref):
    html = get(client, dir1, LISTE).content.decode()
    for c in Candidat.objects.all():
        for valeur in (c.email, c.telephone, c.cin, c.date_naissance.strftime("%d/%m/%Y")):
            assert valeur not in html
    for valeur in ("Villefictive", "Région Fictive Est", "Femme", "Message libre fictif"):
        assert valeur not in html


def test_ca28_navigation(client, dir1, admin1, ref):
    for user in (dir1, admin1):
        root = parse(get(client, user, LISTE))
        (nav,) = by_attr(root, "data-nav-spontanees")
        assert nav.attrs["href"] == LISTE and nav.attrs.get("aria-current") == "page"
        root = parse(client.get(reverse("dashboard")))
        (nav,) = by_attr(root, "data-nav-spontanees")
        assert "aria-current" not in nav.attrs


# --------------------------------------------------------------------------------------
# Liste : filtres, recherche, tri, pagination (CA29 à CA40)
# --------------------------------------------------------------------------------------


def test_ca29_filtre_statut(client, dir1, ref):
    assert listed(client, dir1, ref, statut="recue") == ["S4", "S5", "S1", "S6"]
    assert listed(client, dir1, ref, statut=["vivier", "rejetee"]) == ["S2", "S3"]
    root = parse(client.get(LISTE))
    options = [n.parent.text() for n in inputs(root, "statut")]
    assert options == ["Reçue", "En analyse", "Conservée au vivier", "Rejetée"]


def test_ca30_filtre_fonction(client, dir1, ref):
    assert listed(client, dir1, ref, fonction=["ingenieur", "technicien"]) == ["S1", "S2"]
    assert listed(client, dir1, ref, fonction="non_precisee") == ["S4", "S5"]
    assert len(inputs(parse(client.get(LISTE)), "fonction")) == 12


def test_ca31_filtres_lieu_et_secteur(client, dir1, ref):
    assert listed(client, dir1, ref, lieu="Agadir") == ["S5", "S1"]
    assert listed(client, dir1, ref, secteur="Électricité") == ["S2"]
    root = parse(client.get(LISTE))
    assert [n.attrs["value"] for n in inputs(root, "lieu")] == ["Agadir", "Casablanca", "Tanger"]
    secteurs = [n.attrs["value"] for n in inputs(root, "secteur")]
    # Valeurs distinctes présentes en base, triées par la base (collation).
    assert set(secteurs) == {"Eau et assainissement", "Finance / Comptabilité", "Électricité"}
    assert secteurs == service.options_secteurs()
    # Une valeur absente de la base est ignorée.
    assert listed(client, dir1, ref, lieu="Pirate") == ["S4", "S5", "S1", "S2", "S3", "S6"]


def test_ca32_periode_bornes_incluses(client, dir1, ref):
    assert listed(client, dir1, ref, du="2026-11-01", au="2026-11-10") == ["S5", "S1"]
    assert listed(client, dir1, ref, du="pas-une-date") == ["S4", "S5", "S1", "S2", "S3", "S6"]


def test_ca33_pieces_et_anomalies(client, dir1, ref):
    assert listed(client, dir1, ref, pieces="sans_cv") == ["S4", "S5"]
    assert listed(client, dir1, ref, pieces="avec_cv") == ["S1", "S2", "S3", "S6"]
    assert listed(client, dir1, ref, anomalies="avec") == ["S5"]


def test_ca34_recherche(client, dir1, ref):
    assert listed(client, dir1, ref, q="hamdi") == ["S1", "S6"]
    assert listed(client, dir1, ref, q="salma HAMDI") == ["S1", "S6"]
    assert len(listed(client, dir1, ref, q="h")) == 6
    assert listed(client, dir1, ref, q="0600") == []
    assert listed(client, dir1, ref, q="fictif1@example") == []


def test_ca35_tri_et_parametres_invalides(client, dir1, ref):
    asc = listed(client, dir1, ref, tri="candidat")
    noms = {k: ref[k].candidat.nom_normalise for k in asc}
    assert [noms[k] for k in asc] == sorted(noms[k] for k in asc)
    assert listed(client, dir1, ref, tri="-candidat") == asc[::-1]
    response = get(
        client, dir1, liste_url(tri="sexe", statut="eligible", fonction="pirate", page="abc", taille="1000")
    )  # fmt: skip
    assert response.status_code == 200
    root = parse(response)
    assert compteur(root) == "6 candidatures affichées sur 6"
    assert len(row_ids(root)) <= 100


def test_ca35_tri_par_fonction_statut_anomalies(client, dir1, ref):
    # Ordre alphabétique des libellés : Chef de service, Ingénieur, Responsable, Technicien.
    assert listed(client, dir1, ref, tri="fonction")[:4] == ["S6", "S1", "S3", "S2"]
    assert listed(client, dir1, ref, tri="-anomalies")[0] == "S5"
    statuts = listed(client, dir1, ref, tri="statut")
    assert statuts[-2:] == ["S2", "S3"]  # vivier puis rejetée


def _bulk_spontanees(n, *, when=None, souhaits=True, pieces=True, anomalies=True):
    cands = Candidat.objects.bulk_create(
        [Candidat(nom=f"Volume{i:04d}", prenom="Fictif", nom_normalise=f"volume{i:04d} fictif") for i in range(n)]
    )  # fmt: skip
    cs = Candidature.objects.bulk_create(
        [
            Candidature(
                candidat=c, type=T.SPONTANEE, date_candidature=when or at(2026, 11, 1, 9, i % 60)
            )
            for i, c in enumerate(cands)
        ]
    )
    if souhaits:
        DetailsSpontanee.objects.bulk_create(
            [
                DetailsSpontanee(candidature=c, fonction_souhaitee="agent", lieu_souhaite=f"Ville{i % 5}")
                for i, c in enumerate(cs)
            ]
        )  # fmt: skip
    if pieces:
        Piece.objects.bulk_create(
            [Piece(candidature=c, type="cv", nom_fichier="cv.pdf", storage_key="k") for c in cs]
        )
    if anomalies:
        Anomalie.objects.bulk_create(
            [
                Anomalie(candidature=c, type="autre", gravite="moyenne", description="fictive")
                for c in cs[::3]
            ]
        )
    return cs


def test_ca36_pagination_stable(client, dir1):
    cs = _bulk_spontanees(
        120, when=at(2026, 11, 1, 9), souhaits=False, pieces=False, anomalies=False
    )
    client.force_login(dir1)
    vus = []
    for page in (1, 2, 3):
        vus += row_ids(parse(client.get(liste_url(page=str(page)))))
    assert sorted(vus) == sorted(c.pk for c in cs) and len(vus) == 120


def test_ca37_requetes_constantes(client, dir1):
    client.force_login(dir1)
    _bulk_spontanees(10)
    petit = {t: nb_queries(client, liste_url(taille=str(t))) for t in (50, 100)}
    _bulk_spontanees(990)
    grand = {t: nb_queries(client, liste_url(taille=str(t))) for t in (50, 100)}
    assert petit[50] == petit[100] == grand[50] == grand[100]
    assert grand[100] <= 12


def test_ca38_fragment_htmx_et_reinitialiser(client, dir1, ref):
    url = liste_url(statut="recue")
    fragment = get(client, dir1, url, **HX)
    html = fragment.content.decode()
    assert "<html" not in html and "data-compteur" in html
    page = client.get(url)
    assert "<html" in page.content.decode()
    assert row_ids(parse(fragment)) == row_ids(parse(page))
    assert "Vary" in fragment and "HX-Request" in fragment["Vary"]
    root = parse(page)
    (reset,) = [n for n in by_attr(root, "data-reinitialiser")]
    assert reset.attrs["href"] == LISTE
    assert listed(client, dir1, ref) == ["S4", "S5", "S1", "S2", "S3", "S6"]


def test_ca39_logique_serveur_unique(client, dir1, ref):
    params = {"statut": ["recue", "vivier"], "lieu": ["Agadir", "Tanger"], "taille": ["25"]}
    q = QueryDict(mutable=True)
    for k, v in params.items():
        q.setlist(k, v)
    qs, _flt = service.filter_spontanees(q)
    vus = set()
    client.force_login(dir1)
    page = 1
    while True:
        ids = row_ids(parse(client.get(f"{LISTE}?{q.urlencode()}&page={page}")))
        vus |= set(ids)
        if len(ids) < 25:
            break
        page += 1
    assert set(qs.values_list("pk", flat=True)) == vus == {ref.S1.pk, ref.S2.pk, ref.S5.pk}


def test_ca40_listes_vides(client, dir1):
    root = parse(get(client, dir1, LISTE))
    assert "Aucune candidature spontanée pour le moment." in root.text()
    assert re.fullmatch(r"0 candidatures? affichées? sur 0", compteur(root))


def test_ca40_filtres_sans_resultat(client, dir1, ref):
    root = parse(get(client, dir1, liste_url(q="inexistant")))
    assert "Aucune candidature ne correspond à ces critères." in root.text()
    assert by_attr(root, "data-reinitialiser")


# --------------------------------------------------------------------------------------
# Fiche (CA41 à CA48)
# --------------------------------------------------------------------------------------


def souhait(root, name):
    (n,) = by_attr(root, "data-souhait", name)
    return n.text()


def test_ca41_fiche_et_souhaits(client, dir1, ref):
    response = get(client, dir1, fiche(ref.S1))
    assert response.status_code == 200
    root = parse(response)
    assert by_attr(root, "data-type-spontanee")[0].text() == "Candidature spontanée"
    assert by_attr(root, "id", "statut-candidature")[0].text() == "Reçue"
    assert souhait(root, "fonction") == "Ingénieur"
    assert souhait(root, "secteur") == "Eau et assainissement"
    assert souhait(root, "lieu") == "Agadir"
    assert souhait(root, "disponibilite") == "Immédiate"
    assert souhait(root, "consentement") == "Oui, le 02/11/2026"
    assert "Message libre fictif de Salma." in souhait(root, "message")
    assert "Consentement au vivier" in root.text()
    for lien in by_attr(root, "data-retour-liste"):
        assert lien.attrs["href"] == LISTE
    assert by_attr(root, "data-lien-spontanees")


def test_ca42_pas_de_bloc_eligibilite(client, dir1, ref):
    html = get(client, dir1, fiche(ref.S1)).content.decode()
    assert 'id="titre-eligibilite"' not in html
    assert "Éligibilité non encore vérifiée" not in html
    assert "data-ecart" not in html and "Score sur grille" not in html
    assert "Candidature sans poste ni offre : aucune vérification d’éligibilité." in html
    html_c1 = client.get(fiche(ref.C1)).content.decode()
    assert 'id="titre-eligibilite"' in html_c1 and "Score sur grille" in html_c1
    assert "data-souhaits" not in html_c1 and "data-autres-candidatures" not in html_c1


def test_ca43_fiche_sans_souhait_ni_piece(client, dir1, ref):
    response = get(client, dir1, fiche(ref.S4))
    assert response.status_code == 200
    root = parse(response)
    for name in ("fonction", "secteur", "lieu", "disponibilite", "consentement", "message"):
        assert souhait(root, name) == "Non renseigné"
    assert "Aucune pièce reçue" in root.text()
    assert by_attr(root, "data-consentement-absent")


@override_settings(SITE_SOURCE="fixture", SITE_SOURCE_FIXTURE_SEED=20260101)
def test_ca44_visionneuse_des_pieces(client, dir1, ref):
    cv = ref.S1.pieces.get(type="cv")
    Piece.objects.filter(pk=cv.pk).update(
        storage_key="fixture://candidatures-spontanees/SP-0001/cv.pdf"
    )
    client.force_login(dir1)
    visionneuse = client.get(reverse("recruitment:piece_visionneuse", args=[ref.S1.pk, cv.pk]))
    assert visionneuse.status_code == 200
    contenu = client.get(reverse("recruitment:piece_contenu", args=[ref.S1.pk, cv.pk]))
    assert contenu.status_code == 200 and contenu.content.startswith(b"%PDF")
    entree = JournalAudit.objects.filter(action=AuditAction.VIEW, metadata__vue="piece").get()
    assert entree.metadata["candidature_id"] == ref.S1.pk


def test_ca45_autres_candidatures(client, dir1, ref):
    root = parse(get(client, dir1, fiche(ref.S5)))
    (item,) = by_attr(root, "data-autre-candidature")
    assert item.attrs["data-autre-candidature"] == str(ref.C1.pk)
    texte = item.text()
    assert "Concours" in texte and "P-01 — Ingénieur hydraulique fictif" in texte
    assert "01/10/2026" in texte and "Éligible" in texte
    assert item.find_all(lambda n: n.tag == "a")[0].attrs["href"] == fiche(ref.C1)
    root = parse(client.get(fiche(ref.S1)))
    assert [n.attrs["data-autre-candidature"] for n in by_attr(root, "data-autre-candidature")] == [
        str(ref.S6.pk)
    ]
    root = parse(client.get(fiche(ref.S4)))
    assert "Aucune autre candidature" in by_attr(root, "data-autres-candidatures")[0].text()


def test_ca46_fin_de_conservation(client, dir1, ref):
    root = parse(get(client, dir1, fiche(ref.S1)))
    assert (
        by_attr(root, "data-fin-conservation")[0].text() == "Données conservées jusqu’au 02/11/2028"
    )
    with override_settings(RETENTION_CANDIDATURE_SPONTANEE_MOIS=12):
        root = parse(client.get(fiche(ref.S1)))
    assert (
        by_attr(root, "data-fin-conservation")[0].text() == "Données conservées jusqu’au 02/11/2027"
    )


def test_ca46_conservation_depuis_la_mise_a_jour_source(ref):
    DetailsSpontanee.objects.filter(candidature=ref.S1).update(
        mise_a_jour_source_le=at(2027, 1, 15)
    )
    s1 = Candidature.objects.select_related("details_spontanee").get(pk=ref.S1.pk)
    assert service.fin_conservation(s1) == date(2029, 1, 15)


def test_ca47_requetes_bornees(client, dir1, ref):
    s1 = ref.S1
    client.force_login(dir1)
    # Petit : 1 pièce, 1 anomalie, 1 décision, 1 autre candidature (S6).
    s1.pieces.exclude(type="cv").delete()
    Anomalie.objects.create(candidature=s1, type="autre", gravite="info", description="fictive")
    Decision.objects.create(candidature=s1, auteur=dir1, decision=S.VIVIER, motif=MOTIF)
    petit = nb_queries(client, fiche(s1))
    for i in range(29):
        add_piece(s1, "autre", key=f"k{i}")
        Anomalie.objects.create(candidature=s1, type="autre", gravite="info", description="fictive")
    for _ in range(19):
        Decision.objects.create(candidature=s1, auteur=dir1, decision=S.VIVIER, motif=MOTIF)
    for i in range(24):
        spontanee(ref.salma, at(2025, 1, 1 + i))
    grand = nb_queries(client, fiche(s1))
    assert abs(grand - petit) <= 1  # constante : comptage des autres candidatures au-delà de 20
    root = parse(client.get(fiche(s1)))
    assert len(by_attr(root, "data-autre-candidature")) == 20
    assert by_attr(root, "data-autres-total")[0].text() == "20 plus récentes affichées sur 25"


def test_ca48_message_echappe(client, dir1, ref):
    DetailsSpontanee.objects.filter(candidature=ref.S1).update(message="<script>alert(1)</script>")
    html = get(client, dir1, fiche(ref.S1)).content.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "alert(1)" not in client.get(LISTE).content.decode()


# --------------------------------------------------------------------------------------
# Décisions (CA49 à CA56)
# --------------------------------------------------------------------------------------


def poster(client, c, decision, motif=MOTIF, **extra_data):
    c.refresh_from_db()
    last = c.decisions.order_by("-created_at", "-id").first()
    data = {
        "decision": decision,
        "motif": motif,
        "statut_attendu": c.statut,
        "derniere_decision_id": last.pk if last else "",
        **extra_data,
    }
    return client.post(reverse("recruitment:decision_enregistrer", args=[c.pk]), data)


def test_ca49_conserver_au_vivier(client, dir1, ref):
    client.force_login(dir1)
    response = poster(client, ref.S1, S.VIVIER)
    assert response.status_code == 302
    ref.S1.refresh_from_db()
    assert ref.S1.statut == S.VIVIER
    d = ref.S1.decisions.get()
    assert d.decision == S.VIVIER and d.auteur == dir1
    entree = JournalAudit.objects.filter(
        action=AuditAction.DECISION, object_id=str(ref.S1.pk)
    ).get()
    assert entree.before == {"statut": "recue"} and entree.after == {"statut": "vivier"}
    assert entree.metadata["motif"] == MOTIF and entree.metadata["type_candidature"] == "spontanee"
    assert "HAMDI" not in entree.object_repr


def choix(root):
    (sel,) = root.find_all(lambda n: n.tag == "select" and n.attrs.get("name") == "decision")
    return [o.text() for o in sel.children if o.tag == "option" and o.attrs.get("value")]


def test_ca50_decisions_proposees(client, dir1, ref):
    client.force_login(dir1)
    assert choix(parse(client.get(fiche(ref.S1)))) == ["Conservée au vivier", "Rejetée"]
    assert choix(parse(client.get(fiche(ref.S2)))) == ["Rejetée"]
    assert choix(parse(client.get(fiche(ref.S3)))) == ["Conservée au vivier"]
    # Fiche de concours : jamais « vivier » (table KAN-13 inchangée).
    assert "Conservée au vivier" not in choix(parse(client.get(fiche(ref.C1))))


PERMIS = {
    ("recue", "vivier"), ("recue", "rejetee"), ("en_analyse", "vivier"),
    ("en_analyse", "rejetee"), ("vivier", "rejetee"), ("rejetee", "vivier"),
}  # fmt: skip


@pytest.mark.parametrize("statut", Candidature.STATUTS_SPONTANEE)
@pytest.mark.parametrize("decision", S.values)
def test_ca51_table_des_transitions(client, dir1, ref, statut, decision):
    s1 = ref.S1
    Candidature.objects.filter(pk=s1.pk).update(statut=statut)
    s1.refresh_from_db()
    if (statut, decision) in PERMIS:
        record_decision(s1, dir1, decision, MOTIF, confirmer_revision=True)
        assert Candidature.objects.get(pk=s1.pk).statut == decision
        return
    with pytest.raises(DecisionError) as exc:
        record_decision(s1, dir1, decision, MOTIF, confirmer_revision=True)
    attendu = "deja_dans_ce_statut" if statut == decision else "transition_interdite"
    assert exc.value.code == attendu
    assert not s1.decisions.exists()
    client.force_login(dir1)
    response = poster(client, s1, decision, confirmer_revision="1")
    assert response.status_code == 400
    assert not s1.decisions.exists() and Candidature.objects.get(pk=s1.pk).statut == statut
    refus = JournalAudit.objects.filter(action=AuditAction.UPDATE, metadata__refus=attendu)
    assert refus.exists() == (attendu == "transition_interdite")


@pytest.mark.parametrize("cle", ["C1", "O1"])
def test_ca52_vivier_refuse_pour_concours_et_offre(client, dir1, ref, cle):
    c = ref[cle]
    avant = c.statut
    with pytest.raises(DecisionError):
        record_decision(c, dir1, S.VIVIER, MOTIF)
    client.force_login(dir1)
    assert poster(client, c, S.VIVIER).status_code == 400
    assert not c.decisions.exists() and Candidature.objects.get(pk=c.pk).statut == avant


def test_ca53_revision_rejetee_vers_vivier(dir1, ref):
    s3, d0 = ref.S3, ref.D0
    with pytest.raises(ConfirmationRequise) as exc:
        record_decision(s3, dir1, S.VIVIER, MOTIF)
    assert exc.value.revision and exc.value.decision_remplacee_id == d0.pk
    assert s3.decisions.count() == 1
    d = record_decision(s3, dir1, S.VIVIER, MOTIF, confirmer_revision=True)
    assert Decision.objects.get(pk=d0.pk).decision == S.REJETEE
    entree = JournalAudit.objects.filter(
        action=AuditAction.DECISION, metadata__decision_id=d.pk
    ).get()
    assert entree.metadata["revision"] is True
    assert entree.metadata["decision_remplacee_id"] == d0.pk


def test_ca53_revision_demandee_dans_la_fiche(client, dir1, ref):
    client.force_login(dir1)
    response = poster(client, ref.S3, S.VIVIER)
    assert response.status_code == 200 and "confirmer_revision" in response.content.decode()
    assert ref.S3.decisions.count() == 1


def test_ca54_refus_kan13(client, dir1, cons1, ref):
    s1 = ref.S1
    client.force_login(dir1)
    assert poster(client, s1, S.VIVIER, motif="court").status_code == 400
    client.force_login(cons1)
    assert poster(client, s1, S.VIVIER).status_code == 403
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(dir1)
    assert poster(csrf, s1, S.VIVIER).status_code == 403
    client.force_login(dir1)
    response = poster(client, s1, S.VIVIER, statut_attendu="en_analyse")
    assert response.status_code == 409
    assert not s1.decisions.exists() and Candidature.objects.get(pk=s1.pk).statut == S.RECUE


def test_ca55_pas_d_avertissement_anomalie(dir1, ref):
    record_decision(ref.S5, dir1, S.VIVIER, MOTIF)
    assert Candidature.objects.get(pk=ref.S5.pk).statut == S.VIVIER


def test_ca56_lot_refuse_une_spontanee(client, dir1, ref):
    p1 = ref.p1
    client.force_login(dir1)
    response = client.post(
        reverse("recruitment:lot_recapitulatif", args=[p1.concours_id, p1.pk]),
        {"action": "eligible", "mode": "ids", "ids": [str(ref.S1.pk)]},
    )
    assert response.status_code == 400
    assert Candidature.objects.get(pk=ref.S1.pk).statut == S.RECUE
    assert not ref.S1.decisions.exists()


# --------------------------------------------------------------------------------------
# Audit, tableau de bord, conformité, interface (CA57 à CA63)
# --------------------------------------------------------------------------------------


def test_ca57_audit_de_la_liste(client, dir1, cons1, ref):
    url = liste_url(statut="recue", q="hamdi")
    get(client, dir1, url)
    client.get(url, **HX)
    vues = JournalAudit.objects.filter(action=AuditAction.VIEW, metadata__vue="spontanees_liste")
    assert vues.count() == 2
    for e in vues:
        assert e.content_type is None and e.object_id == ""
        assert e.object_repr == "Liste : candidatures spontanées"
        m = e.metadata
        assert m["statuts"] == ["recue"] and m["recherche"] == "hamdi"
        assert m["tri"] == "-date" and m["page"] == 1 and m["taille"] == 50
        assert m["nb_resultats"] == 2
    # 403, 405 et redirection : aucune entrée « view ».
    client.post(LISTE)
    get(client, cons1, LISTE)
    client.logout()
    client.get(LISTE)
    assert vues.count() == 2


def test_ca58_audit_de_la_fiche_sans_nom(client, dir1, ref):
    get(client, dir1, fiche(ref.S1))
    e = JournalAudit.objects.filter(action=AuditAction.VIEW, metadata__vue="fiche").get()
    assert e.metadata["candidature_id"] == ref.S1.pk
    assert e.content_type == ContentType.objects.get_for_model(Candidature)
    texte = e.object_repr + json.dumps(e.metadata, ensure_ascii=False)
    c = ref.salma
    for valeur in ("HAMDI", "Hamdi", "Salma", c.email, c.telephone, c.cin):
        assert valeur not in texte


def test_ca59_compteurs_du_tableau_de_bord(ref):
    with CaptureQueriesContext(connection) as ctx:
        c = service.compteurs_spontanees(reference=at(2026, 11, 25, 12))
    assert len(ctx.captured_queries) == 1
    assert (c.total, c.a_traiter, c.vivier, c.rejetees) == (6, 4, 1, 1)
    assert c.recues_30_jours == 3  # S1, S4, S5


def test_ca59_tableau_de_bord(client, dir1, ref):
    root = parse(get(client, dir1, reverse("dashboard")))
    (section,) = by_attr(root, "data-section", "spontanees")
    valeurs = {
        n.attrs["data-compteur-spontanees"]: n.find_all(lambda x: x.tag == "dd")[0].text()
        for n in by_attr(section, "data-compteur-spontanees")
    }
    assert valeurs["total"] == "6" and valeurs["a_traiter"] == "4"
    assert valeurs["vivier"] == "1" and valeurs["rejetees"] == "1"
    (lien,) = by_attr(section, "data-toutes-spontanees")
    assert lien.attrs["href"] == LISTE
    (ventilation,) = by_attr(root, "data-ventilation-spontanees")
    assert (
        ventilation.text() == "Candidatures spontanées : 6" and ventilation.attrs["href"] == LISTE
    )
    (tuile,) = by_attr(root, "data-stat-candidatures")
    assert "8" in tuile.find_all(lambda n: "stat-value" in n.attrs.get("class", ""))[0].text()


def test_ca59_tableau_de_bord_requetes_constantes(client, dir1, ref):
    client.force_login(dir1)
    avant = nb_queries(client, reverse("dashboard"))
    _bulk_spontanees(50)
    assert nb_queries(client, reverse("dashboard")) == avant


def test_ca60_conservation_depassee(ref):
    n = (Candidature.objects.count(), DetailsSpontanee.objects.count())
    qs = service.conservation_depassee(mois=3, reference=at(2026, 12, 15))
    assert list(qs) == [ref.S6, ref.S3]
    assert (Candidature.objects.count(), DetailsSpontanee.objects.count()) == n


def test_ca61_filtre_statut_du_poste_sans_vivier(client, dir1, ref):
    p1 = ref.p1
    root = parse(
        get(client, dir1, reverse("recruitment:poste_candidatures", args=[p1.concours_id, p1.pk]))
    )
    valeurs = {n.attrs["value"] for n in inputs(root, "statut")}
    assert "vivier" not in valeurs and len(valeurs) == 9
    assert ref.S1.pk not in row_ids(root)


def test_exports_kan37_hors_perimetre(ref):
    assert list(candidatures_concours(ref.concours)) == [ref.C1]
    contenu, nb_lignes, _ = classeur_concours(ref.concours)
    assert nb_lignes == 1
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(contenu))
    entetes = [c.value for c in wb.worksheets[0][1]]
    assert not any("vivier" in str(v) for v in entetes)


def test_ca62_rtl(client, dir1, ref):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    for url in (LISTE, fiche(ref.S1)):
        response = get(client, dir1, url, HTTP_ACCEPT_LANGUAGE="ar")
        assert response.status_code == 200
        (html,) = parse(response).find_all(lambda n: n.tag == "html")
        assert html.attrs.get("dir") == "rtl"


def test_ca62_pas_de_classes_directionnelles():
    pattern = re.compile(
        r"(?<![\w-])(?:text-(?:left|right)|float-(?:left|right)|-?m[lr]-[\w.]+|p[lr]-[\w.]+"
        r"|-?(?:left|right)-[\w.]+|border-[lr](?:-[\w.]+)?|rounded-[lr](?:-[\w.]+)?)(?![\w-])"
    )
    offenders = []
    for path in GABARITS_KAN39:
        for cls_attr in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            offenders += [(path.name, m.group(0)) for m in pattern.finditer(cls_attr)]
    assert not offenders, offenders


def test_ca62_libelles_dans_le_catalogue():
    msgids = po_msgids()
    missing = []
    for path in GABARITS_KAN39:
        source = path.read_text(encoding="utf-8")
        for _, s in re.findall(r"""\{%\s*(?:translate|trans)\s+(["'])(.*?)\1""", source):
            if s not in msgids:
                missing.append(s)
        for body in re.findall(
            r"\{%\s*blocktranslate[^%]*%\}(.*?)\{%\s*endblocktranslate", source, re.S
        ):
            for part in body.split("{% plural %}"):
                msgid = re.sub(r"\{\{\s*(\w+)\s*\}\}", r"%(\1)s", part)
                if msgid not in msgids:
                    missing.append(msgid)
    for label in ("Conservée au vivier", "Candidature spontanée", "Non précisée", "Sans CV"):
        if label not in msgids:
            missing.append(label)
    assert not missing, missing


def test_ca63_accessibilite(client, dir1, ref):
    root = parse(get(client, dir1, LISTE))
    (table,) = by_attr(root, "data-table-spontanees")
    assert table.find_all(lambda n: n.tag == "caption")
    ths = table.find_all(lambda n: n.tag == "th")
    assert ths and all(th.attrs.get("scope") == "col" for th in ths)
    tries = [th for th in ths if "aria-sort" in th.attrs]
    assert len(tries) == 1 and tries[0].attrs["aria-sort"] == "descending"
    (form,) = by_attr(root, "data-filtres-spontanees")
    fieldsets = form.find_all(lambda n: n.tag == "fieldset")
    assert len(fieldsets) == 7
    assert all(fs.find_all(lambda n: n.tag == "legend") for fs in fieldsets)
    (zone,) = by_attr(root, "id", "resultats")
    assert zone.attrs.get("aria-live") == "polite"
    s1 = row(root, ref.S1)
    assert cell(s1, "statut").text() == "Reçue"
    assert by_attr(cell(s1, "pieces"), "data-cv", "oui")[0].text() == "CV"


def test_badge_vivier_dans_la_charte():
    css = (BASE_DIR / "frontend" / "app.css").read_text(encoding="utf-8")
    assert ".badge-vivier" in css
    assert ".badge-vivier" in (BASE_DIR / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "badge-vivier" in (BASE_DIR / "docs" / "design" / "charte.md").read_text(
        encoding="utf-8"
    )
