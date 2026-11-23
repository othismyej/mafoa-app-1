"""Tests d'acceptation de KAN-11 — Fiche candidature.

Les attentes sont fondées sur la spécification ``docs/specs/KAN-11-fiche-candidature.md``.
Toutes les données sont fictives (domaines example.com / example.org, numéros +212600000xxx).
Aucun appel IA ni accès au stockage de fichiers : ils sont remplacés par des mocks qui échouent.
"""

import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
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
from apps.evaluation.models import Anomalie, Decision, Score, Verdict, VerificationEligibilite
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    OffreEmploi,
    Organisation,
    Piece,
    PosteConcours,
)

from .conftest import make_user

pytestmark = pytest.mark.django_db

S = Candidature.Statut
HX = {"HTTP_HX_REQUEST": "true"}

# --------------------------------------------------------------------------------------
# Analyse HTML minimale (bibliothèque standard uniquement)
# --------------------------------------------------------------------------------------

VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source", "col", "area", "base", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []
        self.ordered = []

    def text(self) -> str:
        parts = [item if isinstance(item, str) else item.text() for item in self.ordered]
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()

    def find_all(self, predicate):
        return [n for n in self.iter() if predicate(n)]

    def with_attr(self, name, value=None):
        return self.find_all(
            lambda n: name in n.attrs and (value is None or n.attrs.get(name) == value)
        )


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", [])
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        self.current.ordered.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        self.current.ordered.append(data)


def parse(html) -> Node:
    if not isinstance(html, str):
        html = html.content.decode()
    builder = TreeBuilder()
    builder.feed(html)
    return builder.root


def one(root: Node, name: str, value=None) -> Node:
    nodes = root.with_attr(name, value)
    assert len(nodes) == 1, f"{name}={value!r} : {len(nodes)} éléments"
    return nodes[0]


def page_text(response) -> str:
    return parse(response).text()


# --------------------------------------------------------------------------------------
# Jeu de données fictif
# --------------------------------------------------------------------------------------


def at(*args) -> datetime:
    return timezone.make_aware(datetime(*args))


def local(dt: datetime) -> datetime:
    return timezone.localtime(dt)


def fmt_date(d) -> str:
    return d.strftime("%d/%m/%Y")


def date_variants(d: date) -> list[str]:
    mois = [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ]
    return [fmt_date(d), f"{d.day} {mois[d.month - 1]} {d.year}"]


def assert_date_present(text: str, d: date):
    assert any(v in text for v in date_variants(d)), f"date {d} absente"


def assert_time_present(text: str, dt: datetime):
    dt = local(dt)
    assert re.search(rf"{dt:%H}\s*[:h]\s*{dt:%M}", text), f"heure {dt:%H:%M} absente"


FORMATIONS = [
    {
        "intitule": "Master génie de l'eau",
        "niveau": "Bac+5",
        "specialite": "Hydraulique",
        "etablissement": "École fictive d'ingénieurs Alpha",
        "annee_obtention": 2014,
    },
    {
        "intitule": "Licence sciences et techniques",
        "niveau": "Bac+3",
        "specialite": "Génie civil",
        "etablissement": "Faculté fictive Beta",
        "annee_obtention": 2012,
    },
]

EXPERIENCES = [
    {
        "debut": "2015-09-01",
        "fin": "2019-06-30",
        "employeur": "Bureau d'études fictif Gamma",
        "poste": "Ingénieur junior",
    },
    {
        "debut": "2019-07-01",
        "fin": None,
        "employeur": "Régie fictive Delta",
        "poste": "Ingénieur hydraulicien",
    },
]


@pytest.fixture
def org(db):
    return Organisation.objects.create(nom="Agence Fictive Atlas", type=Organisation.Type.PUBLIC)


@pytest.fixture
def poste(org):
    concours = Concours.objects.create(organisation=org, intitule="Concours fictif Atlas 2026")
    return PosteConcours.objects.create(
        concours=concours, intitule="Ingénieur hydraulique", reference="P-07"
    )


_seq = {"n": 0}


def new_candidat(**kw) -> Candidat:
    _seq["n"] += 1
    n = _seq["n"]
    data = {
        "nom": f"Fictif{n}",
        "prenom": "Test",
        "email": f"fictif{n}@example.org",
        "telephone": f"+212600000{n:03d}",
    }
    data.update(kw)
    return Candidat.objects.create(**data)


def new_candidature(poste=None, offre=None, candidat=None, **kw) -> Candidature:
    data = {
        "candidat": candidat or new_candidat(),
        "poste_concours": poste,
        "offre": offre,
        "date_candidature": at(2026, 3, 15, 10, 30),
        "statut": S.A_VERIFIER,
        "donnees_declarees": {"formation": FORMATIONS, "experiences": EXPERIENCES},
    }
    data.update(kw)
    return Candidature.objects.create(**data)


@pytest.fixture
def candidat(db):
    return Candidat.objects.create(
        nom="Alaoui",
        prenom="Samira",
        email="samira.alaoui@example.org",
        telephone="+212600000123",
        date_naissance=date(1990, 5, 10),
        cin="ZZ999001",
        ville="Agadir",
        sexe="F",
        region="Souss-Massa",
    )


@pytest.fixture
def cand(poste, candidat):
    return new_candidature(poste=poste, candidat=candidat)


def url(candidature_or_pk) -> str:
    pk = getattr(candidature_or_pk, "pk", candidature_or_pk)
    return reverse("recruitment:candidature_detail", args=[pk])


def get(client, user, target, **extra):
    client.force_login(user)
    return client.get(target if isinstance(target, str) else url(target), **extra)


def ok(client, user, target, **extra):
    response = get(client, user, target, **extra)
    assert response.status_code == 200
    return response


def view_entries():
    return JournalAudit.objects.filter(action=AuditAction.VIEW)


def verification(candidature, resultat, created_at, details=None, version="1.0.0", **kw):
    v = VerificationEligibilite.objects.create(
        candidature=candidature,
        resultat=resultat,
        details=details or [],
        moteur_version=version,
        sources=kw.pop("sources", [{"type": "donnees_declarees"}]),
        **kw,
    )
    VerificationEligibilite.objects.filter(pk=v.pk).update(created_at=created_at)
    v.refresh_from_db()
    return v


