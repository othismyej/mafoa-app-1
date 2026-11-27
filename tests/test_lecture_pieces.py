"""Tests d'acceptation de KAN-41 — Lecture des pièces par l'IA et éligibilité sur pièces.

Fondés sur ``docs/specs/KAN-41-lecture-pieces-ia.md`` (CA1 à CA25). Données fictives
uniquement (domaine example.com, CIN ZZ…). L'IA est **toujours simulée** (transport factice
injecté dans ``AIClient``) ; le garde-fou de ``conftest.py`` fait échouer tout appel réel.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from ai.client import AIClient, AIError, AINotConfigured, AIRefusal
from ai.client import anthropic_transport as transport_reel  # importé avant le garde-fou
from ai.models import AppelIA
from ai.schemas import (
    ExperienceLue,
    FormationLue,
    LectureAttestation,
    LectureCV,
    LectureDiplome,
    PeriodeAttestee,
)
from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.documents import lecture
from apps.documents.models import Extraction
from apps.documents.services import (
    ecarts_declare_pieces,
    extractions_de,
    profil_extrait,
    schema_pour,
)
from apps.evaluation.eligibility.levels import NiveauEtudes
from apps.evaluation.models import Anomalie, Decision, VerificationEligibilite
from apps.evaluation.services import run_eligibility
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    OffreEmploi,
    Organisation,
    Piece,
    PosteConcours,
)
from integrations.site_source.fixture_source import FixtureSource
from integrations.site_source.sync import sync_from_source

from .conftest import make_user

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
ROOT = Path(__file__).resolve().parent.parent
SECRETS = ("ALAOUI", "Salma", "ZZ100037", "salma.alaoui@example.com", "06 11 22 33 44")


# --------------------------------------------------------------------------------------
# Documents fictifs
# --------------------------------------------------------------------------------------


def pdf_texte(*lignes: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    y = 800
    c.setFont("Helvetica", 11)
    for ligne in ("DOCUMENT FICTIF — GÉNÉRÉ POUR LES TESTS", *lignes):
        c.drawString(60, y, ligne)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def pdf_scan(pages: int = 1) -> bytes:
    """PDF sans couche texte (simule un scan)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for _ in range(pages):
        c.rect(50, 50, 400, 600, fill=1)
        c.showPage()
    c.save()
    return buf.getvalue()


def image(fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, format=fmt)
    return buf.getvalue()


CV_PDF = pdf_texte(
    "CURRICULUM VITAE",
    "Salma ALAOUI",
    "salma.alaoui@example.com — 06 11 22 33 44 — CIN ZZ100037 — née le 14/03/1990",
    "FORMATION",
    "2015 : Master en Informatique — École Fictive d'Ingénieurs",
    "EXPÉRIENCE PROFESSIONNELLE",
    "2018-01-01 → aujourd'hui : Ingénieure — Société Fictive des Eaux",
)
DIPLOME_PDF = pdf_texte(
    "ÉCOLE FICTIVE D'INGÉNIEURS",
    "DIPLÔME",
    "Décerné à Salma ALAOUI, née le 14/03/1990",
    "Intitulé : Master en Informatique — Niveau : Bac+5 — Année : 2015",
)
ATTESTATION_PDF = pdf_texte(
    "ATTESTATION DE TRAVAIL",
    "Société Fictive des Eaux atteste que Salma ALAOUI, CIN ZZ100037,",
    "occupe le poste d'Ingénieure depuis le 2018-01-01 à ce jour.",
)
LETTRE_PDF = pdf_texte("LETTRE DE MOTIVATION", "Salma ALAOUI", "Madame, Monsieur, …")


# --------------------------------------------------------------------------------------
# Doubles de test : source et IA simulées
# --------------------------------------------------------------------------------------


class FakeSource:
    name = "fake"

    def __init__(self):
        self.contents: dict[str, bytes | Exception] = {}
        self.reads: list[str] = []

    def read_piece(self, storage_key, *, max_bytes=None, timeout=None):
        self.reads.append(storage_key)
        value = self.contents.get(storage_key)
        if value is None:
            raise FileNotFoundError(storage_key)
        if isinstance(value, Exception):
            raise value
        return value


def cv_conforme(confiance=0.95, **kw) -> LectureCV:
    data = {
        "nom_titulaire": "[NOM_2] [NOM_1]",
        "formations": [
            FormationLue(
                intitule="Master en Informatique",
                niveau="Bac+5",
                specialite="Informatique",
                etablissement="École Fictive d'Ingénieurs",
                annee_obtention=2015,
                extrait="2015 : Master en Informatique",
            )
        ],
        "experiences": [
            ExperienceLue(
                employeur="Société Fictive des Eaux",
                poste="Ingénieure",
                debut="2018-01-01",
                fin=None,
                en_cours=True,
                extrait="2018-01-01 → aujourd'hui",
            )
        ],
        "competences": ["Hydraulique"],
        "confiance": confiance,
    }
    data.update(kw)
    return LectureCV(**data)


def diplome_lu(niveau="Bac+5", specialite="Informatique", confiance=0.95) -> LectureDiplome:
    return LectureDiplome(
        nom_titulaire="[NOM_2] [NOM_1]",
        intitule=f"Master en {specialite}" if niveau == "Bac+5" else f"Licence en {specialite}",
        niveau=niveau,
        specialite=specialite,
        etablissement="École Fictive d'Ingénieurs",
        annee_obtention=2015,
        extrait=f"Niveau : {niveau}",
        confiance=confiance,
    )


