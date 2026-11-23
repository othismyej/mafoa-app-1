"""Tests d'acceptation de KAN-12 — Visionneuse des pièces.

Fondés sur ``docs/specs/KAN-12-visionneuse-pieces.md`` (CA1 à CA41 ; CA42 est manuel).
Données fictives uniquement (domaine example.com, CIN ZZ…). La source est la ``FixtureSource``
ou un double de test ; aucun accès réseau, aucun appel IA.
"""

import base64
import hashlib
import re
from datetime import datetime
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.models import Anomalie
from apps.recruitment import pieces as pieces_service
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    Organisation,
    Piece,
    PosteConcours,
)
from integrations.site_source.fixture_source import FixtureSource

from .conftest import make_user
from .test_candidature_detail import one, parse

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
FIXTURE_KEY = "fixture://candidatures/A-0001/cv.pdf"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG_1PX = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
PDF_FICTIF = b"%PDF-1.4\n% document fictif\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
HTML_PIEGE = b"<html><script>alert(1)</script></html>"
DOCX_FICTIF = b"PK\x03\x04" + b"\x00" * 60


# --------------------------------------------------------------------------------------
# Doubles de test de la source
# --------------------------------------------------------------------------------------


class FakeSource:
    """Source simulée : renvoie des octets fictifs ou lève l'exception voulue."""

    name = "fake"

    def __init__(self, content=PDF_FICTIF, exc=None):
        self.content = content
        self.exc = exc
        self.calls = []

    def read_piece(self, storage_key, *, max_bytes=None, timeout=None):
        self.calls.append({"key": storage_key, "max_bytes": max_bytes, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return self.content


def _interdit(*args, **kwargs):
    raise AssertionError("La source des pièces ne doit pas être appelée")


@pytest.fixture
def fake():
    source = FakeSource()
    with mock.patch("apps.recruitment.views.get_source", return_value=source):
        yield source


@pytest.fixture
def no_source():
    """Échoue si la source (ou le stockage) est sollicitée."""
    with (
        mock.patch("apps.recruitment.views.get_source", side_effect=_interdit) as get_source,
        mock.patch(
            "integrations.site_source.fixture_source.FixtureSource.read_piece",
            side_effect=_interdit,
        ),
        mock.patch("boto3.client", side_effect=_interdit),
    ):
        yield get_source


# --------------------------------------------------------------------------------------
# Jeu de données fictif
# --------------------------------------------------------------------------------------


def at(*args) -> datetime:
    return timezone.make_aware(datetime(*args))


@pytest.fixture
def poste(db):
    org = Organisation.objects.create(nom="Agence Fictive Atlas", type=Organisation.Type.PUBLIC)
    concours = Concours.objects.create(organisation=org, intitule="Concours fictif Atlas 2026")
    return PosteConcours.objects.create(concours=concours, intitule="Ingénieur fictif")


_seq = {"n": 0}


def new_candidature(poste, **candidat) -> Candidature:
    _seq["n"] += 1
    n = _seq["n"]
    data = {"nom": f"Fictif{n}", "prenom": "Test", "email": f"fictif{n}@example.com"}
    data.update(candidat)
    return Candidature.objects.create(
        candidat=Candidat.objects.create(**data),
        poste_concours=poste,
        date_candidature=at(2026, 3, 15, 10, 30),
        statut=Candidature.Statut.A_VERIFIER,
    )


@pytest.fixture
def cand(poste):
    return new_candidature(
        poste, nom="ALAOUI", prenom="Salma", cin="ZZ100037", email="salma.alaoui1@example.com"
    )


def piece(candidature, type_=Piece.Type.CV, nom="cv_alaoui.pdf", **kw) -> Piece:
    data = {
        "storage_key": FIXTURE_KEY,
        "content_type": "application/pdf",
        "taille": None,
        "sha256": "",
        "recue_le": at(2026, 3, 16, 9, 0),
    }
    data.update(kw)
    return Piece.objects.create(candidature=candidature, type=type_, nom_fichier=nom, **data)


def contenu_url(p: Piece, candidature_pk=None, **params) -> str:
    u = reverse("recruitment:piece_contenu", args=[candidature_pk or p.candidature_id, p.pk])
    if params:
        u += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return u


def fragment_url(p: Piece, candidature_pk=None) -> str:
    return reverse("recruitment:piece_visionneuse", args=[candidature_pk or p.candidature_id, p.pk])


def fiche_url(c) -> str:
    return reverse("recruitment:candidature_detail", args=[c.pk])


def get(client, user, path, **extra):
    client.force_login(user)
    return client.get(path, **extra)


def view_entries():
    return JournalAudit.objects.filter(action=AuditAction.VIEW)


def piece_entries(p: Piece):
    ct = ContentType.objects.get_for_model(Piece)
    return view_entries().filter(content_type=ct, object_id=str(p.pk))


def single_entry(p: Piece) -> JournalAudit:
    entries = list(piece_entries(p))
    assert len(entries) == 1, entries
    return entries[0]


def body(response) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


# --------------------------------------------------------------------------------------
# Permissions et appartenance (CA1 à CA7)
# --------------------------------------------------------------------------------------


def test_ca1_direction_apercu_pdf_fixture(client, direction_user, cand):
    """CA1 — Pièce fictive lue via FixtureSource : 200, PDF identique à read_piece."""
    p = piece(cand)
    expected = FixtureSource(seed=settings.SITE_SOURCE_FIXTURE_SEED).read_piece(FIXTURE_KEY)
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert body(response).startswith(b"%PDF-")
    assert body(response) == expected


def test_ca2_admin_meme_resultat(client, admin_user, cand):
    """CA2 — Un administrateur obtient le même contenu."""
    p = piece(cand)
    expected = FixtureSource(seed=settings.SITE_SOURCE_FIXTURE_SEED).read_piece(FIXTURE_KEY)
    response = get(client, admin_user, contenu_url(p))
    assert response.status_code == 200
    assert body(response) == expected


@pytest.mark.parametrize("vue", ["fragment", "contenu"])
def test_ca3_non_connecte_redirige(client, cand, no_source, vue):
    """CA3 — Non connecté : redirection vers la connexion avec ``next``, source non appelée."""
    p = piece(cand)
    path = fragment_url(p) if vue == "fragment" else contenu_url(p)
    response = client.get(path)
    assert response.status_code == 302
    location = urlparse(response["Location"])
    assert location.path == reverse("account_login")
    assert parse_qs(location.query)["next"] == [path]
    assert not no_source.called


def test_utilisateur_sans_totp_redirige(client, user_without_mfa, cand, no_source):
    """Rôles et permissions — Connecté sans TOTP : redirection vers l'activation du TOTP."""
    p = piece(cand)
    for path in (fragment_url(p), contenu_url(p)):
        response = get(client, user_without_mfa, path)
        assert response.status_code == 302
        assert response["Location"] == reverse("mfa_activate_totp")
    assert not no_source.called


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
def test_ca4_role_insuffisant(client, cand, no_source, role):
    """CA4 — consultant / client : 403 sur les deux vues, aucun octet ni nom de fichier, source
    non appelée ; refus journalisé (``refus = droits``) pour le contenu seulement."""
    p = piece(cand)
    user = make_user(f"{role}@example.com", role)
    fragment = get(client, user, fragment_url(p))
    assert fragment.status_code == 403
    assert not view_entries().exists()
    response = get(client, user, contenu_url(p))
    assert response.status_code == 403
    for r in (fragment, response):
        assert b"%PDF" not in body(r)
        assert b"cv_alaoui" not in body(r)
    assert not no_source.called
    entry = view_entries().get()
    assert entry.metadata["refus"] == "droits"
    assert entry.actor == user
    assert entry.object_id == ""
    assert entry.object_repr == f"Pièce #{p.pk} (Candidature #{cand.pk})"


def test_ca5_piece_d_une_autre_candidature(client, direction_user, cand, poste, no_source):
    """CA5 — Pièce de A demandée sous B : 404 sans appel à la source ; refus journalisé pour le
    contenu (``piece_hors_candidature``), pas pour le fragment."""
    p = piece(cand)
    autre = new_candidature(poste)
    fragment = get(client, direction_user, fragment_url(p, autre.pk))
    assert fragment.status_code == 404
    assert not view_entries().exists()
    response = get(client, direction_user, contenu_url(p, autre.pk))
    assert response.status_code == 404
    assert b"%PDF" not in body(response)
    assert not no_source.called
    entry = view_entries().get()
    assert entry.metadata["refus"] == "piece_hors_candidature"
    assert entry.metadata["candidature_id"] == autre.pk
    assert entry.metadata["piece_id"] == p.pk
    assert entry.object_repr == f"Pièce #{p.pk} (Candidature #{autre.pk})"


def test_ca6_identifiants_inexistants(client, direction_user, cand, no_source):
    """CA6 — Candidature ou pièce inexistante : 404 pour les deux vues, sans appel à la source."""
    p = piece(cand)
    paths = [
        reverse("recruitment:piece_visionneuse", args=[999999, p.pk]),
        reverse("recruitment:piece_contenu", args=[999999, p.pk]),
        reverse("recruitment:piece_visionneuse", args=[cand.pk, 999999]),
        reverse("recruitment:piece_contenu", args=[cand.pk, 999999]),
    ]
    for path in paths:
        assert get(client, direction_user, path).status_code == 404, path
    assert not no_source.called


@pytest.mark.parametrize("method", ["post", "head", "put", "delete"])
def test_ca7_methodes_interdites(client, direction_user, cand, no_source, method):
    """CA7 — Toute méthode autre que GET (dont POST et HEAD) : 405, source non appelée."""
    p = piece(cand)
    client.force_login(direction_user)
    for path in (fragment_url(p), contenu_url(p)):
        assert getattr(client, method)(path).status_code == 405, path
    assert not no_source.called


# --------------------------------------------------------------------------------------
# En-têtes et types (CA8 à CA14)
# --------------------------------------------------------------------------------------


def test_ca8_entetes_apercu_pdf(client, direction_user, cand):
    """CA8 — En-têtes de l'aperçu PDF ; dérogation de cadrage limitée à cette réponse."""
    p = piece(cand)
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 200
    disposition = response["Content-Disposition"]
    assert disposition.startswith("inline")
    assert 'filename="' in disposition
    assert "filename*=UTF-8''" in disposition
    assert int(response["Content-Length"]) == len(body(response))
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]
    assert response["X-Frame-Options"] == "SAMEORIGIN"
    assert response["Accept-Ranges"] == "none"
    assert response["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response["Referrer-Policy"] == "same-origin"
    csp = response["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "sandbox" not in csp
    assert not response.has_header("ETag")
    assert not response.has_header("Last-Modified")


@pytest.mark.parametrize(
    ("content", "declared", "expected"),
    [(PNG_1PX, "image/png", "image/png"), (JPEG_1PX, "image/jpeg", "image/jpeg")],
)
def test_ca9_apercu_images(client, direction_user, cand, fake, content, declared, expected):
    """CA9 — PNG / JPEG fictifs : type détecté, ``inline`` et CSP avec ``sandbox``."""
    fake.content = content
    p = piece(cand, nom="photo_fictive.png", content_type=declared)
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 200
    assert response["Content-Type"] == expected
    assert response["Content-Disposition"].startswith("inline")
    assert "sandbox" in response["Content-Security-Policy"]
    assert body(response) == content


def test_ca10_html_declare_pdf(client, direction_user, cand, fake):
    """CA10 — Octets HTML déclarés PDF : aperçu refusé (415) sans renvoyer le contenu ;
    téléchargement en ``application/octet-stream``."""
    fake.content = HTML_PIEGE
    p = piece(cand)
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 415
    assert b"<script>alert(1)" not in body(response)
    assert "Aperçu impossible" in response.content.decode()
    assert single_entry(p).metadata["resultat"] == "type_non_affichable"
    download = get(client, direction_user, contenu_url(p, telechargement=1))
    assert download.status_code == 200
    assert download["Content-Type"] == "application/octet-stream"
    assert download["Content-Disposition"].startswith("attachment")


def test_ca11_fragment_type_non_affichable(client, direction_user, cand, no_source):
    """CA11 — Document Word déclaré : ni iframe ni img, « Aperçu non disponible » et lien
    « Télécharger » avec ``telechargement=1``."""
    p = piece(
        cand,
        nom="lettre_fictive.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response = get(client, direction_user, fragment_url(p), **HX)
    assert response.status_code == 200
    root = parse(response)
    assert not root.find_all(lambda n: n.tag in ("iframe", "img"))
    assert "Aperçu non disponible pour ce type de fichier" in root.text()
    lien = one(root, "data-telecharger")
    assert lien.text() == "Télécharger"
    href = urlparse(lien.attrs["href"])
    assert href.path == contenu_url(p)
    assert parse_qs(href.query) == {"telechargement": ["1"]}


def test_ca11_telechargement_docx(client, direction_user, cand, fake):
    """Règles 8 et 14 — Un type non affichable n'est servi qu'en téléchargement."""
    fake.content = DOCX_FICTIF
    p = piece(cand, nom="lettre_fictive.docx", content_type="application/msword")
    assert get(client, direction_user, contenu_url(p)).status_code == 415
    download = get(client, direction_user, contenu_url(p, telechargement=1))
    assert download.status_code == 200
    assert download["Content-Type"] == "application/octet-stream"
    assert "lettre_fictive.docx" in download["Content-Disposition"]
    assert "sandbox" not in download["Content-Security-Policy"]
    # Toute autre valeur du paramètre vaut aperçu.
    assert get(client, direction_user, contenu_url(p, telechargement="oui")).status_code == 415


def _filename(disposition: str) -> str:
    match = re.search(r'filename="([^"]*)"', disposition)
    assert match, disposition
    return match.group(1)


def test_ca12_nom_de_fichier_assaini(client, direction_user, cand, fake):
    """CA12 — Nom piégé : ni chemin, ni CR/LF, ni guillemet, aucun en-tête injecté ; extension
    alignée sur le type détecté."""
    p = piece(cand, nom='../cv "test"\r\nX-Inject: 1.pdf')
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 200
    disposition = response["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    name = _filename(disposition)
    assert "/" not in name and "\\" not in name
    star = disposition.split("filename*=UTF-8''", 1)[1]
    assert "/" not in star and '"' not in star and "%0D" not in star and "%0A" not in star
    assert not response.has_header("X-Inject")

    p2 = piece(cand, nom="cv_test.docx")
    response = get(client, direction_user, contenu_url(p2))
    assert _filename(response["Content-Disposition"]).endswith(".pdf")
    assert response["Content-Disposition"].endswith(".pdf")


@pytest.mark.parametrize(
    ("nom", "type_detecte", "attendu"),
    [
        ("cv_test.docx", "application/pdf", "cv_test.pdf"),
        ("photo.jpeg", "image/jpeg", "photo.jpeg"),
        ("photo.png", "image/jpeg", "photo.jpg"),
        ("diplome", "application/pdf", "diplome.pdf"),
        ("", "application/pdf", "piece-7.pdf"),
        ("../..", "image/png", "piece-7.png"),
        ("archive.zip", None, "archive.zip"),
        ('a\\b/c"d\x00e.pdf', "application/pdf", "abcde.pdf"),
    ],
)
def test_regle13_nom_assaini(nom, type_detecte, attendu):
    """Règle 13 — Assainissement du nom proposé au navigateur."""
    assert pieces_service.nom_assaini(nom, 7, type_detecte) == attendu


def test_regle13_nom_tronque_a_150():
    nom = pieces_service.nom_assaini("x" * 400 + ".pdf", 7, "application/pdf")
    assert len(nom) == 150
    assert nom.endswith(".pdf")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (PDF_FICTIF, "application/pdf"),
        (b" " * 1000 + b"%PDF-1.7", "application/pdf"),  # dans les 1 024 premiers octets
        (b" " * 1100 + b"%PDF-1.7", None),  # au-delà : non affichable
        (PNG_1PX, "image/png"),
        (JPEG_1PX, "image/jpeg"),
        (HTML_PIEGE, None),
        (DOCX_FICTIF, None),
        (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", None),
        (b"GIF89a", None),
    ],
)
def test_regle8_detection_du_type(content, expected):
    """Règle 8 — Type réel détecté sur les octets."""
    assert pieces_service.detect_type(content) == expected


def test_ca13_range_ignore(client, direction_user, cand):
    """CA13 — ``Range`` ignoré : 200 et corps complet."""
    p = piece(cand)
    full = body(get(client, direction_user, contenu_url(p)))
    response = get(client, direction_user, contenu_url(p), HTTP_RANGE="bytes=0-99")
    assert response.status_code == 200
    assert body(response) == full
    assert len(full) > 100


def test_ca14_politique_globale_inchangee(client, direction_user, cand, no_source):
    """CA14 — Fiche et fragment gardent ``DENY`` et ``frame-ancestors 'none'`` ; ni ``<object``,
    ni ``<embed``, ni ``sandbox`` sur l'iframe."""
    p = piece(cand)
    assert settings.X_FRAME_OPTIONS == "DENY"
    assert settings.CONTENT_SECURITY_POLICY["DIRECTIVES"]["frame-ancestors"] == ["'none'"]
    for response in (
        get(client, direction_user, fiche_url(cand)),
        get(client, direction_user, fragment_url(p), **HX),
    ):
        assert response.status_code == 200
        assert response["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
        html = response.content.decode()
        assert "<object" not in html and "<embed" not in html
        for iframe in parse(html).find_all(lambda n: n.tag == "iframe"):
            assert "sandbox" not in iframe.attrs


# --------------------------------------------------------------------------------------
# Lecture à la demande (CA15 à CA17)
# --------------------------------------------------------------------------------------


def test_ca15_aucune_copie_disque_ni_cache(client, direction_user, cand):
    """CA15 — Aucun fichier temporaire, aucune écriture du stockage par défaut, aucune clé de
    cache ; les champs de la pièce sont inchangés."""
    content = FixtureSource(seed=settings.SITE_SOURCE_FIXTURE_SEED).read_piece(FIXTURE_KEY)
    p = piece(cand, taille=len(content), sha256=hashlib.sha256(content).hexdigest())
    before = Piece.objects.values("sha256", "taille", "updated_at").get(pk=p.pk)
    client.force_login(direction_user)
    with (
        mock.patch("tempfile.NamedTemporaryFile", side_effect=_interdit),
        mock.patch("tempfile.TemporaryFile", side_effect=_interdit),
        mock.patch("tempfile.SpooledTemporaryFile", side_effect=_interdit),
        mock.patch("tempfile.mkstemp", side_effect=_interdit),
        mock.patch("django.core.files.storage.default_storage.save", side_effect=_interdit),
        mock.patch("django.core.cache.backends.locmem.LocMemCache.set", side_effect=_interdit),
        mock.patch("django.core.cache.backends.locmem.LocMemCache.add", side_effect=_interdit),
        mock.patch("django.core.cache.backends.locmem.LocMemCache.set_many", side_effect=_interdit),
    ):
        response = client.get(contenu_url(p))
    assert response.status_code == 200
    assert Piece.objects.values("sha256", "taille", "updated_at").get(pk=p.pk) == before


def test_ca16_fiche_et_fragment_sans_appel_source(client, direction_user, cand, no_source):
    """CA16 — La fiche et le fragment n'appellent jamais la source."""
    p = piece(cand)
    assert get(client, direction_user, fiche_url(cand)).status_code == 200
    assert get(client, direction_user, fragment_url(p), **HX).status_code == 200
    assert not no_source.called


def test_ca16_contenu_un_appel_par_requete(client, direction_user, cand, fake):
    """CA16 — Une requête de contenu = un appel à la source (avec taille maximale et délai)."""
    p = piece(cand)
    get(client, direction_user, contenu_url(p))
    assert len(fake.calls) == 1
    assert fake.calls[0]["key"] == FIXTURE_KEY
    assert fake.calls[0]["max_bytes"] == settings.PIECE_VIEWER_MAX_BYTES
    assert fake.calls[0]["timeout"] == settings.PIECE_SOURCE_TIMEOUT
    get(client, direction_user, contenu_url(p))
    assert len(fake.calls) == 2


def test_ca17_aucune_cle_de_stockage_exposee(client, direction_user, cand, poste):
    """CA17 — Ni ``fixture://`` ni ``digitaloceanspaces.com`` dans la fiche, le fragment ou les
    réponses d'erreur."""
    p = piece(cand)
    absente = piece(cand, Piece.Type.DIPLOME, "diplome.pdf", storage_key="fixture://inconnue.pdf")
    autre = new_candidature(poste)
    responses = [
        get(client, direction_user, fiche_url(cand)),
        get(client, direction_user, fragment_url(p), **HX),
        get(client, direction_user, contenu_url(absente)),
        get(client, direction_user, contenu_url(p, autre.pk)),
        get(client, direction_user, reverse("recruitment:piece_visionneuse", args=[cand.pk, 0])),
    ]
    with mock.patch(
        "apps.recruitment.views.get_source", return_value=FakeSource(exc=ConnectionError("x"))
    ):
        responses.append(get(client, direction_user, contenu_url(p)))
    for response in responses:
        assert b"fixture://" not in body(response)
        assert b"digitaloceanspaces.com" not in body(response)


# --------------------------------------------------------------------------------------
# Taille (CA18 à CA20)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("params", [{}, {"telechargement": 1}])
def test_ca18_taille_connue_trop_grande(client, direction_user, cand, no_source, settings, params):
    """CA18 — ``taille`` > maximum : 422 sans lire la source, journal ``trop_volumineuse``."""
    settings.PIECE_VIEWER_MAX_BYTES = 1000
    p = piece(cand, taille=5000)
    response = get(client, direction_user, contenu_url(p, **params))
    assert response.status_code == 422
    assert "Pièce trop volumineuse pour la visionneuse" in response.content.decode()
    assert not no_source.called
    assert single_entry(p).metadata["resultat"] == "trop_volumineuse"


def test_ca18_message_tailles_en_mo(client, direction_user, cand, no_source):
    """Règle 6 — Message avec la taille et le maximum par défaut (20 Mo)."""
    p = piece(cand, taille=25 * 1024 * 1024)
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 422
    text = response.content.decode()
    assert "taille : 25 Mo" in text
    assert "maximum : 20 Mo" in text


def test_ca19_taille_inconnue_contenu_trop_grand(client, direction_user, cand, fake, settings):
    """CA19 — Taille inconnue, contenu lu > maximum : 422, aucun octet renvoyé."""
    settings.PIECE_VIEWER_MAX_BYTES = 1000
    fake.content = b"%PDF-1.4 " + b"Z" * 4991
    p = piece(cand, taille=None)
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 422
    assert b"ZZZZ" not in body(response)
    assert b"%PDF" not in body(response)
    assert single_entry(p).metadata["resultat"] == "trop_volumineuse"


def test_ca20_fichier_vide(client, direction_user, cand, fake):
    """CA20 — 0 octet : 422 « Le fichier reçu est vide », journal ``vide``."""
    fake.content = b""
    p = piece(cand)
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 422
    assert "Le fichier reçu est vide" in response.content.decode()
    assert single_entry(p).metadata["resultat"] == "vide"


# --------------------------------------------------------------------------------------
# Empreinte (CA21 à CA26)
# --------------------------------------------------------------------------------------


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_ca21_empreinte_conforme_insensible_a_la_casse(client, direction_user, cand, fake):
    """CA21 — Empreinte en majuscules identique : 200, ``empreinte = conforme``."""
    p = piece(cand, sha256=sha(PDF_FICTIF).upper())
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 200
    assert single_entry(p).metadata["empreinte"] == "conforme"


@pytest.fixture
def piece_modifiee(cand):
    return piece(cand, sha256=sha(b"contenu fictif d'origine"))


def test_ca22_empreinte_differente_apercu_bloque(
    client, direction_user, cand, fake, piece_modifiee
):
    """CA22 — Empreinte différente : 409 sans contenu, anomalie haute créée et journalisée,
    ``Piece.sha256`` et statut inchangés."""
    p = piece_modifiee
    attendue = p.sha256
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 409
    assert "Le fichier a été modifié à la source depuis sa réception" in response.content.decode()
    assert b"%PDF" not in body(response)
    anomalie = Anomalie.objects.get(piece=p)
    assert anomalie.type == Anomalie.Type.EMPREINTE_DIFFERENTE == "empreinte_differente"
    assert anomalie.get_type_display() == "Empreinte de pièce différente"
    assert anomalie.gravite == Anomalie.Gravite.HAUTE
    assert anomalie.candidature == cand
    assert attendue in anomalie.description
    assert sha(PDF_FICTIF) in anomalie.description
    assert not anomalie.resolue
    create = JournalAudit.objects.get(action=AuditAction.CREATE)
    assert create.content_type == ContentType.objects.get_for_model(Anomalie)
    assert create.object_id == str(anomalie.pk)
    assert create.actor is None
    assert "contrôle d’empreinte" in create.actor_repr
    assert create.metadata == {
        "piece_id": p.pk,
        "candidature_id": cand.pk,
        "declenche_par_user_id": direction_user.pk,
    }
    entry = single_entry(p)
    assert entry.metadata["resultat"] == "empreinte_differente"
    assert entry.metadata["empreinte"] == "differente"
    p.refresh_from_db()
    cand.refresh_from_db()
    assert p.sha256 == attendue
    assert cand.statut == Candidature.Statut.A_VERIFIER


def test_ca23_empreinte_differente_telechargement(
    client, direction_user, cand, fake, piece_modifiee
):
    """CA23 — Le téléchargement reste possible et porte ``empreinte = differente``."""
    response = get(client, direction_user, contenu_url(piece_modifiee, telechargement=1))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    entry = single_entry(piece_modifiee)
    assert entry.metadata["empreinte"] == "differente"
    assert entry.metadata["resultat"] == "servie"
    assert entry.metadata["mode"] == "telechargement"
    assert Anomalie.objects.filter(piece=piece_modifiee).count() == 1


def test_ca24_anomalie_idempotente(client, direction_user, cand, fake, piece_modifiee):
    """CA24 — Même empreinte constatée : une seule anomalie, même après résolution ; nouvelle
    empreinte constatée : seconde anomalie."""
    p = piece_modifiee
    get(client, direction_user, contenu_url(p))
    get(client, direction_user, contenu_url(p))
    Anomalie.objects.filter(piece=p).update(
        resolue=True, resolue_par=direction_user, resolue_le=timezone.now()
    )
    get(client, direction_user, contenu_url(p))
    assert Anomalie.objects.filter(piece=p, type="empreinte_differente").count() == 1
    assert JournalAudit.objects.filter(action=AuditAction.CREATE).count() == 1
    fake.content = PDF_FICTIF + b"% autre version fictive\n"
    get(client, direction_user, contenu_url(p))
    assert Anomalie.objects.filter(piece=p, type="empreinte_differente").count() == 2


def test_ca25_empreinte_absente(client, direction_user, cand, fake):
    """CA25 — Sans empreinte : servie, ``non_verifiee``, ``sha256`` reste vide, mention dans le
    fragment."""
    p = piece(cand, sha256="")
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 200
    assert single_entry(p).metadata["empreinte"] == "non_verifiee"
    p.refresh_from_db()
    assert p.sha256 == ""
    fragment = get(client, direction_user, fragment_url(p), **HX)
    assert "Empreinte non disponible : intégrité non vérifiée" in parse(fragment).text()


def test_ca26_bandeau_empreinte_modifiee(client, direction_user, cand, no_source):
    """CA26 — Anomalie d'empreinte non résolue : bandeau dans le fragment ; plus de bandeau
    une fois l'anomalie résolue."""
    p = piece(cand, sha256="a" * 64)
    anomalie = Anomalie.objects.create(
        candidature=cand,
        piece=p,
        type=Anomalie.Type.EMPREINTE_DIFFERENTE,
        gravite=Anomalie.Gravite.HAUTE,
        description="Empreinte fictive différente.",
    )
    root = parse(get(client, direction_user, fragment_url(p), **HX))
    bandeau = one(root, "data-empreinte-modifiee")
    assert "Le fichier a été modifié à la source depuis sa réception" in bandeau.text()
    assert root.with_attr("data-visionneuse-anomalie")
    anomalie.resolue = True
    anomalie.resolue_par = direction_user
    anomalie.save()
    root = parse(get(client, direction_user, fragment_url(p), **HX))
    assert not root.with_attr("data-empreinte-modifiee")
    assert not root.with_attr("data-empreinte-absente")


# --------------------------------------------------------------------------------------
# Erreurs de la source (CA27, CA28)
# --------------------------------------------------------------------------------------


def _assert_indisponible(response, p):
    assert response.status_code == 503
    text = response.content.decode()
    assert "La source des pièces est momentanément indisponible" in text
    for technique in ("Traceback", "NotImplementedError", "DatabaseSource", "ConnectionError"):
        assert technique not in text
    assert "TimeoutError" not in text
    assert piece_entries(p).filter(metadata__resultat="source_indisponible").exists()


def test_ca27_source_database_indisponible(client, direction_user, cand, settings):
    """CA27 — ``SITE_SOURCE = database`` (non disponible) : 503 journalisé."""
    settings.SITE_SOURCE = "database"
    p = piece(cand)
    _assert_indisponible(get(client, direction_user, contenu_url(p)), p)


@pytest.mark.parametrize(
    "exc", [ConnectionError("réseau fictif"), TimeoutError("délai"), RuntimeError("boom")]
)
def test_ca27_erreurs_reseau(client, direction_user, cand, exc):
    """CA27 — ``ConnectionError``, ``TimeoutError`` ou autre erreur : 503, jamais 500."""
    p = piece(cand)
    with mock.patch("apps.recruitment.views.get_source", return_value=FakeSource(exc=exc)):
        response = get(client, direction_user, contenu_url(p))
    _assert_indisponible(response, p)


def test_ca28_fichier_introuvable(client, direction_user, cand):
    """CA28 — Clé inconnue de FixtureSource : 404 « Fichier introuvable à la source », aucune
    anomalie, journal ``introuvable``."""
    p = piece(cand, storage_key="fixture://candidatures/A-9999/inconnu.pdf")
    response = get(client, direction_user, contenu_url(p))
    assert response.status_code == 404
    assert "Fichier introuvable à la source" in response.content.decode()
    assert not Anomalie.objects.exists()
    assert single_entry(p).metadata["resultat"] == "introuvable"


def test_regle18_page_erreur_affichable_dans_l_iframe(client, direction_user, cand, fake):
    """Règle 18 — Page d'erreur sans script, cadrable par la fiche (même origine)."""
    fake.content = b""
    p = piece(cand)
    response = get(client, direction_user, contenu_url(p))
    assert response["X-Frame-Options"] == "SAMEORIGIN"
    csp = response["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in csp
    assert "script-src" not in csp and "default-src 'none'" in csp
    assert "<script" not in response.content.decode()


# --------------------------------------------------------------------------------------
# Visionneuse (CA29 à CA35)
# --------------------------------------------------------------------------------------


@pytest.fixture
def trois_pieces(cand):
    # Ordre de la fiche : type puis id → attestation_travail, cv, diplome.
    return [
        piece(cand, Piece.Type.ATTESTATION_TRAVAIL, "attestation_fictive.pdf"),
        piece(cand, Piece.Type.CV, "cv_alaoui.pdf"),
        piece(cand, Piece.Type.DIPLOME, "diplome_fictif.pdf"),
    ]


def test_ca29_boutons_consulter(client, direction_user, cand, trois_pieces, no_source):
    """CA29 — Chaque pièce porte « Consulter » (hx-get du fragment, cible ``#visionneuse``, href
    de repli vers le contenu dans un nouvel onglet) ; ``#visionneuse`` est vide."""
    root = parse(get(client, direction_user, fiche_url(cand)))
    for p in trois_pieces:
        bouton = one(root, "data-consulter", str(p.pk))
        assert bouton.text().startswith("Consulter")
        assert bouton.attrs["hx-get"] == fragment_url(p)
        assert bouton.attrs["hx-target"] == "#visionneuse"
        assert bouton.attrs["href"] == contenu_url(p)
        assert bouton.attrs["target"] == "_blank"
        assert "noopener" in bouton.attrs["rel"].split()
        assert bouton.parent.attrs.get("data-piece") == str(p.pk)
    zone = one(root, "id", "visionneuse")
    assert zone.children == [] and zone.text() == ""
    assert zone.attrs.get("aria-live") == "polite"
    assert not no_source.called


def test_ca30_fragment_piece_de_rang_2(client, direction_user, cand, trois_pieces):
    """CA30 — Fragment de la pièce 2 : iframe, « Pièce 2 sur 3 », précédente / suivante,
    ouverture dans un nouvel onglet et téléchargement."""
    p1, p2, p3 = trois_pieces
    response = get(client, direction_user, fragment_url(p2), **HX)
    assert response.status_code == 200
    root = parse(response)
    iframes = root.find_all(lambda n: n.tag == "iframe")
    assert len(iframes) == 1
    assert iframes[0].attrs["src"] == contenu_url(p2)
    assert iframes[0].attrs["title"].startswith("Aperçu")
    assert iframes[0].attrs["title"] == "Aperçu : CV"
    assert "Pièce 2 sur 3" in root.text()
    precedente = one(root, "data-precedente")
    suivante = one(root, "data-suivante")
    assert "Pièce précédente" in precedente.text()
    assert "Pièce suivante" in suivante.text()
    assert precedente.attrs["hx-get"] == precedente.attrs["href"] == fragment_url(p1)
    assert suivante.attrs["hx-get"] == suivante.attrs["href"] == fragment_url(p3)
    assert precedente.attrs["hx-target"] == suivante.attrs["hx-target"] == "#visionneuse"
    ouvrir = one(root, "data-ouvrir-onglet")
    assert ouvrir.text() == "Ouvrir dans un nouvel onglet"
    assert ouvrir.attrs["href"] == contenu_url(p2)
    assert ouvrir.attrs["target"] == "_blank"
    assert "noopener" in ouvrir.attrs["rel"]
    assert one(root, "data-telecharger").attrs["href"] == contenu_url(p2, telechargement=1)
    titre = one(root, "data-visionneuse-titre")
    assert titre.attrs["tabindex"] == "-1"
    assert root.with_attr("data-visionneuse-fermer")


def test_ca31_extremites(client, direction_user, cand, trois_pieces):
    """CA31 — Pas de lien actif « Pièce précédente » sur la première ni « Pièce suivante » sur
    la dernière ; une seule pièce : ni l'un ni l'autre."""
    p1, _, p3 = trois_pieces
    first = parse(get(client, direction_user, fragment_url(p1), **HX))
    assert not first.with_attr("data-precedente")
    assert one(first, "data-precedente-inactive").attrs.get("aria-disabled") == "true"
    assert "href" not in one(first, "data-precedente-inactive").attrs
    assert first.with_attr("data-suivante")
    last = parse(get(client, direction_user, fragment_url(p3), **HX))
    assert not last.with_attr("data-suivante")
    assert "href" not in one(last, "data-suivante-inactive").attrs
    assert last.with_attr("data-precedente")


def test_une_seule_piece(client, direction_user, poste):
    """Cas limite — Une seule pièce : « Pièce 1 sur 1 », ni précédente ni suivante."""
    c = new_candidature(poste)
    p = piece(c)
    root = parse(get(client, direction_user, fragment_url(p), **HX))
    assert "Pièce 1 sur 1" in root.text()
    assert not root.with_attr("data-precedente") and not root.with_attr("data-suivante")


def test_ca32_image_alt_sans_nom(client, direction_user, cand):
    """CA32 — Pièce déclarée JPEG : ``<img>`` vers le contenu, ``alt`` non vide et sans nom."""
    p = piece(cand, nom="photo_alaoui.jpg", content_type="image/jpeg")
    root = parse(get(client, direction_user, fragment_url(p), **HX))
    imgs = root.find_all(lambda n: n.tag == "img")
    assert len(imgs) == 1
    assert imgs[0].attrs["src"] == contenu_url(p)
    alt = imgs[0].attrs["alt"]
    assert alt and "alaoui" not in alt.lower() and "salma" not in alt.lower()
    assert alt == "CV, pièce 1 sur 1"
    assert not root.find_all(lambda n: n.tag == "iframe")


def test_ca33_navigation_limitee_a_la_candidature(client, direction_user, cand, poste):
    """CA33 — Les liens précédente / suivante ne pointent jamais vers une autre candidature."""
    autre = new_candidature(poste)
    a = [piece(cand, Piece.Type.CV), piece(cand, Piece.Type.DIPLOME, "d.pdf")]
    b = [piece(autre, Piece.Type.CV, "cv_b.pdf"), piece(autre, Piece.Type.DIPLOME, "d_b.pdf")]
    autorises = {fragment_url(p) for p in a}
    interdits = {fragment_url(p) for p in b} | {fragment_url(p, cand.pk) for p in b}
    for p in a:
        root = parse(get(client, direction_user, fragment_url(p), **HX))
        for lien in root.with_attr("hx-get"):
            assert lien.attrs["hx-get"] in autorises
            assert lien.attrs["hx-get"] not in interdits


def _fragment_queries(client, user, nb_pieces, poste) -> int:
    c = new_candidature(poste)
    pieces = [piece(c, Piece.Type.AUTRE, f"piece_{i}.pdf") for i in range(nb_pieces)]
    client.force_login(user)
    client.get(fragment_url(pieces[0]), **HX)  # préchauffe (session, types de contenu)
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(fragment_url(pieces[nb_pieces // 2]), **HX)
    assert response.status_code == 200
    return len(ctx.captured_queries)


def test_ca34_requetes_bornees(client, direction_user, poste):
    """CA34 — Le nombre de requêtes SQL du fragment ne dépend pas du nombre de pièces."""
    deux = _fragment_queries(client, direction_user, 2, poste)
    cinquante = _fragment_queries(client, direction_user, 50, poste)
    assert cinquante == deux
    assert deux <= 12


def test_ca35_nom_echappe(client, direction_user, cand):
    """CA35 — Nom de fichier contenant une balise : affiché échappé, dans un ``<bdi>``."""
    p = piece(cand, nom="<script>alert(1)</script>.pdf")
    response = get(client, direction_user, fragment_url(p), **HX)
    html = response.content.decode()
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;.pdf" in html
    bdi = parse(html).find_all(lambda n: n.tag == "bdi")
    assert [n.text() for n in bdi] == ["<script>alert(1)</script>.pdf"]


# --------------------------------------------------------------------------------------
# Journalisation (CA36 à CA39)
# --------------------------------------------------------------------------------------


def test_ca36_entree_de_journal_apercu(client, direction_user, cand):
    """CA36 — Une entrée ``view`` complète sur l'objet Pièce, libellé sans nom."""
    p = piece(cand)
    client.force_login(direction_user)  # la connexion est journalisée à part (§5)
    before = JournalAudit.objects.count()
    client.get(contenu_url(p), REMOTE_ADDR="192.0.2.10", HTTP_USER_AGENT="NavigateurFictif/1.0")
    assert JournalAudit.objects.count() == before + 1
    entry = single_entry(p)
    assert entry.action == "view"
    assert entry.actor == direction_user
    assert entry.content_type == ContentType.objects.get_for_model(Piece)
    assert entry.object_id == str(p.pk)
    assert entry.object_repr == f"Pièce #{p.pk} (Candidature #{cand.pk})"
    assert entry.ip_address == "192.0.2.10"
    assert entry.user_agent == "NavigateurFictif/1.0"
    content = FixtureSource(seed=settings.SITE_SOURCE_FIXTURE_SEED).read_piece(FIXTURE_KEY)
    assert entry.metadata == {
        "candidature_id": cand.pk,
        "piece_id": p.pk,
        "type_piece": "cv",
        "mode": "apercu",
        "resultat": "servie",
        "empreinte": "non_verifiee",
        "type_detecte": "application/pdf",
        "taille": len(content),
    }


def test_ca37_entree_de_journal_telechargement(client, direction_user, cand):
    """CA37 — Téléchargement réussi : ``mode = telechargement``."""
    p = piece(cand)
    response = get(client, direction_user, contenu_url(p, telechargement=1))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment")
    assert single_entry(p).metadata["mode"] == "telechargement"


INTERDITS = ("alaoui", "salma", "zz100037", "example.com", "cv_alaoui", "fixture://")


def test_ca38_aucune_donnee_nominative(client, direction_user, cand, poste, settings):
    """CA38 — Succès, erreurs et refus : aucune donnée nominative, ni nom de fichier, ni clé de
    stockage dans les entrées créées."""
    p = piece(cand, sha256=sha(b"autre contenu"))
    autre = new_candidature(poste)
    consultant = make_user("consultant@example.org", Role.CONSULTANT)
    get(client, direction_user, contenu_url(p, telechargement=1))  # servie (empreinte diff.)
    get(client, direction_user, contenu_url(p))  # empreinte différente
    get(client, direction_user, contenu_url(p, autre.pk))  # pièce hors candidature
    get(client, consultant, contenu_url(p))  # droits
    introuvable = piece(cand, Piece.Type.DIPLOME, "diplome_alaoui.pdf", storage_key="fixture://x")
    get(client, direction_user, contenu_url(introuvable))
    settings.PIECE_VIEWER_MAX_BYTES = 10
    get(client, direction_user, contenu_url(p))  # trop volumineuse
    settings.SITE_SOURCE = "database"
    settings.PIECE_VIEWER_MAX_BYTES = 10**7
    get(client, direction_user, contenu_url(p))  # source indisponible
    entries = JournalAudit.objects.exclude(action=AuditAction.LOGIN)
    assert entries.count() >= 8
    for entry in entries:
        blob = " ".join(
            str(v) for v in (entry.object_repr, entry.metadata, entry.before, entry.after)
        ).lower()
        for interdit in INTERDITS:
            assert interdit not in blob, (interdit, entry.action, blob)
    anomalie = Anomalie.objects.get(piece=p)
    for interdit in INTERDITS:
        assert interdit not in anomalie.description.lower()


def test_ca39_fragment_non_journalise(client, direction_user, cand):
    """CA39 — Le fragment de visionneuse ne crée aucune entrée de journal."""
    p = piece(cand)
    client.force_login(direction_user)
    before = JournalAudit.objects.count()
    assert client.get(fragment_url(p), **HX).status_code == 200
    assert JournalAudit.objects.count() == before


# --------------------------------------------------------------------------------------
# Interface (CA40, CA41)
# --------------------------------------------------------------------------------------


def test_ca40_arabe_rtl(client, direction_user, cand, trois_pieces):
    """CA40 — Langue ``ar`` : fiche en ``dir="rtl"``, nom dans un ``<bdi>``, flèches
    ``flip-rtl``."""
    client.force_login(direction_user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    fiche = client.get(fiche_url(cand))
    assert fiche.status_code == 200
    html = parse(fiche).find_all(lambda n: n.tag == "html")[0]
    assert html.attrs.get("dir") == "rtl"
    fragment = client.get(fragment_url(trois_pieces[1]), **HX)
    assert fragment.status_code == 200
    root = parse(fragment)
    assert [n.text() for n in root.find_all(lambda n: n.tag == "bdi")] == ["cv_alaoui.pdf"]
    for attr in ("data-precedente", "data-suivante"):
        fleches = one(root, attr).find_all(lambda n: "flip-rtl" in n.attrs.get("class", ""))
        assert len(fleches) == 1


def test_ca41_gabarits_sans_style_ni_script_en_ligne():
    """CA41 — Gabarits de la visionneuse : ni ``style``, ni ``on…=``, ni ``<script>`` en ligne,
    ni propriétés physiques (``ml-``, ``left-``, ``text-right``…)."""
    base = settings.BASE_DIR / "templates" / "recruitment"
    for name in ("_visionneuse.html", "_visionneuse_erreur.html", "piece_erreur.html"):
        content = (base / name).read_text(encoding="utf-8")
        assert not re.search(r"\sstyle\s*=", content), name
        assert not re.search(r"\son[a-z]+\s*=", content), name
        assert "<script" not in content, name
        assert not re.search(
            r"(?<![\w-])(ml|mr|pl|pr|left|right)-[0-9a-z]|text-(left|right)", content
        ), name
        assert (
            "{% translate" in content
            or "{% blocktranslate" in content
            or name.startswith("_visionneuse_erreur")
        )
    fiche = (base / "candidature_detail.html").read_text(encoding="utf-8")
    assert "<object" not in fiche and "<embed" not in fiche
    # Le seul script ajouté par la fiche est un fichier statique (CSP script-src 'self').
    assert re.findall(r"<script[^>]*>", fiche) == [
        "<script src=\"{% static 'js/visionneuse.js' %}\" defer>"
    ]


def test_fragment_404_lisible(client, direction_user, cand):
    """Cas limite — Pièce supprimée entre l'affichage et le clic : fragment 404 avec message."""
    p = piece(cand)
    path = fragment_url(p)
    p.delete()
    response = get(client, direction_user, path, **HX)
    assert response.status_code == 404
    assert "Pièce introuvable" in parse(response).text()