def decision(candidature, auteur, value, motif, created_at):
    d = Decision.objects.create(candidature=candidature, auteur=auteur, decision=value, motif=motif)
    Decision.objects.filter(pk=d.pk).update(created_at=created_at)
    d.refresh_from_db()
    return d


def anomalie(candidature, created_at, **kw):
    data = {
        "type": Anomalie.Type.AUTRE,
        "gravite": Anomalie.Gravite.MOYENNE,
        "description": "Anomalie fictive",
    }
    data.update(kw)
    a = Anomalie.objects.create(candidature=candidature, **data)
    Anomalie.objects.filter(pk=a.pk).update(created_at=created_at)
    a.refresh_from_db()
    return a


def piece(candidature, type_, nom, taille=122880, recue_le=None, **kw):
    return Piece.objects.create(
        candidature=candidature,
        type=type_,
        nom_fichier=nom,
        storage_key=f"fixture://{nom}",
        content_type=kw.pop("content_type", "application/pdf"),
        taille=taille,
        recue_le=recue_le or at(2026, 3, 16, 9, 0),
        **kw,
    )


IDENTITE = ("ALAOUI", "Samira", "ZZ999001", "samira.alaoui@example.org", "+212600000123", "Agadir")


# --------------------------------------------------------------------------------------
# CA1 / CA2 — Contenu de l'en-tête et de l'identité, rôles direction et admin
# --------------------------------------------------------------------------------------


def assert_identite(response):
    text = page_text(response)
    for value in IDENTITE:
        assert value in text, value
    assert_date_present(text, date(1990, 5, 10))
    statut = one(parse(response), "data-statut")
    assert "À vérifier" in statut.text()
    assert_date_present(text, local(at(2026, 3, 15, 10, 30)).date())


def test_direction_voit_identite_statut_et_date(client, direction_user, cand):
    """CA1 — direction avec TOTP : 200, identité complète, statut en français, date de
    candidature."""
    assert_identite(ok(client, direction_user, cand))


def test_admin_voit_le_meme_contenu(client, admin_user, cand):
    """CA2 — admin : 200 avec le même contenu."""
    assert_identite(ok(client, admin_user, cand))


def test_nom_en_majuscules_et_prenom_dans_l_en_tete(client, direction_user, cand):
    """Règle 2 — L'en-tête affiche le nom en majuscules et le prénom."""
    text = page_text(ok(client, direction_user, cand))
    assert re.search(r"ALAOUI\s+Samira|Samira\s+ALAOUI", text)


def test_champs_identite_vides_non_renseigne(client, direction_user, poste):
    """Règle 3 — Champ d'identité vide : « Non renseigné »."""
    c = new_candidature(
        poste=poste,
        candidat=new_candidat(nom="Vide", prenom="Champs", email="", telephone="", ville=""),
    )
    text = page_text(ok(client, direction_user, c))
    assert "Non renseigné" in text


def test_url_stable_fondee_sur_l_identifiant_interne(cand):
    """Règle 1 — URL ``/candidatures/<id>/`` fondée sur l'identifiant interne."""
    Candidature.objects.filter(pk=cand.pk).update(source_id="SITE-99999")
    assert url(cand) == f"/candidatures/{cand.pk}/"


# --------------------------------------------------------------------------------------
# CA3 / CA4 / CA5 — Permissions
# --------------------------------------------------------------------------------------


def test_non_connecte_redirige_vers_connexion_avec_next(client, cand):
    """CA3 — Non connecté : redirection vers la connexion avec ``next``, sans audit."""
    target = url(cand)
    response = client.get(target)
    assert response.status_code == 302
    parsed = urlparse(response.url)
    assert parsed.path == reverse("account_login")
    assert parse_qs(parsed.query)["next"] == [target]
    assert not view_entries().exists()


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
@pytest.mark.parametrize("htmx", [False, True], ids=["page", "htmx"])
def test_roles_vague_2_refuses_403(client, cand, role, htmx):
    """CA4 — consultant ou client : 403 (page et requête HTMX), aucune donnée du candidat."""
    user = make_user(f"{role}@example.com", role)
    response = get(client, user, cand, **(HX if htmx else {}))
    assert response.status_code == 403
    body = response.content.decode()
    for value in IDENTITE + ("Alaoui", "Master génie de l'eau"):
        assert value not in body
    assert not view_entries().exists()


def test_sans_totp_redirige_vers_activation(client, user_without_mfa, cand):
    """Rôles — connecté sans TOTP actif : redirection vers l'activation du TOTP."""
    response = get(client, user_without_mfa, cand)
    assert response.status_code == 302
    assert urlparse(response.url).path == reverse("mfa_activate_totp")
    assert "ALAOUI" not in response.content.decode()


def test_compte_desactive_refuse(client, cand):
    """Rôles — compte désactivé : pas de session (redirection connexion) ou 403."""
    user = make_user("inactif@example.com", Role.DIRECTION)
    client.force_login(user)
    user.is_active = False
    user.save()
    response = client.get(url(cand))
    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert urlparse(response.url).path == reverse("account_login")
    assert "ALAOUI" not in response.content.decode()


def test_candidature_inexistante_404(client, direction_user, cand):
    """CA5 — Identifiant inexistant : 404."""
    response = get(client, direction_user, url(cand.pk + 10_000))
    assert response.status_code == 404


# --------------------------------------------------------------------------------------
# CA6 — Données interdites
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("htmx", [False, True], ids=["page", "htmx"])
def test_sexe_region_et_age_jamais_affiches(client, direction_user, cand, htmx):
    """CA6 — Ni sexe, ni région, ni libellés « Sexe » / « Région », ni âge calculé dans le
    HTML (ni en texte, ni en attribut)."""
    response = ok(client, direction_user, cand, **(HX if htmx else {}))
    html = response.content.decode()
    assert "Souss-Massa" not in html
    assert not re.search(r"\bSexe\b", html, re.I)
    assert not re.search(r"\bR[ée]gion\b", html, re.I)
    assert "Femme" not in html
    assert not re.search(r"\bâge\b", html, re.I)
    today = timezone.localdate()
    age = today.year - 1990 - ((today.month, today.day) < (5, 10))
    for years in (age - 1, age, age + 1):
        assert not re.search(rf"\b{years}\s*ans\b", html), years
    root = parse(html)
    for node in root.iter():
        for name, value in node.attrs.items():
            assert not re.search(r"sexe|gender|region|age\b", name, re.I), name
            assert value != "Souss-Massa"