def attestation_lue(debut="2018-01-01", fin=None, confiance=0.95) -> LectureAttestation:
    return LectureAttestation(
        nom_titulaire="[NOM_2] [NOM_1]",
        employeur="Société Fictive des Eaux",
        periodes=[
            PeriodeAttestee(
                poste="Ingénieure", debut=debut, fin=fin, en_cours=fin is None, extrait="depuis"
            )
        ],
        confiance=confiance,
    )


class FakeTransport:
    """Simule l'API Claude : aucun appel réseau. Réponse par schéma : instance ou exception."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reponses: dict = {
            LectureCV: cv_conforme(),
            LectureDiplome: diplome_lu(),
            LectureAttestation: attestation_lue(),
        }

    def __call__(self, model, system, user_text, schema, attachments=None):
        self.calls.append(
            {
                "model": model,
                "system": system,
                "user_text": user_text,
                "schema": schema,
                "attachments": attachments,
            }
        )
        r = self.reponses[schema]
        if isinstance(r, Exception):
            raise r
        return r, {"input_tokens": 1234, "output_tokens": 321}


@pytest.fixture
def ia(settings, monkeypatch):
    """IA « configurée » avec un transport simulé (la clé factice n'est jamais envoyée)."""
    settings.ANTHROPIC_API_KEY = "cle-factice-pour-tests"
    settings.AI_PSEUDONYMIZE = True
    settings.AI_VISION_NON_PSEUDONYMISEE = False
    transport = FakeTransport()
    monkeypatch.setattr(
        lecture, "get_client", lambda: AIClient(transport=transport, model="modele-test")
    )
    return transport


@pytest.fixture
def src(monkeypatch):
    source = FakeSource()
    monkeypatch.setattr("integrations.site_source.base.get_source", lambda *a, **k: source)
    return source


# --------------------------------------------------------------------------------------
# Jeu de données
# --------------------------------------------------------------------------------------


def at(*args) -> datetime:
    return timezone.make_aware(datetime(*args))


@pytest.fixture
def poste(db):
    org = Organisation.objects.create(nom="Agence Fictive Atlas", type=Organisation.Type.PUBLIC)
    concours = Concours.objects.create(
        organisation=org, intitule="Concours fictif Atlas 2026", date_limite=date(2026, 6, 30)
    )
    return PosteConcours.objects.create(
        concours=concours,
        intitule="Ingénieur fictif",
        niveau_etudes_min=NiveauEtudes.BAC_5,
        specialites_acceptees=["Informatique"],
        experience_min_annees=Decimal("3"),
    )


DECLARE = {
    "formation": [
        {
            "intitule": "Master en Informatique",
            "niveau": "Bac+5",
            "specialite": "Informatique",
            "etablissement": "École Fictive d'Ingénieurs",
            "annee_obtention": 2015,
        }
    ],
    "experiences": [
        {"employeur": "Société Fictive des Eaux", "poste": "Ingénieure", "debut": "2018-01-01"}
    ],
}


def new_cand(poste, declare=None, **kw) -> Candidature:
    candidat = Candidat.objects.create(
        nom=kw.pop("nom", "ALAOUI"),
        prenom=kw.pop("prenom", "Salma"),
        cin="ZZ100037",
        email="salma.alaoui@example.com",
        telephone="06 11 22 33 44",
        date_naissance=date(1990, 3, 14),
    )
    return Candidature.objects.create(
        candidat=candidat,
        poste_concours=poste,
        date_candidature=at(2026, 3, 15, 10, 30),
        statut=kw.pop("statut", Candidature.Statut.RECUE),
        donnees_declarees=declare if declare is not None else DECLARE,
        **kw,
    )


@pytest.fixture
def cand(poste):
    return new_cand(poste)


def add_piece(src, candidature, type_, content, *, key=None, **kw) -> Piece:
    key = key or f"fake://{candidature.pk}/{type_}-{Piece.objects.count()}"
    if content is not None:
        src.contents[key] = content
    data = {"content_type": "application/pdf", "sha256": "", "taille": None}
    data.update(kw)
    return Piece.objects.create(
        candidature=candidature,
        type=type_,
        nom_fichier=f"{type_}_alaoui.pdf",
        storage_key=key,
        **data,
    )


@pytest.fixture
def dossier(src, cand):
    """Candidature avec CV, diplôme, attestation et lettre (PDF texte)."""
    return {
        "cand": cand,
        "cv": add_piece(src, cand, Piece.Type.CV, CV_PDF),
        "diplome": add_piece(src, cand, Piece.Type.DIPLOME, DIPLOME_PDF),
        "attestation": add_piece(src, cand, Piece.Type.ATTESTATION_TRAVAIL, ATTESTATION_PDF),
        "lettre": add_piece(src, cand, Piece.Type.LETTRE, LETTRE_PDF),
    }


def lire(pieces, *, force=False, user=None, capture=None):
    """Met en file puis exécute (tâches Celery « eager » après validation)."""
    with capture(execute=True):
        return lecture.mettre_en_file(list(pieces), force=force, user=user)


def lire_url(c) -> str:
    return reverse("recruitment:lecture_pieces_lancer", args=[c.pk])


def etat_url(c) -> str:
    return reverse("recruitment:lecture_pieces_etat", args=[c.pk])


def fiche_url(c) -> str:
    return reverse("recruitment:candidature_detail", args=[c.pk])


def terminee(piece, donnees, confiance=0.95, **kw) -> Extraction:
    """Extraction terminée créée directement (tests du moteur, sans IA)."""
    data = {
        "piece": piece,
        "type_piece": piece.type,
        "statut": Extraction.Statut.TERMINEE,
        "methode": Extraction.Methode.PDF_TEXTE,
        "donnees": donnees.model_dump(mode="json"),
        "confiance": confiance,
        "prompt_nom": lecture.PROMPTS[piece.type][0],
        "prompt_version": "1",
        "empreinte": piece.sha256,
        "terminee_le": timezone.now(),
    }
    data.update(kw)
    return Extraction.objects.create(**data)


def ai_entries():
    return JournalAudit.objects.filter(action=AuditAction.AI_CALL)


# --------------------------------------------------------------------------------------
# CA1 à CA5 — pipeline, schémas, vision, validation
# --------------------------------------------------------------------------------------


def test_ca1_cv_pdf_texte_fixture_source(ia, monkeypatch, django_capture_on_commit_callbacks):
    """CV PDF texte réel de la FixtureSource : extraction complète et entrée pseudonymisée."""
    fixture = FixtureSource(seed=4141, nb_candidats=12)
    sync_from_source(fixture)  # avant la mise en file : rien n'est encore lu
    monkeypatch.setattr("integrations.site_source.base.get_source", lambda *a, **k: fixture)
    piece = Piece.objects.filter(type=Piece.Type.CV).select_related("candidature__candidat").first()
    candidat = piece.candidature.candidat
    Extraction.objects.all().delete()  # lectures mises en file par la synchronisation

    lire([piece], capture=django_capture_on_commit_callbacks)

    e = Extraction.objects.get(piece=piece)
    assert e.statut == Extraction.Statut.TERMINEE
    assert e.methode == Extraction.Methode.PDF_TEXTE
    assert "CURRICULUM VITAE" in e.texte
    assert LectureCV.model_validate(e.donnees).formations
    assert e.confiance == pytest.approx(0.95)
    assert (e.modele, e.prompt_nom, e.prompt_version) == ("modele-test", "lecture_cv", "1")
    assert e.empreinte == piece.sha256 and e.appel_ia is not None
    envoye = ia.calls[0]["user_text"]
    for secret in (candidat.nom, candidat.prenom, candidat.cin, candidat.email):
        assert secret not in envoye
    assert "[NOM_" in envoye and "[EMAIL_1]" in envoye
    assert f"{candidat.date_naissance:%d/%m/%Y}" not in envoye  # date de naissance masquée


def test_ca1_entree_pseudonymisee_et_restauration(ia, dossier, django_capture_on_commit_callbacks):
    lire([dossier["cv"]], capture=django_capture_on_commit_callbacks)
    e = Extraction.objects.get(piece=dossier["cv"])
    envoye = ia.calls[0]["user_text"]
    for secret in (*SECRETS, "14/03/1990"):
        assert secret not in envoye
    # Le nom reçu en jetons est ré-identifié côté serveur.
    assert set(e.donnees["nom_titulaire"].split()) == {"ALAOUI", "Salma"}
    assert ia.calls[0]["attachments"] is None


def test_ca2_prompts_et_schemas_par_type(ia, dossier, django_capture_on_commit_callbacks):
    pieces = [dossier[k] for k in ("cv", "diplome", "attestation", "lettre")]
    rapport = lire(pieces, capture=django_capture_on_commit_callbacks)
    assert dossier["lettre"].pk not in rapport.en_file
    assert not Extraction.objects.filter(piece=dossier["lettre"]).exists()
    schemas = {c["schema"] for c in ia.calls}
    assert schemas == {LectureCV, LectureDiplome, LectureAttestation}
    prompts = set(Extraction.objects.values_list("prompt_nom", flat=True))
    assert prompts == {"lecture_cv", "lecture_diplome", "lecture_attestation"}
    systems = {c["schema"]: c["system"] for c in ia.calls}
    assert "diplôme" in systems[LectureDiplome] and "attestation" in systems[LectureAttestation]
    assert schema_pour(Piece.Type.DIPLOME) is LectureDiplome


@pytest.mark.parametrize(
    "content,media_type,bloc",
    [
        (image("PNG"), "image/png", "image"),
        (image("JPEG"), "image/jpeg", "image"),
        (pdf_scan(), "application/pdf", "document"),
    ],
)
def test_ca3_vision_autorisee(
    ia, src, cand, settings, content, media_type, bloc, django_capture_on_commit_callbacks
):
    settings.AI_VISION_NON_PSEUDONYMISEE = True
    p = add_piece(src, cand, Piece.Type.DIPLOME, content, content_type=media_type)
    lire([p], capture=django_capture_on_commit_callbacks)
    e = Extraction.objects.get(piece=p)
    assert e.statut == Extraction.Statut.TERMINEE and e.methode == Extraction.Methode.VISION
    jointes = ia.calls[0]["attachments"]
    assert jointes[0]["media_type"] == media_type and jointes[0]["data"] == content
    appel = e.appel_ia
    assert appel.pseudonymise is False
    assert appel.entree["pieces_jointes"][0]["taille"] == len(content)
    assert "data" not in appel.entree["pieces_jointes"][0]  # jamais le contenu de la pièce
    # Le bloc envoyé à l'API serait du bon type (construction du message).
    from ai.client import _content_blocks

    assert _content_blocks("x", jointes)[0]["type"] == bloc


def test_ca4_vision_non_autorisee_avec_pseudonymisation(
    ia, src, cand, client, admin_user, django_capture_on_commit_callbacks
):
    p = add_piece(src, cand, Piece.Type.DIPLOME, pdf_scan())
    lire([p], capture=django_capture_on_commit_callbacks)
    e = Extraction.objects.get(piece=p)
    assert (e.statut, e.code) == (Extraction.Statut.IGNOREE, "vision_non_autorisee")
    assert ia.calls == [] and not AppelIA.objects.exists()
    client.force_login(admin_user)
    html = client.get(fiche_url(cand)).content.decode()
    assert "pseudonymisation impossible" in html


class AvecSexe(BaseModel):
    """Réponse simulée contenant un champ sensible non prévu par le schéma."""

    intitule: str = "Master en Informatique"
    confiance: float = 0.9
    sexe: str = "F"


@pytest.mark.parametrize(
    "reponse,code",
    [
        (LectureDiplome.model_construct(intitule="x", confiance=1.7), "sortie_invalide"),
        (LectureDiplome.model_construct(intitule="x"), "sortie_invalide"),  # confiance absente
        (AvecSexe(), "sortie_invalide"),
        (AIRefusal("refus"), "refus"),
        (AIError("panne", code="ia_indisponible"), "ia_indisponible"),
    ],
)
def test_ca5_sortie_invalide_ou_refus(
    ia, dossier, reponse, code, django_capture_on_commit_callbacks
):
    ia.reponses[LectureDiplome] = reponse
    lire([dossier["diplome"]], capture=django_capture_on_commit_callbacks)
    e = Extraction.objects.get(piece=dossier["diplome"])
    assert (e.statut, e.code) == (Extraction.Statut.ECHEC, code)
    assert e.donnees == {} and e.confiance is None
    assert e.appel_ia is not None and e.appel_ia.statut == AppelIA.Statut.ERREUR
    assert not VerificationEligibilite.objects.exists()  # aucune lecture terminée


def test_ca5_libelles_motifs(ia, dossier, client, admin_user, django_capture_on_commit_callbacks):
    ia.reponses[LectureDiplome] = AIRefusal("refus")
    lire([dossier["diplome"]], capture=django_capture_on_commit_callbacks)
    client.force_login(admin_user)
    html = client.get(fiche_url(dossier["cand"])).content.decode()
    assert "Lecture refusée par l’IA" in html


def test_journal_appel_ia(ia, dossier, django_capture_on_commit_callbacks):
    """Règle 15 : modèle, prompt, version, entrée pseudonymisée, sortie, durée, jetons."""
    lire([dossier["attestation"]], capture=django_capture_on_commit_callbacks)
    appel = AppelIA.objects.get()
    assert appel.tache == "lecture_attestation" and appel.modele == "modele-test"
    assert (appel.prompt_nom, appel.prompt_version) == ("lecture_attestation", "1")
    assert appel.pseudonymise is True and appel.statut == AppelIA.Statut.SUCCES
    assert appel.tokens_entree == 1234 and appel.tokens_sortie == 321
    assert appel.duree_ms >= 0 and appel.sortie["employeur"] == "Société Fictive des Eaux"
    assert not any(s in str(appel.entree) for s in SECRETS)


# --------------------------------------------------------------------------------------
# CA6 à CA10 — idempotence, versions, clé absente, limites, erreurs de source
# --------------------------------------------------------------------------------------


def test_ca6_idempotence_et_relire(ia, dossier, django_capture_on_commit_callbacks):
    cv = dossier["cv"]
    lire([cv], capture=django_capture_on_commit_callbacks)
    assert len(ia.calls) == 1
    # Empreinte inconnue de la pièce : la vérification se fait dans la tâche, sans appel.
    lire([cv], capture=django_capture_on_commit_callbacks)
    assert len(ia.calls) == 1 and Extraction.objects.filter(piece=cv).count() == 1
    # Empreinte enregistrée : la pièce n'est même pas remise en file.
    cv.sha256 = Extraction.objects.get(piece=cv).empreinte
    cv.save()
    rapport = lire([cv], capture=django_capture_on_commit_callbacks)
    assert rapport.deja_lues == [cv.pk] and len(ia.calls) == 1
    # « Relire » force une nouvelle lecture et conserve la précédente.
    rapport = lire([cv], force=True, capture=django_capture_on_commit_callbacks)
    assert rapport.en_file == [cv.pk] and len(ia.calls) == 2
    assert Extraction.objects.filter(piece=cv, statut=Extraction.Statut.TERMINEE).count() == 2


def test_ca7_nouvelle_version_de_prompt(ia, dossier, django_capture_on_commit_callbacks):
    cv = dossier["cv"]
    ancienne = terminee(cv, cv_conforme(), prompt_version="0")
    ancienne.empreinte = lecture.pieces_service.empreinte(CV_PDF)
    ancienne.save()
    lire([cv], capture=django_capture_on_commit_callbacks)
    assert len(ia.calls) == 1
    assert Extraction.objects.filter(piece=cv, prompt_version="1").exists()


def test_ca8_cle_absente(
    settings, src, dossier, client, direction_user, django_capture_on_commit_callbacks
):
    settings.ANTHROPIC_API_KEY = ""
    client.force_login(direction_user)
    html = client.get(fiche_url(dossier["cand"])).content.decode()
    assert "IA non configurée" in html
    assert re.search(r'value="lire"[^>]*data-lire disabled', html)
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(lire_url(dossier["cand"]), {"mode": "lire"}, **HX)
    assert r.status_code == 503 and "IA non configurée" in r.content.decode()
    assert not Extraction.objects.exists() and not AppelIA.objects.exists()
    with pytest.raises(lecture.LectureIndisponible):
        lecture.mettre_en_file([dossier["cv"]])


def test_ca8_synchronisation_sans_cle(settings, db, django_capture_on_commit_callbacks):
    settings.ANTHROPIC_API_KEY = ""
    with django_capture_on_commit_callbacks(execute=True):
        report = sync_from_source(FixtureSource(seed=7, nb_candidats=12), compute_hashes=False)
    assert report.lecture_ia_en_file == 0 and not Extraction.objects.exists()


def test_ca8_transport_reel_sans_cle(settings):
    """Le transport réel refuse de partir sans clé : vérifié avant tout accès réseau."""
    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(AINotConfigured):
        transport_reel("modele", "système", "texte", LectureCV)


@pytest.mark.parametrize(
    "cas,code",
    [
        ("taille_enregistree", "trop_volumineuse"),
        ("taille_constatee", "trop_volumineuse"),
        ("texte_trop_long", "texte_trop_long"),
        ("trop_de_pages", "trop_de_pages"),
        ("image_trop_grosse", "image_trop_volumineuse"),
        ("format", "type_non_pris_en_charge"),
    ],
)
def test_ca9_limites(ia, src, cand, settings, cas, code, django_capture_on_commit_callbacks):
    settings.AI_VISION_NON_PSEUDONYMISEE = True
    kw = {}
    content = CV_PDF
    if cas == "taille_enregistree":
        kw["taille"] = settings.AI_PIECE_MAX_BYTES + 1
    elif cas == "taille_constatee":
        settings.AI_PIECE_MAX_BYTES = len(CV_PDF) - 1
    elif cas == "texte_trop_long":
        settings.AI_TEXTE_MAX_CARACTERES = 100
    elif cas == "trop_de_pages":
        settings.AI_PDF_MAX_PAGES = 2
        content = pdf_scan(pages=3)
    elif cas == "image_trop_grosse":
        content = image("PNG") + b"\x00" * (lecture.IMAGE_MAX_BYTES + 1)
    else:
        content = b"PK\x03\x04" + b"\x00" * 200
    p = add_piece(src, cand, Piece.Type.CV, content, **kw)
    lire([p], capture=django_capture_on_commit_callbacks)
    e = Extraction.objects.get(piece=p)
    assert (e.statut, e.code) == (Extraction.Statut.IGNOREE, code)
    assert ia.calls == [] and e.texte == ""


@pytest.mark.parametrize(
    "valeur,code", [(None, "introuvable"), (TimeoutError("lent"), "source_indisponible")]
)
def test_ca10_erreurs_source(ia, src, cand, valeur, code, django_capture_on_commit_callbacks):
    p = add_piece(src, cand, Piece.Type.CV, valeur)
    lire([p], capture=django_capture_on_commit_callbacks)
    e = Extraction.objects.get(piece=p)
    assert (e.statut, e.code) == (Extraction.Statut.ECHEC, code) and ia.calls == []


def test_ca10_empreinte_differente(ia, src, cand, django_capture_on_commit_callbacks):
    p = add_piece(src, cand, Piece.Type.DIPLOME, DIPLOME_PDF, sha256="0" * 64)
    lire([p], capture=django_capture_on_commit_callbacks)
    lire([p], force=True, capture=django_capture_on_commit_callbacks)
    assert set(Extraction.objects.values_list("code", flat=True)) == {"empreinte_differente"}
    assert ia.calls == []
    assert Anomalie.objects.filter(piece=p, type=Anomalie.Type.EMPREINTE_DIFFERENTE).count() == 1


# --------------------------------------------------------------------------------------
# CA11 à CA14 — bouton, permissions, audit, synchronisation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "direction"])
def test_ca11_bouton_lire(
    ia, dossier, client, admin_user, direction_user, role, django_capture_on_commit_callbacks
):
    user = admin_user if role == "admin" else direction_user
    client.force_login(user)
    html = client.get(fiche_url(dossier["cand"])).content.decode()
    assert "Lire les pièces" in html and "data-lecture-pieces" in html
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(lire_url(dossier["cand"]), {"mode": "lire"}, **HX)
    assert r.status_code == 200
    assert "3 pièce(s) mise(s) en lecture" in r.content.decode()
    assert len(ia.calls) == 3
    assert set(Extraction.objects.values_list("statut", flat=True)) == {"terminee"}
    assert Extraction.objects.filter(declenchee_par=user).count() == 3


def test_ca11_pas_de_doublon_en_cours(ia, dossier, client, direction_user):
    client.force_login(direction_user)
    # Sans exécution des callbacks : les lectures restent en attente.
    client.post(lire_url(dossier["cand"]), {"mode": "lire"})
    r = client.post(lire_url(dossier["cand"]), {"mode": "relire"}, follow=True)
    assert "3 lecture(s) déjà en cours" in r.content.decode()
    assert Extraction.objects.filter(statut=Extraction.Statut.EN_ATTENTE).count() == 3


def test_ca11_lecture_perdue_remplacee(ia, dossier, django_capture_on_commit_callbacks):
    cv = dossier["cv"]
    perdue = Extraction.objects.create(piece=cv, type_piece=cv.type)
    Extraction.objects.filter(pk=perdue.pk).update(created_at=timezone.now() - timedelta(hours=2))
    rapport = lire([cv], capture=django_capture_on_commit_callbacks)
    assert rapport.en_file == [cv.pk]
    perdue.refresh_from_db()
    assert (perdue.statut, perdue.code) == (Extraction.Statut.ECHEC, "lecture_interrompue")


def test_file_indisponible(ia, dossier, monkeypatch, django_capture_on_commit_callbacks):
    from apps.documents import tasks

    def panne(*a, **k):
        raise ConnectionError("broker")

    monkeypatch.setattr(tasks.lire_piece, "delay", panne)
    lire([dossier["cv"]], capture=django_capture_on_commit_callbacks)
    e = Extraction.objects.get(piece=dossier["cv"])
    assert (e.statut, e.code) == (Extraction.Statut.ECHEC, "file_indisponible")


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
def test_ca12_roles_refuses(ia, dossier, client, role):
    user = make_user(f"{role}@example.com", role)
    client.force_login(user)
    for r in (
        client.post(lire_url(dossier["cand"]), {"mode": "lire"}),
        client.get(etat_url(dossier["cand"]), **HX),
    ):
        assert r.status_code == 403
    assert JournalAudit.objects.filter(action=AuditAction.ACCESS_DENIED).count() == 2
    assert not Extraction.objects.exists() and ia.calls == []


def test_ca12_get_interdit_et_spontanee(ia, src, client, direction_user):
    client.force_login(direction_user)
    candidat = Candidat.objects.create(nom="FICTIF", prenom="Spontané")
    spontanee = Candidature.objects.create(
        candidat=candidat, type=Candidature.Type.SPONTANEE, date_candidature=at(2026, 1, 2, 9, 0)
    )
    add_piece(src, spontanee, Piece.Type.CV, CV_PDF)
    assert client.get(lire_url(spontanee)).status_code == 405
    r = client.post(lire_url(spontanee), {"mode": "lire"}, **HX)
    assert r.status_code == 400 and "non disponible" in r.content.decode()
    assert "data-lecture-pieces" not in client.get(fiche_url(spontanee)).content.decode()
    assert not Extraction.objects.exists()


def test_ca13_audit(ia, dossier, client, direction_user, django_capture_on_commit_callbacks):
    client.force_login(direction_user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(lire_url(dossier["cand"]), {"mode": "relire"}, **HX)
    lancement = ai_entries().get(metadata__operation="lecture_pieces")
    assert lancement.actor == direction_user and lancement.metadata["mode"] == "relire"
    assert sorted(lancement.metadata["pieces_en_file"]) == sorted(
        dossier[k].pk for k in ("cv", "diplome", "attestation")
    )
    lectures = ai_entries().filter(metadata__operation="lecture_piece")
    assert lectures.count() == 3
    ct = ContentType.objects.get_for_model(Extraction)
    for entry in lectures:
        assert entry.content_type == ct and entry.actor == direction_user
        assert entry.metadata["statut"] == "terminee" and entry.metadata["appel_ia_id"]
    tout = " ".join(str(e.metadata) + e.object_repr for e in ai_entries())
    assert not any(s in tout for s in (*SECRETS, "cv_alaoui.pdf"))


def test_ca14_lecture_apres_synchronisation(ia, monkeypatch, django_capture_on_commit_callbacks):
    fixture = FixtureSource(seed=4242, nb_candidats=12)
    monkeypatch.setattr("integrations.site_source.base.get_source", lambda *a, **k: fixture)
    with django_capture_on_commit_callbacks(execute=True):
        report = sync_from_source(fixture)
    lisibles = (
        Piece.objects.filter(type__in=lecture.TYPES_LUS)
        .exclude(candidature__type=Candidature.Type.SPONTANEE)
        .count()
    )
    assert report.lecture_ia_en_file == lisibles > 0
    assert Extraction.objects.filter(statut=Extraction.Statut.TERMINEE).count() == lisibles
    entry = ai_entries().filter(metadata__operation="lecture_piece").first()
    assert entry.actor is None and entry.actor_repr == lecture.ACTEUR_SYNC
    sync_entry = JournalAudit.objects.filter(action=AuditAction.SYNC).latest("id")
    assert sync_entry.metadata["lecture_ia_en_file"] == lisibles
    # Deuxième synchronisation, rien n'a changé : aucune nouvelle lecture.
    appels = len(ia.calls)
    with django_capture_on_commit_callbacks(execute=True):
        report = sync_from_source(fixture)
    assert report.lecture_ia_en_file == 0 and len(ia.calls) == appels


def test_ca14_empreinte_modifiee_relue(ia, monkeypatch, django_capture_on_commit_callbacks):
    fixture = FixtureSource(seed=4243, nb_candidats=12)
    monkeypatch.setattr("integrations.site_source.base.get_source", lambda *a, **k: fixture)
    with override_settings(ANTHROPIC_API_KEY=""):
        sync_from_source(fixture)
    piece = Piece.objects.filter(type=Piece.Type.CV).first()
    Piece.objects.filter(pk=piece.pk).update(sha256="f" * 64)  # ancienne empreinte différente
    with django_capture_on_commit_callbacks(execute=True):
        report = sync_from_source(fixture)
    assert report.pieces_nouvelles_ou_modifiees == [piece.pk]
    assert Extraction.objects.filter(piece=piece, statut=Extraction.Statut.TERMINEE).exists()


# --------------------------------------------------------------------------------------
# CA15 à CA20 — éligibilité sur pièces
# --------------------------------------------------------------------------------------


def critere(v, code):
    return next(d for d in v.details if d["critere"] == code)


def test_ca15_concordance(src, dossier):
    terminee(dossier["cv"], cv_conforme())
    terminee(dossier["diplome"], diplome_lu())
    terminee(dossier["attestation"], attestation_lue())
    v = run_eligibility(dossier["cand"])
    assert v.resultat == "eligible" and v.moteur_version == "1.1.0"
    assert v.sources[0] == {"type": "donnees_declarees"}
    assert {s["piece_id"] for s in v.sources[1:]} == {
        dossier[k].pk for k in ("cv", "diplome", "attestation")
    }
    niveau = critere(v, "niveau_etudes")
    assert niveau["source"] == "declare_et_pieces" and niveau["verdict_pieces"] == "eligible"
    assert "Confirmé par les pièces" in niveau["raison"]
    assert sorted(critere(v, "experience")["pieces"]) == sorted(
        [dossier["cv"].pk, dossier["attestation"].pk]
    )


def test_ca16_ecart_niveau_jamais_non_eligible(src, dossier):
    terminee(dossier["diplome"], diplome_lu(niveau="Bac+3"))
    v = run_eligibility(dossier["cand"])
    niveau = critere(v, "niveau_etudes")
    assert niveau["verdict_declare"] == "eligible" and niveau["verdict_pieces"] == "non_eligible"
    assert niveau["verdict"] == "a_verifier" and "Écart entre le déclaré" in niveau["raison"]
    assert v.resultat == "a_verifier"
    dossier["cand"].refresh_from_db()
    assert dossier["cand"].statut == Candidature.Statut.A_VERIFIER


def test_ca17_confiance_faible(src, dossier, settings):
    settings.AI_CONFIANCE_MIN = 0.7
    terminee(dossier["attestation"], attestation_lue(confiance=0.4), confiance=0.4)
    v = run_eligibility(dossier["cand"])
    exp = critere(v, "experience")
    assert exp["verdict"] == "a_verifier" and "Confiance faible" in exp["raison"]
    assert critere(v, "niveau_etudes")["source"] == "declare"  # non alimenté par l'attestation
    assert v.resultat == "a_verifier"


def test_ca18_non_eligible_seulement_si_declare_et_pieces(src, poste):
    insuffisant = {**DECLARE, "experiences": [{"debut": "2025-01-01", "fin": "2025-12-31"}]}
    c1 = new_cand(poste, insuffisant)
    a1 = add_piece(src, c1, Piece.Type.ATTESTATION_TRAVAIL, ATTESTATION_PDF)
    terminee(a1, attestation_lue(debut="2018-01-01"))  # pièces : éligible
    v1 = run_eligibility(c1)
    assert critere(v1, "experience")["verdict"] == "a_verifier" and v1.resultat == "a_verifier"

    c2 = new_cand(poste, insuffisant)
    a2 = add_piece(src, c2, Piece.Type.ATTESTATION_TRAVAIL, ATTESTATION_PDF)
    terminee(a2, attestation_lue(debut="2025-01-01", fin="2025-12-31"))  # concordant
    v2 = run_eligibility(c2)
    assert critere(v2, "experience")["verdict"] == "non_eligible"
    assert v2.resultat == "non_eligible"


def test_ca19_sans_extraction_courante(src, dossier):
    v0 = run_eligibility(dossier["cand"])
    assert v0.sources == [{"type": "donnees_declarees"}] and v0.moteur_version == "1.0.0"
    # Extraction en échec ou obsolète : ignorée.
    Extraction.objects.create(
        piece=dossier["diplome"], type_piece="diplome", statut=Extraction.Statut.ECHEC
    )
    terminee(dossier["cv"], cv_conforme(), empreinte="a" * 64)
    Piece.objects.filter(pk=dossier["cv"].pk).update(sha256="b" * 64)
    v = run_eligibility(dossier["cand"])
    assert v.sources == [{"type": "donnees_declarees"}] and v.moteur_version == "1.0.0"
    assert v.details == v0.details


def test_ca20_relance_apres_lecture(ia, dossier, django_capture_on_commit_callbacks):
    ia.reponses[LectureCV] = cv_conforme(formations=[])
    ia.reponses[LectureDiplome] = diplome_lu(niveau="Bac+3")
    lire(
        [dossier[k] for k in ("cv", "diplome", "attestation")],
        capture=django_capture_on_commit_callbacks,
    )
    verifs = VerificationEligibilite.objects.filter(candidature=dossier["cand"])
    assert verifs.count() == 1  # une seule relance, à la fin de la dernière lecture
    assert verifs.get().resultat == "a_verifier" and verifs.get().moteur_version == "1.1.0"
    assert not Decision.objects.exists()


def test_ca20_candidature_decidee_non_reclassee(
    ia, dossier, direction_user, django_capture_on_commit_callbacks
):
    c = dossier["cand"]
    Decision.objects.create(
        candidature=c, auteur=direction_user, decision="retenue", motif="Décision fictive motivée"
    )
    Candidature.objects.filter(pk=c.pk).update(statut=Candidature.Statut.RETENUE)
    ia.reponses[LectureDiplome] = diplome_lu(niveau="Bac+3")
    lire([dossier["diplome"]], capture=django_capture_on_commit_callbacks)
    c.refresh_from_db()
    assert c.statut == Candidature.Statut.RETENUE and Decision.objects.count() == 1
    assert VerificationEligibilite.objects.filter(candidature=c).count() == 1


def test_offre_aussi_lue(ia, src, django_capture_on_commit_callbacks):
    org = Organisation.objects.create(nom="Entreprise Fictive", type=Organisation.Type.PRIVE)
    offre = OffreEmploi.objects.create(
        organisation=org, intitule="Ingénieur fictif", niveau_etudes_min=NiveauEtudes.BAC_5
    )
    candidat = Candidat.objects.create(nom="ALAOUI", prenom="Salma")
    c = Candidature.objects.create(
        candidat=candidat,
        offre=offre,
        date_candidature=at(2026, 2, 1, 9, 0),
        donnees_declarees=DECLARE,
    )
    p = add_piece(src, c, Piece.Type.DIPLOME, DIPLOME_PDF)
    lire([p], capture=django_capture_on_commit_callbacks)
    assert VerificationEligibilite.objects.get(candidature=c).moteur_version == "1.1.0"


def test_experience_fin_inconnue_incertaine(src, dossier):
    """Une période lue sans fin ni « en cours » n'est pas comptée (à vérifier)."""
    lue = attestation_lue()
    lue.periodes[0].en_cours = False
    terminee(dossier["attestation"], lue)
    v = run_eligibility(dossier["cand"])
    exp = critere(v, "experience")
    assert exp["verdict_pieces"] == "a_verifier" and exp["verdict"] == "a_verifier"


# --------------------------------------------------------------------------------------
# CA21 à CA25 — fiche, fragment d'état, données sensibles, API, i18n / RTL
# --------------------------------------------------------------------------------------


def test_ca21_fiche(ia, dossier, client, direction_user, django_capture_on_commit_callbacks):
    ia.reponses[LectureCV] = cv_conforme(formations=[])
    ia.reponses[LectureDiplome] = diplome_lu(niveau="Bac+3", specialite="Gestion", confiance=0.5)
    lire(
        [dossier[k] for k in ("cv", "diplome", "attestation")],
        capture=django_capture_on_commit_callbacks,
    )
    client.force_login(direction_user)
    r = client.get(fiche_url(dossier["cand"]))
    html = r.content.decode()
    assert f'data-lecture-statut="{dossier["cv"].pk}" data-etat="lue"' in html
    assert 'data-confiance="faible"' in html and 'data-confiance="elevee"' in html
    assert "Master en Informatique" in html and "Licence en Gestion" in html
    assert "data-extrait" in html and "2018-01-01 → aujourd&#x27;hui" in html
    assert 'data-ecart="niveau_etudes"' in html and 'data-ecart="specialite"' in html
    assert f"Pièce n° {dossier['diplome'].pk} (Diplôme)" in html  # sources de l'éligibilité
    assert "à vérifier sur la pièce" in html
    assert JournalAudit.objects.filter(action=AuditAction.VIEW, metadata__vue="fiche").exists()


def test_ca21_aucun_ecart(src, dossier, client, admin_user):
    terminee(dossier["cv"], cv_conforme())
    client.force_login(admin_user)
    html = client.get(fiche_url(dossier["cand"])).content.decode()
    assert "data-aucun-ecart" in html


def test_ca22_fragment_etat(ia, dossier, client, direction_user):
    client.force_login(direction_user)
    client.post(lire_url(dossier["cand"]), {"mode": "lire"})  # lectures en attente
    vues = JournalAudit.objects.filter(action=AuditAction.VIEW).count()
    r = client.get(etat_url(dossier["cand"]), **HX)
    html = r.content.decode()
    assert (
        r.status_code == 200 and "data-lecture-polling" in html and 'hx-trigger="every 5s"' in html
    )
    assert not any(s in html for s in (*SECRETS, "cv_alaoui.pdf", "Informatique"))
    assert JournalAudit.objects.filter(action=AuditAction.VIEW).count() == vues
    # Une fois terminé : plus de rafraîchissement, lien d'actualisation.
    for e in Extraction.objects.all():
        lecture.executer_lecture(e.pk)
    html = client.get(etat_url(dossier["cand"]), **HX).content.decode()
    assert "data-lecture-polling" not in html and "data-lecture-terminee" in html


SENSIBLES = ("sexe", "age", "naissance", "photo", "familial", "nationalite", "region", "adresse")


def test_ca23_aucune_donnee_sensible():
    for schema in (LectureCV, LectureDiplome, LectureAttestation):
        champs = set(schema.model_json_schema()["properties"])
        for sous in schema.model_json_schema().get("$defs", {}).values():
            champs |= set(sous["properties"])
        assert not [c for c in champs if any(s in c for s in SENSIBLES)], schema
        assert schema.model_config.get("extra") == "forbid"
    for name in ("lecture_cv", "lecture_diplome", "lecture_attestation"):
        texte = (ROOT / "ai" / "prompts" / f"{name}.v1.md").read_text(encoding="utf-8")
        for mot in ("sexe", "âge", "date ou lieu de naissance", "photo", "situation familiale"):
            assert mot in texte
        assert "N'invente rien" in texte


def test_ca24_api_kan42(src, dossier):
    terminee(dossier["cv"], cv_conforme(formations=[]))
    terminee(dossier["diplome"], diplome_lu(niveau="Bac+3"))
    nb = Extraction.objects.count()
    extractions = extractions_de(dossier["cand"])
    assert {e.piece_id for e in extractions} == {dossier["cv"].pk, dossier["diplome"].pk}
    profil = profil_extrait(dossier["cand"])
    assert sorted(profil.pieces_niveau) == sorted([dossier["cv"].pk, dossier["diplome"].pk])
    assert profil.pieces_experience == [dossier["cv"].pk]
    assert profil.noms_titulaires[dossier["cv"].pk] == "[NOM_2] [NOM_1]"
    assert len(profil.profil.diplomes) == 1 and len(profil.profil.experiences) == 1
    assert profil.confiance_niveau == pytest.approx(0.95)
    ecarts = ecarts_declare_pieces(dossier["cand"])
    assert [(e.critere, e.declare, e.pieces) for e in ecarts] == [
        ("niveau_etudes", "Bac+5", "Bac+3")
    ]
    assert Extraction.objects.count() == nb and not AppelIA.objects.exists()


def test_ca25_i18n_et_rtl():
    for name in ("_lecture_pieces.html", "_lecture_etat.html"):
        texte = (ROOT / "templates" / "recruitment" / name).read_text(encoding="utf-8")
        assert not re.search(r'class="[^"]*\b(ml|mr|pl|pr|left|right|text-left|text-right)-', texte)
        assert "{% translate" in texte
    catalogue = (ROOT / "locale" / "ar" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")
    assert 'msgid "Lecture des pièces (IA)"' in catalogue