# --------------------------------------------------------------------------------------
# CA7 / CA8 — Cible de la candidature
# --------------------------------------------------------------------------------------


def test_en_tete_poste_de_concours_et_lien_retour(client, direction_user, cand, poste):
    """CA7 — Concours, organisation, référence et intitulé du poste ; lien vers la liste des
    candidatures du poste."""
    response = ok(client, direction_user, cand)
    text = page_text(response)
    for value in ("Concours fictif Atlas 2026", "Agence Fictive Atlas", "P-07"):
        assert value in text, value
    assert "Ingénieur hydraulique" in text
    retour = one(parse(response), "data-retour-liste")
    assert retour.tag == "a"
    expected = reverse("recruitment:poste_candidatures", args=[poste.concours_id, poste.pk])
    assert urlparse(retour.attrs["href"]).path == expected


def test_en_tete_offre_confidentielle(client, direction_user):
    """CA8 — Offre confidentielle : intitulé de l'offre et « Entreprise confidentielle » au lieu
    de l'organisation (libellé harmonisé par KAN-38, règle 3)."""
    org = Organisation.objects.create(nom="Groupe Privé Fictif Omega", type=Organisation.Type.PRIVE)
    offre = OffreEmploi.objects.create(
        organisation=org, intitule="Responsable logistique fictif", entreprise_confidentielle=True
    )
    c = new_candidature(offre=offre)
    response = ok(client, direction_user, c)
    html = response.content.decode()
    text = page_text(response)
    assert "Responsable logistique fictif" in text
    assert "Entreprise confidentielle" in text
    assert "Groupe Privé Fictif Omega" not in html


def test_en_tete_offre_non_confidentielle_affiche_organisation(client, direction_user):
    """Règle 2 — Offre non confidentielle : intitulé et organisation."""
    org = Organisation.objects.create(nom="Société Fictive Sigma", type=Organisation.Type.PRIVE)
    offre = OffreEmploi.objects.create(organisation=org, intitule="Comptable fictif")
    text = page_text(ok(client, direction_user, new_candidature(offre=offre)))
    assert "Comptable fictif" in text
    assert "Société Fictive Sigma" in text
    assert "Entreprise confidentielle" not in text


# --------------------------------------------------------------------------------------
# CA9 à CA12 — Données déclarées
# --------------------------------------------------------------------------------------


def test_formations_dans_l_ordre_du_json(client, direction_user, cand):
    """CA9 — Deux formations : intitulé, niveau, spécialité, établissement, année, dans
    l'ordre du JSON."""
    rows = parse(ok(client, direction_user, cand)).with_attr("data-formation")
    assert len(rows) == 2
    for row, f in zip(rows, FORMATIONS, strict=True):
        text = row.text()
        for key in ("intitule", "niveau", "specialite", "etablissement", "annee_obtention"):
            assert str(f[key]) in text, (key, text)


def test_formations_ordre_inverse_respecte(client, direction_user, poste):
    """CA9 — L'ordre affiché suit le JSON (et non un tri par année)."""
    c = new_candidature(poste=poste, donnees_declarees={"formation": FORMATIONS[::-1]})
    rows = parse(ok(client, direction_user, c)).with_attr("data-formation")
    assert [("Licence" in r.text()) for r in rows] == [True, False]


def test_experiences_dates_jj_mm_aaaa_et_en_cours(client, direction_user, cand):
    """CA10 — Employeur, poste, début et fin en JJ/MM/AAAA ; sans fin : « en cours »."""
    rows = parse(ok(client, direction_user, cand)).with_attr("data-experience")
    assert len(rows) == 2
    first, second = (r.text() for r in rows)
    for value in ("Bureau d'études fictif Gamma", "Ingénieur junior", "01/09/2015", "30/06/2019"):
        assert value in first, value
    for value in ("Régie fictive Delta", "Ingénieur hydraulicien", "01/07/2019"):
        assert value in second, value
    assert "en cours" in second.lower()
    assert "en cours" not in first.lower()
    assert rows[1].with_attr("data-date", "en_cours")
    assert not rows[0].with_attr("data-date", "en_cours")
    assert "2015-09-01" not in first


def test_experience_fin_chaine_vide_en_cours(client, direction_user, poste):
    """Règle 6 — Fin vide (chaîne vide) et début renseigné : « en cours »."""
    exp = [{"debut": "2021-01-04", "fin": "", "employeur": "Entreprise fictive Z", "poste": "X"}]
    c = new_candidature(poste=poste, donnees_declarees={"experiences": exp})
    rows = parse(ok(client, direction_user, c)).with_attr("data-experience")
    assert len(rows) == 1
    assert "04/01/2021" in rows[0].text()
    assert "en cours" in rows[0].text().lower()


def test_donnees_declarees_vides(client, direction_user, poste):
    """CA11 — ``{}`` : « Aucune formation déclarée » et « Expérience non renseignée »."""
    c = new_candidature(poste=poste, donnees_declarees={})
    text = page_text(ok(client, direction_user, c))
    assert "Aucune formation déclarée" in text
    assert "Expérience non renseignée" in text
    assert "Aucune expérience déclarée" not in text


def test_experiences_liste_vide(client, direction_user, poste):
    """CA11 — ``experiences = []`` : « Aucune expérience déclarée »."""
    c = new_candidature(poste=poste, donnees_declarees={"formation": [], "experiences": []})
    text = page_text(ok(client, direction_user, c))
    assert "Aucune expérience déclarée" in text
    assert "Expérience non renseignée" not in text
    assert "Aucune formation déclarée" in text


def test_dates_manquante_et_illisible(client, direction_user, poste):
    """CA12 — ``debut`` null : « Date manquante » ; ``"2020-13-45"`` : valeur brute marquée
    « Date illisible » ; page 200."""
    exp = [
        {"debut": None, "fin": "2018-12-31", "employeur": "Employeur fictif A", "poste": "P1"},
        {
            "debut": "2020-13-45",
            "fin": "2021-03-01",
            "employeur": "Employeur fictif B",
            "poste": "P2",
        },
    ]
    c = new_candidature(poste=poste, donnees_declarees={"experiences": exp})
    rows = parse(ok(client, direction_user, c)).with_attr("data-experience")
    assert len(rows) == 2
    assert "Date manquante" in rows[0].text()
    assert rows[0].with_attr("data-date", "manquante")
    assert "31/12/2018" in rows[0].text()
    assert "2020-13-45" in rows[1].text()
    assert "Date illisible" in rows[1].text()
    assert rows[1].with_attr("data-date", "illisible")
    assert "01/03/2021" in rows[1].text()


def test_donnees_declarees_mal_formees(client, direction_user, poste):
    """Cas limites — ``formation`` n'est pas une liste, entrée sans clé attendue : 200 et
    « Non renseigné » pour les champs absents."""
    c = new_candidature(poste=poste, donnees_declarees={"formation": "Bac+5", "experiences": [{}]})
    assert get(client, direction_user, c).status_code == 200
    c2 = new_candidature(
        poste=poste,
        donnees_declarees={"formation": [{"intitule": "Diplôme fictif seul"}], "experiences": []},
    )
    rows = parse(ok(client, direction_user, c2)).with_attr("data-formation")
    assert len(rows) == 1
    assert "Diplôme fictif seul" in rows[0].text()
    assert "Non renseigné" in rows[0].text()


# --------------------------------------------------------------------------------------
# CA13 / CA14 / CA21 — Pièces
# --------------------------------------------------------------------------------------


def _storage_forbidden(*args, **kwargs):
    raise AssertionError("Accès au stockage de fichiers interdit dans la fiche")


def test_trois_pieces_listees_sans_acces_stockage(client, direction_user, cand):
    """CA13 — CV, diplôme, attestation : type, nom, taille lisible, date de réception ; aucun
    appel au stockage (FixtureSource.read_piece et boto3.client échouent s'ils sont appelés)."""
    p1 = piece(cand, Piece.Type.CV, "cv_fictif.pdf", taille=122880)
    p2 = piece(cand, Piece.Type.DIPLOME, "diplome_fictif.pdf", taille=256000)
    p3 = piece(
        cand,
        Piece.Type.ATTESTATION_TRAVAIL,
        "attestation_fictive.pdf",
        taille=51200,
        recue_le=at(2026, 3, 17, 11, 0),
    )
    with (
        mock.patch(
            "integrations.site_source.fixture_source.FixtureSource.read_piece",
            side_effect=_storage_forbidden,
        ) as read_piece,
        mock.patch("boto3.client", side_effect=_storage_forbidden) as boto_client,
    ):
        response = ok(client, direction_user, cand)
    assert not read_piece.called
    assert not boto_client.called
    root = parse(response)
    expected = {
        p1.pk: ("CV", "cv_fictif.pdf", "120", "16/03/2026"),
        p2.pk: ("Diplôme", "diplome_fictif.pdf", "250", "16/03/2026"),
        p3.pk: ("Attestation de travail", "attestation_fictive.pdf", "50", "17/03/2026"),
    }
    nodes = root.with_attr("data-piece")
    assert {int(n.attrs["data-piece"]) for n in nodes} == set(expected)
    for pk, values in expected.items():
        text = one(root, "data-piece", str(pk)).text()
        type_label, nom, kilo, recue = values
        for value in (type_label, nom, recue):
            assert value in text, (pk, value, text)
        # Taille lisible (la spec donne « 120 Ko » en exemple) : valeur en kilo-octets.
        assert re.search(rf"(?<![\d,.]){kilo}([,.]0)?\s*Ki?o\b", text), (pk, text)
        assert "application/pdf" in text


def test_aucune_piece(client, direction_user, cand):
    """CA14 — Sans pièce : « Aucune pièce reçue »."""
    text = page_text(ok(client, direction_user, cand))
    assert "Aucune pièce reçue" in text


def test_emplacement_visionneuse_prevu_et_vide(client, direction_user, cand):
    """Règle 7 — Emplacement de la visionneuse (KAN-12) prévu mais vide."""
    piece(cand, Piece.Type.CV, "cv_fictif.pdf")
    root = parse(ok(client, direction_user, cand))
    slot = one(root, "data-kan12")
    assert not slot.find_all(lambda n: n.tag in ("iframe", "embed", "object", "img"))


def test_indicateur_anomalie_sur_piece(client, direction_user, cand):
    """CA21 — Une pièce visée par une anomalie non résolue porte un indicateur ; les autres
    (sans anomalie ou anomalie résolue) n'en portent pas."""
    visee = piece(cand, Piece.Type.DIPLOME, "diplome_fictif.pdf")
    saine = piece(cand, Piece.Type.CV, "cv_fictif.pdf")
    resolue = piece(cand, Piece.Type.LETTRE, "lettre_fictive.pdf")
    anomalie(cand, at(2026, 3, 18, 8, 0), piece=visee, description="Date du diplôme incohérente")
    anomalie(
        cand,
        at(2026, 3, 18, 9, 0),
        piece=resolue,
        resolue=True,
        resolue_par=direction_user,
        resolue_le=at(2026, 3, 19, 9, 0),
    )
    root = parse(ok(client, direction_user, cand))
    assert one(root, "data-piece", str(visee.pk)).with_attr("data-piece-anomalie")
    assert not one(root, "data-piece", str(saine.pk)).with_attr("data-piece-anomalie")
    assert not one(root, "data-piece", str(resolue.pk)).with_attr("data-piece-anomalie")


# --------------------------------------------------------------------------------------
# CA15 à CA18 — Éligibilité
# --------------------------------------------------------------------------------------

DETAILS = [
    {
        "critere": "niveau_etudes",
        "libelle": "Niveau d'études",
        "verdict": "eligible",
        "raison": "Niveau Bac+5 atteint",
        "attendu": "Bac+5",
        "constate": "Bac+5 (Master)",
    },
    {
        "critere": "specialite",
        "libelle": "Spécialité",
        "verdict": "non_eligible",
        "raison": "Spécialité hors liste acceptée",
        "attendu": "Hydraulique, Génie de l'eau",
        "constate": "Génie civil",
    },
    {
        "critere": "experience",
        "libelle": "Expérience professionnelle",
        "verdict": "a_verifier",
        "raison": "Une date de fin manque",
        "attendu": "5 ans minimum",
        "constate": "4,8 ans",
    },
]


def test_derniere_verification_affichee_et_historique(client, direction_user, cand):
    """CA15 — « à vérifier » puis « éligible » : verdict global « Éligible » avec version et
    date ; la précédente est dans l'historique des vérifications."""
    old = verification(cand, Verdict.A_VERIFIER, at(2026, 3, 20, 9, 0), version="0.9.0")
    new = verification(cand, Verdict.ELIGIBLE, at(2026, 4, 2, 14, 15), version="1.1.0")
    response = ok(client, direction_user, cand)
    root = parse(response)
    verdict = one(root, "data-verdict-global")
    assert "Éligible" in verdict.text()
    assert "Non éligible" not in verdict.text()
    histo = one(root, "data-historique-verifications")
    histo_text = histo.text()
    main_text = root.text()
    assert "1.1.0" in main_text
    assert_date_present(main_text, local(new.created_at).date())
    assert "0.9.0" in histo_text
    assert "À vérifier" in histo_text
    assert_date_present(histo_text, local(old.created_at).date())


def test_egalite_de_date_departagee_par_identifiant(client, direction_user, cand):
    """Règle 8 — Même ``created_at`` : la vérification d'identifiant le plus élevé l'emporte."""
    same = at(2026, 4, 1, 8, 0)
    verification(cand, Verdict.ELIGIBLE, same, version="1.0.0")
    verification(cand, Verdict.NON_ELIGIBLE, same, version="1.0.1")
    verdict = one(parse(ok(client, direction_user, cand)), "data-verdict-global")
    assert "Non éligible" in verdict.text()


def test_tableau_criteres_dans_l_ordre(client, direction_user, cand):
    """CA16 — Libellé, verdict en texte, attendu, constaté, raison, dans l'ordre de
    ``details``."""
    verification(cand, Verdict.A_VERIFIER, at(2026, 4, 2, 9, 0), details=DETAILS)
    root = parse(ok(client, direction_user, cand))
    table = one(root, "data-criteres")
    lines = table.with_attr("data-critere")
    assert [n.attrs["data-critere"] for n in lines] == ["niveau_etudes", "specialite", "experience"]
    labels = {"eligible": "Éligible", "non_eligible": "Non éligible", "a_verifier": "À vérifier"}
    for line, d in zip(lines, DETAILS, strict=True):
        text = line.text()
        for key in ("libelle", "raison", "attendu", "constate"):
            assert d[key] in text, (key, text)
        assert labels[d["verdict"]] in text


def test_sources_en_libelle_lisible(client, direction_user, cand):
    """Règle 8 — Les sources sont affichées en libellé lisible (« Données déclarées »)."""
    verification(cand, Verdict.ELIGIBLE, at(2026, 4, 2, 9, 0), details=DETAILS[:1])
    text = page_text(ok(client, direction_user, cand))
    assert "Données déclarées" in text


def test_details_vides_ou_incomplets(client, direction_user, cand, poste):
    """Cas limites — ``details`` vide, ou critère sans attendu / constaté : cellules « — »,
    pas d'erreur."""
    verification(cand, Verdict.A_VERIFIER, at(2026, 4, 2, 9, 0), details=[])
    assert get(client, direction_user, cand).status_code == 200
    other = new_candidature(poste=poste)
    details = [
        {"critere": "autres", "libelle": "Autres critères", "verdict": "a_verifier", "raison": "R"}
    ]
    verification(other, Verdict.A_VERIFIER, at(2026, 4, 2, 9, 0), details=details)
    line = one(parse(ok(client, direction_user, other)), "data-critere", "autres")
    assert "—" in line.text()


def test_aucune_verification(client, direction_user, cand):
    """CA17 — Sans vérification : « Éligibilité non encore vérifiée », 200."""
    text = page_text(ok(client, direction_user, cand))
    assert "Éligibilité non encore vérifiée" in text


def test_ecart_verdict_statut_bandeau(client, direction_user, poste):
    """CA18 — Statut « convoquée » par décision humaine, vérification plus récente « non
    éligible » : bandeau d'écart, statut affiché « Convoquée », statut en base inchangé."""
    c = new_candidature(poste=poste, statut=S.CONVOQUEE)
    decision(c, direction_user, S.CONVOQUEE, "Convocation à l'oral", at(2026, 4, 1, 10, 0))
    verification(c, Verdict.NON_ELIGIBLE, at(2026, 4, 5, 10, 0))
    root = parse(ok(client, direction_user, c))
    assert root.with_attr("data-ecart")
    banner = root.with_attr("data-ecart")[0].text()
    assert "Non éligible" in banner or "non éligible" in banner
    assert "01/04/2026" in banner or "1 avril 2026" in banner
    assert "Convoquée" in one(root, "data-statut").text()
    c.refresh_from_db()
    assert c.statut == S.CONVOQUEE


def test_pas_de_bandeau_sans_decision_humaine(client, direction_user, poste):
    """Règle 10 — Sans décision humaine, pas de bandeau d'écart."""
    c = new_candidature(poste=poste, statut=S.RECUE)
    verification(c, Verdict.NON_ELIGIBLE, at(2026, 4, 5, 10, 0))
    assert not parse(ok(client, direction_user, c)).with_attr("data-ecart")


def test_pas_de_bandeau_si_coherent(client, direction_user, poste):
    """Règle 10 — Décision, statut et dernière vérification concordants : pas de bandeau."""
    c = new_candidature(poste=poste, statut=S.ELIGIBLE)
    decision(c, direction_user, S.ELIGIBLE, "Conforme", at(2026, 4, 1, 10, 0))
    verification(c, Verdict.ELIGIBLE, at(2026, 4, 5, 10, 0))
    assert not parse(ok(client, direction_user, c)).with_attr("data-ecart")


def test_statut_et_verdict_inconnus_valeur_brute(client, direction_user, poste):
    """Cas limites — Statut ou verdict inconnu : valeur brute affichée, pas d'erreur 500."""
    c = new_candidature(poste=poste)
    Candidature.objects.filter(pk=c.pk).update(statut="statut_mystere")
    details = [
        {"critere": "x", "libelle": "Critère X", "verdict": "verdict_mystere", "raison": "R"}
    ]
    verification(c, Verdict.ELIGIBLE, at(2026, 4, 5, 10, 0), details=details)
    VerificationEligibilite.objects.filter(candidature=c).update(resultat="resultat_mystere")
    root = parse(ok(client, direction_user, c))
    assert "statut_mystere" in one(root, "data-statut").text()
    assert "verdict_mystere" in one(root, "data-critere", "x").text()
    assert "resultat_mystere" in one(root, "data-verdict-global").text()


# --------------------------------------------------------------------------------------
# CA19 / CA20 — Anomalies
# --------------------------------------------------------------------------------------


def test_anomalies_ordre_details_et_compteur(client, direction_user, admin_user, cand):
    """CA19 — Non résolue (haute) en premier avec type, gravité, description ; résolue avec
    auteur et date ; en-tête « 1 anomalie non résolue »."""
    ouverte = anomalie(
        cand,
        at(2026, 3, 18, 8, 0),
        type=Anomalie.Type.INCOHERENCE_DATES,
        gravite=Anomalie.Gravite.HAUTE,
        description="Fin d'expérience antérieure au début",
    )
    fermee = anomalie(
        cand,
        at(2026, 3, 25, 8, 0),
        type=Anomalie.Type.PIECE_MANQUANTE,
        gravite=Anomalie.Gravite.MOYENNE,
        description="Attestation absente",
        resolue=True,
        resolue_par=admin_user,
        resolue_le=at(2026, 3, 26, 15, 45),
    )
    root = parse(ok(client, direction_user, cand))
    nodes = root.with_attr("data-anomalie")
    assert [int(n.attrs["data-anomalie"]) for n in nodes] == [ouverte.pk, fermee.pk]
    first, second = nodes
    assert first.attrs.get("data-resolue") == "non"
    assert second.attrs.get("data-resolue") == "oui"
    for value in ("Incohérence de dates", "Haute", "Fin d'expérience antérieure au début"):
        assert value in first.text(), value
    assert_date_present(first.text(), date(2026, 3, 18))
    assert "admin@example.com" in second.text()
    assert_date_present(second.text(), date(2026, 3, 26))
    compteur = one(root, "data-nb-anomalies")
    assert "1 anomalie non résolue" in compteur.text()


def test_anomalies_non_resolues_par_date_decroissante(client, direction_user, cand):
    """Règle 11 — Non résolues d'abord, puis par date décroissante."""
    a1 = anomalie(cand, at(2026, 3, 1, 8, 0), description="Ancienne ouverte")
    a2 = anomalie(cand, at(2026, 3, 10, 8, 0), description="Récente ouverte")
    r1 = anomalie(
        cand,
        at(2026, 3, 5, 8, 0),
        resolue=True,
        resolue_par=direction_user,
        resolue_le=at(2026, 3, 6, 8, 0),
    )
    r2 = anomalie(
        cand,
        at(2026, 3, 20, 8, 0),
        resolue=True,
        resolue_par=direction_user,
        resolue_le=at(2026, 3, 21, 8, 0),
    )
    root = parse(ok(client, direction_user, cand))
    order = [int(n.attrs["data-anomalie"]) for n in root.with_attr("data-anomalie")]
    assert order == [a2.pk, a1.pk, r2.pk, r1.pk]
    assert "2 anomalies non résolues" in one(root, "data-nb-anomalies").text()


def test_lien_vers_candidature_liee_doublon(client, direction_user, cand, poste):
    """CA20 — Anomalie « doublon de candidat » : lien vers la fiche de la candidature liée."""
    autre = new_candidature(poste=poste)
    a = anomalie(
        cand,
        at(2026, 3, 18, 8, 0),
        type=Anomalie.Type.DOUBLON_CANDIDAT,
        candidature_liee=autre,
        description="Même email normalisé",
    )
    root = parse(ok(client, direction_user, cand))
    node = one(root, "data-anomalie", str(a.pk))
    links = node.with_attr("data-candidature-liee")
    assert links
    hrefs = [n.attrs.get("href") or "" for n in links] + [
        n.attrs.get("href") or "" for link in links for n in link.find_all(lambda x: x.tag == "a")
    ]
    assert url(autre) in [urlparse(h).path for h in hrefs]
    assert "Doublon de candidat" in node.text()


def test_candidature_liee_indisponible(client, direction_user, cand):
    """Cas limites — Doublon dont la candidature liée a disparu : « candidature liée
    indisponible »."""
    a = anomalie(
        cand,
        at(2026, 3, 18, 8, 0),
        type=Anomalie.Type.DOUBLON_CANDIDAT,
        candidature_liee=None,
        description="Doublon fictif",
    )
    node = one(parse(ok(client, direction_user, cand)), "data-anomalie", str(a.pk))
    assert "candidature liée indisponible" in node.text().lower()


# --------------------------------------------------------------------------------------
# CA22 / CA23 — Décisions
# --------------------------------------------------------------------------------------


def test_decisions_ordre_et_en_vigueur(client, direction_user, admin_user, cand):
    """CA22 — Trois décisions : de la plus récente à la plus ancienne, décision, motif complet,
    auteur, date-heure ; seule la plus récente est « en vigueur »."""
    long_motif = "Motif fictif détaillé. " * 40 + "FIN-DU-MOTIF"
    d1 = decision(cand, direction_user, S.A_VERIFIER, "Pièce à contrôler", at(2026, 4, 1, 9, 5))
    d2 = decision(cand, admin_user, S.ELIGIBLE, long_motif, at(2026, 4, 3, 11, 20))
    d3 = decision(cand, direction_user, S.CONVOQUEE, "Convoqué à l'oral", at(2026, 4, 8, 16, 40))
    root = parse(ok(client, direction_user, cand))
    nodes = root.with_attr("data-decision")
    assert [int(n.attrs["data-decision"]) for n in nodes] == [d3.pk, d2.pk, d1.pk]
    expected = [
        (d3, "Convoquée", "Convoqué à l'oral", "direction@example.com"),
        (d2, "Éligible", long_motif.strip(), "admin@example.com"),
        (d1, "À vérifier", "Pièce à contrôler", "direction@example.com"),
    ]
    for node, (d, label, motif, auteur) in zip(nodes, expected, strict=True):
        text = node.text()
        assert label in text
        assert re.sub(r"\s+", " ", motif) in text
        assert auteur in text
        assert_date_present(text, local(d.created_at).date())
        assert_time_present(text, d.created_at)
    assert "data-en-vigueur" in nodes[0].attrs or nodes[0].with_attr("data-en-vigueur")
    assert "en vigueur" in nodes[0].text().lower()
    for node in nodes[1:]:
        assert not ("data-en-vigueur" in node.attrs or node.with_attr("data-en-vigueur"))
        assert "en vigueur" not in node.text().lower()
    assert len(root.with_attr("data-en-vigueur")) == 1


def test_aucune_decision(client, direction_user, cand):
    """CA23 — Sans décision : « Aucune décision enregistrée »."""
    assert "Aucune décision enregistrée" in page_text(ok(client, direction_user, cand))


def test_auteur_desactive_reste_affiche(client, direction_user, cand):
    """Cas limites — Auteur d'une décision désactivé : son nom reste affiché."""
    ancien = make_user("ancien.associe@example.com", Role.DIRECTION)
    decision(cand, ancien, S.ELIGIBLE, "Décision fictive", at(2026, 4, 1, 9, 0))
    ancien.is_active = False
    ancien.save()
    node = one(parse(ok(client, direction_user, cand)), "data-decision")
    assert "ancien.associe@example.com" in node.text()


# --------------------------------------------------------------------------------------
# CA24 — Lecture seule
# --------------------------------------------------------------------------------------


def _forbidden(*args, **kwargs):
    raise AssertionError("Écriture métier, tâche Celery ou appel IA interdit dans la fiche")


def _snapshot(c):
    return {
        "candidature": list(Candidature.objects.values_list("pk", "statut", "updated_at")),
        "verif": list(VerificationEligibilite.objects.values_list("pk", "resultat", "created_at")),
        "decision": list(Decision.objects.values_list("pk", "decision", "motif")),
        "anomalie": list(Anomalie.objects.values_list("pk", "resolue", "resolue_le")),
        "score": Score.objects.count(),
    }


def test_lecture_seule(client, direction_user, cand):
    """CA24 — GET : aucun objet métier créé ni modifié ; aucune tâche Celery ni appel IA."""
    verification(cand, Verdict.A_VERIFIER, at(2026, 4, 2, 9, 0), details=DETAILS)
    decision(cand, direction_user, S.A_VERIFIER, "Contrôle", at(2026, 4, 3, 9, 0))
    anomalie(cand, at(2026, 4, 4, 9, 0))
    before = _snapshot(cand)
    with (
        mock.patch("celery.app.task.Task.apply_async", side_effect=_forbidden) as apply_async,
        mock.patch("ai.client.AIClient.structured", side_effect=_forbidden) as ai_call,
        mock.patch("apps.evaluation.services.run_eligibility", side_effect=_forbidden) as engine,
    ):
        ok(client, direction_user, cand)
    assert not apply_async.called
    assert not ai_call.called
    assert not engine.called
    assert _snapshot(cand) == before


def test_post_ne_modifie_rien(client, direction_user, cand):
    """Règle 13 — Une requête POST sur la fiche n'effectue aucune écriture métier."""
    before = _snapshot(cand)
    client.force_login(direction_user)
    response = client.post(url(cand), {"statut": "rejetee"})
    assert response.status_code != 500
    assert _snapshot(cand) == before


# --------------------------------------------------------------------------------------
# CA25 — Nombre de requêtes borné
# --------------------------------------------------------------------------------------


def _populate(c, nb_pieces, nb_anomalies, nb_verifs, nb_decisions, users, autre):
    pieces = [
        piece(c, Piece.Type.AUTRE, f"piece_fictive_{i}.pdf", taille=1024 * (i + 1))
        for i in range(nb_pieces)
    ]
    base = at(2026, 3, 1, 8, 0)
    for i in range(nb_anomalies):
        resolue = i % 2 == 1
        anomalie(
            c,
            base + timedelta(hours=i),
            piece=pieces[i % len(pieces)],
            candidature_liee=autre,
            type=Anomalie.Type.DOUBLON_CANDIDAT,
            resolue=resolue,
            resolue_par=users[i % len(users)] if resolue else None,
            resolue_le=base + timedelta(days=1, hours=i) if resolue else None,
        )
    for i in range(nb_verifs):
        verification(c, Verdict.A_VERIFIER, base + timedelta(hours=i), details=DETAILS)
    for i in range(nb_decisions):
        decision(c, users[i % len(users)], S.A_VERIFIER, f"Motif {i}", base + timedelta(hours=i))


def _count_queries(client, c) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url(c))
    assert response.status_code == 200
    return len(ctx.captured_queries)


def test_nombre_de_requetes_independant_des_volumes(client, direction_user, admin_user, poste):
    """CA25 — 50 pièces, 50 anomalies, 20 vérifications, 20 décisions : même nombre de
    requêtes SQL qu'avec une seule occurrence de chaque (tolérance fixée à 0)."""
    users = [direction_user, admin_user]
    autre = new_candidature(poste=poste)
    petite = new_candidature(poste=poste)
    grosse = new_candidature(poste=poste)
    # Deux auteurs et deux résolveurs dans les deux jeux pour neutraliser les caches.
    _populate(petite, 2, 2, 1, 2, users, autre)
    _populate(grosse, 50, 50, 20, 20, users, autre)
    client.force_login(direction_user)
    client.get(url(autre))  # échauffement (session, contenttypes…)
    small = _count_queries(client, petite)
    large = _count_queries(client, grosse)
    assert large == small, f"{large} requêtes contre {small}"


# --------------------------------------------------------------------------------------
# CA26 / CA27 — RTL et échappement
# --------------------------------------------------------------------------------------


def test_page_en_arabe_rtl(client, direction_user, cand):
    """CA26 — Langue active ``ar`` : ``<html dir="rtl">``, 200, contenu non traduit en
    français."""
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    response = get(client, direction_user, cand, HTTP_ACCEPT_LANGUAGE="ar")
    assert response.status_code == 200
    html = parse(response).find_all(lambda n: n.tag == "html")[0]
    assert html.attrs.get("dir") == "rtl"
    assert "ALAOUI" in page_text(response)


def test_html_echappe_motif_et_description(client, direction_user, cand):
    """CA27 — ``<script>alert(1)</script>`` dans un motif ou une description : échappé."""
    payload = "<script>alert(1)</script>"
    decision(cand, direction_user, S.A_VERIFIER, f"Motif {payload}", at(2026, 4, 1, 9, 0))
    anomalie(cand, at(2026, 4, 2, 9, 0), description=f"Description {payload}")
    html = ok(client, direction_user, cand).content.decode()
    assert "alert(1)</script>" not in html
    assert "<script>alert(1)" not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 2


# --------------------------------------------------------------------------------------
# Traçabilité, conformité et cas limites divers
# --------------------------------------------------------------------------------------


def test_en_tete_cache_control_no_store(client, direction_user, cand):
    """Traçabilité — ``Cache-Control: no-store`` sur la fiche."""
    response = ok(client, direction_user, cand)
    assert "no-store" in response.get("Cache-Control", "")


def test_date_synchronisation_site_affichee(client, direction_user, cand):
    """Cas limites — « Données du site synchronisées le … » avec ``source_synced_at``."""
    Candidature.objects.filter(pk=cand.pk).update(
        source_id="SITE-1", source_synced_at=at(2026, 4, 9, 7, 30)
    )
    text = page_text(ok(client, direction_user, cand))
    assert "Données du site synchronisées le" in text
    assert_date_present(text, date(2026, 4, 9))


def test_consultation_journalisee(client, direction_user, cand):
    """Traçabilité (KAN-14, bonus) — Une ouverture de fiche crée une entrée ``view`` sur la
    candidature, avec l'acteur."""
    ok(client, direction_user, cand)
    ct = ContentType.objects.get_for_model(Candidature)
    entries = view_entries().filter(content_type=ct, object_id=str(cand.pk))
    assert entries.count() == 1
    assert entries.get().actor == direction_user


def test_pas_de_journal_pour_un_acces_refuse(client, cand):
    """Traçabilité — Un accès refusé (403) ne journalise aucune consultation du dossier."""
    user = make_user("consultant@example.com", Role.CONSULTANT)
    assert get(client, user, cand).status_code == 403
    assert not view_entries().exists()


def test_gabarit_proprietes_logiques_rtl():
    """Règle 15 — Mise en page en propriétés logiques (pas de ``ml-``/``mr-``/``pl-``/``pr-``,
    ``text-left``/``text-right``, ``left-``/``right-``) et libellés traduisibles."""
    base = settings.BASE_DIR
    for name in ("candidature_detail.html", "_date_affichee.html"):
        content = (base / "templates" / "recruitment" / name).read_text(encoding="utf-8")
        assert not re.search(
            r"(?<![\w-])(ml|mr|pl|pr|left|right)-[0-9a-z]|text-(left|right)|float-(left|right)",
            content,
        ), name
    detail = (base / "templates" / "recruitment" / "candidature_detail.html").read_text("utf-8")
    assert "{% load i18n %}" in detail
    assert "{% translate" in detail or "{% blocktranslate" in detail


# --- Relecture conformité KAN-11 ------------------------------------------------------


@pytest.mark.parametrize(
    "details",
    [
        [{"libelle": "Niveau", "verdict": "eligible"}],  # sans clé « critere »
        "pas une liste",
        {"critere": "niveau_etudes"},
        [None, 3, {"critere": "experience", "verdict": "a_verifier"}],
    ],
)
def test_details_mal_formes_sans_erreur_500(client, direction_user, cand, details):
    verification(cand, "a_verifier", at(2026, 3, 1, 10), details=details)
    response = ok(client, direction_user, cand)
    assert "data-criteres" in response.content.decode()


def test_pas_de_bandeau_quand_statut_favorable_et_verdict_eligible(client, direction_user, cand):
    decision(cand, direction_user, "convoquee", "Convocation à l'oral du 12/04", at(2026, 3, 2, 9))
    Candidature.objects.filter(pk=cand.pk).update(statut="convoquee")
    verification(cand, "eligible", at(2026, 3, 3, 9))
    assert "data-ecart" not in ok(client, direction_user, cand).content.decode()


def test_consultation_journalisee_sans_nom(client, direction_user, cand):
    ok(client, direction_user, cand)
    entry = view_entries().latest("id")
    assert entry.object_repr == f"Candidature #{cand.pk}"
    assert cand.candidat.nom.upper() not in entry.object_repr.upper()


def test_titre_de_page_sans_nom(client, direction_user, cand):
    html = ok(client, direction_user, cand).content.decode()
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    assert cand.candidat.nom.upper() not in title.upper()
    assert str(cand.pk) in title


def test_donnees_importees_echappees(client, direction_user, poste):
    xss = "<script>alert(1)</script>"
    c = new_candidature(
        poste=poste,
        donnees_declarees={
            "formation": [{"intitule": xss, "specialite": xss}],
            "experiences": [{"employeur": xss, "poste": xss, "debut": xss}],
        },
    )
    verification(
        c,
        "a_verifier",
        at(2026, 3, 1, 9),
        details=[
            {
                "critere": xss,
                "libelle": xss,
                "verdict": "a_verifier",
                "attendu": xss,
                "constate": xss,
                "raison": xss,
            }
        ],
    )
    piece(c, Piece.Type.CV, f"cv{xss}.pdf")
    html = ok(client, direction_user, c).content.decode()
    assert xss not in html
    assert "&lt;script&gt;" in html
