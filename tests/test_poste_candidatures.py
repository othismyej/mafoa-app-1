"""Tests d'acceptation de KAN-10 — Liste des candidatures d'un poste : filtres, tri, recherche.

Les attentes sont fondées sur la spécification ``docs/specs/KAN-10-liste-candidatures-poste.md``.
Toutes les données sont fictives (domaines example.com / example.org). Aucun appel IA.
"""

import json
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.http import QueryDict
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.models import Anomalie, Verdict, VerificationEligibilite
from apps.recruitment.filters import ANOMALIE_CHOICES, VERDICT_CHOICES, filter_candidatures
from apps.recruitment.models import Candidat, Candidature, Concours, Organisation, PosteConcours
from apps.recruitment.normalization import normalize_name

from .conftest import make_user

pytestmark = pytest.mark.django_db

BASE_DIR = Path(settings.BASE_DIR)
TEMPLATES = (
    BASE_DIR / "templates" / "recruitment" / "poste_candidatures.html",
    BASE_DIR / "templates" / "recruitment" / "_candidatures_resultats.html",
)
CATALOG = BASE_DIR / "locale" / "ar" / "LC_MESSAGES" / "django.po"
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


def rows(root: Node) -> list[Node]:
    return root.find_all(lambda n: n.tag == "tr" and "data-candidature-id" in n.attrs)


def row_ids(root: Node) -> list[int]:
    return [int(r.attrs["data-candidature-id"]) for r in rows(root)]


def tds(row: Node) -> list[Node]:
    return [c for c in row.children if c.tag == "td"]


def col(row: Node, name: str) -> Node:
    cells = row.find_all(lambda n: n.attrs.get("data-col") == name)
    assert len(cells) == 1, name
    return cells[0]


def compteur(root: Node) -> str:
    nodes = root.find_all(lambda n: "data-compteur" in n.attrs)
    assert len(nodes) == 1
    return nodes[0].text()


def pagination(root: Node) -> Node | None:
    nodes = root.find_all(lambda n: "data-pagination" in n.attrs)
    assert len(nodes) <= 1
    return nodes[0] if nodes else None


def inputs(root: Node, name: str) -> list[Node]:
    return root.find_all(lambda n: n.tag == "input" and n.attrs.get("name") == name)


def checked_values(root: Node, name: str) -> set[str]:
    return {n.attrs.get("value") for n in inputs(root, name) if "checked" in n.attrs}


def links_with_text(root: Node, text: str) -> list[Node]:
    return root.find_all(lambda n: n.tag == "a" and n.text() == text)


def main_html(html: str) -> str:
    match = re.search(r"<main\b.*?</main>", html, re.S)
    assert match
    return match.group(0)


# --------------------------------------------------------------------------------------
# Jeu de données
# --------------------------------------------------------------------------------------

_seq = {"n": 0}


def new_candidat(nom=None, prenom=None, **kwargs) -> Candidat:
    _seq["n"] += 1
    n = _seq["n"]
    return Candidat.objects.create(
        nom=nom if nom is not None else f"Fictif{n}",
        prenom=prenom if prenom is not None else f"Prenom{n}",
        **kwargs,
    )


def at(y, m, d, hh=10, mm=0) -> datetime:
    return timezone.make_aware(datetime(y, m, d, hh, mm))


def new_candidature(poste, statut=Candidature.Statut.RECUE, candidat=None, when=None):
    return Candidature.objects.create(
        candidat=candidat or new_candidat(),
        poste_concours=poste,
        date_candidature=when or at(2026, 3, 1),
        statut=statut,
    )


def add_verif(candidature, resultat, when=None) -> VerificationEligibilite:
    verif = VerificationEligibilite.objects.create(
        candidature=candidature, resultat=resultat, moteur_version="test"
    )
    if when is not None:
        VerificationEligibilite.objects.filter(pk=verif.pk).update(created_at=when)
    return verif


def add_anomalie(candidature, gravite, resolue=False) -> Anomalie:
    return Anomalie.objects.create(
        candidature=candidature,
        type=Anomalie.Type.AUTRE,
        gravite=gravite,
        description="Anomalie fictive",
        resolue=resolue,
    )


def make_concours(intitule="Concours fictif", **kwargs) -> Concours:
    org = Organisation.objects.create(
        nom=f"Organisation fictive {intitule}", type=Organisation.Type.PUBLIC
    )
    return Concours.objects.create(organisation=org, intitule=intitule, **kwargs)


S = Candidature.Statut
G = Anomalie.Gravite

# Identités fictives, distinctes et détectables (CA7).
IDENTITES = {
    "sara": ("El Amrani", "Sara", "F", date(1991, 7, 13)),
    "youssef": ("Bennani", "Youssef", "M", date(1988, 1, 22)),
    "imane": ("Éttahiri", "Imane", "F", date(1993, 11, 5)),
    "karim": ("Alaoui", "Karim", "M", date(1985, 4, 30)),
    "nora": ("Ziani", "Nora", "F", date(1995, 9, 17)),
    "omar": ("El Amrani", "Omar", "M", date(1990, 12, 2)),
}
ORDRE_DEFAUT = ["sara", "youssef", "imane", "karim", "nora", "omar"]


@pytest.fixture
def ref():
    """Jeu de données de référence de la spec (CA1 à CA35)."""
    concours = make_concours(
        "Concours fictif SRM Atlas 2026",
        date_publication=date(2026, 1, 10),
        date_limite=date(2026, 3, 31),
        statut=Concours.Statut.EN_EVALUATION,
    )
    p1 = PosteConcours.objects.create(
        concours=concours,
        reference="P-01",
        intitule="Ingénieur réseaux",
        nombre_postes=2,
        date_limite=date(2026, 2, 28),
    )
    p2 = PosteConcours.objects.create(
        concours=concours, reference="P-02", intitule="Technicien maintenance", nombre_postes=3
    )

    cand = {}
    for i, key in enumerate(ORDRE_DEFAUT):
        nom, prenom, sexe, naissance = IDENTITES[key]
        cand[key] = new_candidat(
            nom=nom,
            prenom=prenom,
            sexe=sexe,
            date_naissance=naissance,
            email=f"{prenom.lower()}.fictif{i}@example.org",
            telephone=f"+212600000{i:03d}",
            cin=f"QX77{i:04d}",
            ville=f"Villefictive{key.capitalize()}",
            region=f"Regionfictive{key.capitalize()}",
        )

    c = {}
    # Créées dans le désordre pour vérifier que l'ordre vient bien du tri.
    c["omar"] = new_candidature(p1, S.A_VERIFIER, cand["omar"], at(2026, 2, 6, 14))
    c["karim"] = new_candidature(p1, S.CONVOQUEE, cand["karim"], at(2026, 2, 4, 12))
    c["sara"] = new_candidature(p1, S.A_VERIFIER, cand["sara"], at(2026, 2, 1, 9))
    c["youssef"] = new_candidature(p1, S.ELIGIBLE, cand["youssef"], at(2026, 2, 2, 10))
    c["imane"] = new_candidature(p1, S.NON_ELIGIBLE, cand["imane"], at(2026, 2, 3, 11))
    c["nora"] = new_candidature(p1, S.RECUE, cand["nora"], at(2026, 2, 5, 13))

    add_verif(c["sara"], Verdict.A_VERIFIER, at(2026, 2, 10))
    add_verif(c["youssef"], Verdict.ELIGIBLE, at(2026, 2, 10))
    add_verif(c["imane"], Verdict.NON_ELIGIBLE, at(2026, 2, 10))
    add_verif(c["karim"], Verdict.ELIGIBLE, at(2026, 2, 10))
    add_verif(c["omar"], Verdict.A_VERIFIER, at(2026, 2, 10))

    add_anomalie(c["sara"], G.HAUTE)
    add_anomalie(c["youssef"], G.HAUTE, resolue=True)
    add_anomalie(c["imane"], G.MOYENNE)
    add_anomalie(c["omar"], G.INFO)
    add_anomalie(c["omar"], G.MOYENNE)

    dupont = new_candidature(
        p2,
        S.RECUE,
        new_candidat(nom="Dupont", prenom="Paul", email="paul.dupont@example.org"),
        at(2026, 2, 1, 8),
    )
    return {
        "concours": concours,
        "p1": p1,
        "p2": p2,
        "c": c,
        "candidats": cand,
        "dupont": dupont,
        "ids": {k: v.pk for k, v in c.items()},
    }


def url(concours, poste, params=None) -> str:
    base = reverse("recruitment:poste_candidatures", args=[concours.pk, poste.pk])
    if params:
        base += "?" + (params if isinstance(params, str) else urlencode(params, doseq=True))
    return base


def get(client, user, ref_or_url, params=None, **extra):
    client.force_login(user)
    target = (
        ref_or_url
        if isinstance(ref_or_url, str)
        else url(ref_or_url["concours"], ref_or_url["p1"], params)
    )
    return client.get(target, **extra)


def keys_of(ref, root: Node) -> list[str]:
    by_id = {v: k for k, v in ref["ids"].items()}
    return [by_id.get(i, f"?{i}") for i in row_ids(root)]


def listed(client, user, ref, params=None, **extra) -> list[str]:
    response = get(client, user, ref, params, **extra)
    assert response.status_code == 200
    return keys_of(ref, parse(response))


def view_entries():
    return JournalAudit.objects.filter(action=AuditAction.VIEW)


def bulk_poste(concours, n, *, same_date=False, reference="V-01", with_eval=False):
    """Poste de ``n`` candidatures fictives (création en masse)."""
    poste = PosteConcours.objects.create(
        concours=concours, reference=reference, intitule=f"Poste volume {reference}"
    )
    candidats = Candidat.objects.bulk_create(
        [
            Candidat(
                nom=f"Volume{reference}{i:05d}",
                prenom="Fictif",
                nom_normalise=normalize_name(f"Volume{reference}{i:05d}", "Fictif"),
            )
            for i in range(n)
        ]
    )
    base = at(2026, 2, 1)
    candidatures = Candidature.objects.bulk_create(
        [
            Candidature(
                candidat=candidats[i],
                poste_concours=poste,
                concours=concours,
                date_candidature=base if same_date else base + timedelta(minutes=i),
                statut=S.RECUE,
            )
            for i in range(n)
        ]
    )
    if with_eval:
        verdicts = [Verdict.ELIGIBLE, Verdict.NON_ELIGIBLE, Verdict.A_VERIFIER, None]
        gravites = [G.INFO, G.MOYENNE, G.HAUTE, None, None]
        verifs, anomalies = [], []
        for i, cand in enumerate(candidatures):
            v = verdicts[i % 4]
            if v is not None:
                verifs.append(
                    VerificationEligibilite(
                        candidature=cand, resultat=Verdict.A_VERIFIER, moteur_version="t"
                    )
                )
                verifs.append(
                    VerificationEligibilite(candidature=cand, resultat=v, moteur_version="t")
                )
            g = gravites[i % 5]
            if g is not None:
                anomalies.append(
                    Anomalie(candidature=cand, type="autre", gravite=g, description="fictive")
                )
                anomalies.append(
                    Anomalie(
                        candidature=cand,
                        type="autre",
                        gravite=G.HAUTE,
                        description="fictive",
                        resolue=True,
                    )
                )
        VerificationEligibilite.objects.bulk_create(verifs)
        Anomalie.objects.bulk_create(anomalies)
    return poste, candidatures


# --------------------------------------------------------------------------------------
# Affichage et accès (CA1 à CA9)
# --------------------------------------------------------------------------------------


def test_liste_par_defaut_compteur_et_ordre_chronologique(client, direction_user, ref):
    """CA1 — direction avec TOTP, sans paramètre : 200, « 6 candidatures affichées sur 6 »,
    ordre Sara, Youssef, Imane, Karim, Nora, Omar (date croissante)."""
    response = get(client, direction_user, ref)
    assert response.status_code == 200
    root = parse(response)
    assert compteur(root) == "6 candidatures affichées sur 6"
    assert keys_of(ref, root) == ORDRE_DEFAUT


def test_colonnes_de_chaque_ligne(client, direction_user, ref):
    """CA2 — Chaque ligne : « NOM Prénom », date jj/mm/aaaa hh:mm, libellé du statut, verdict,
    anomalies ; Nora « Non évaluée » ; Youssef sans anomalie (résolue ignorée) ; Omar 2
    anomalies, gravité « moyenne »."""
    root = parse(get(client, direction_user, ref))
    by_key = dict(zip(keys_of(ref, root), rows(root), strict=True))

    attendu = {
        "sara": ("EL AMRANI Sara", "01/02/2026 09:00", "À vérifier", "À vérifier"),
        "youssef": ("BENNANI Youssef", "02/02/2026 10:00", "Éligible", "Éligible"),
        "imane": ("ÉTTAHIRI Imane", "03/02/2026 11:00", "Non éligible", "Non éligible"),
        "karim": ("ALAOUI Karim", "04/02/2026 12:00", "Convoquée", "Éligible"),
        "nora": ("ZIANI Nora", "05/02/2026 13:00", "Reçue", "Non évaluée"),
        "omar": ("EL AMRANI Omar", "06/02/2026 14:00", "À vérifier", "À vérifier"),
    }
    for key, (nom, quand, statut, verdict) in attendu.items():
        texts = [td.text() for td in tds(by_key[key])]
        assert nom in texts, (key, texts)
        assert quand in texts, (key, texts)
        assert statut in texts, (key, texts)
        assert col(by_key[key], "verdict").text() == verdict, key

    def badges(key):
        return col(by_key[key], "anomalies").find_all(lambda n: "data-gravite" in n.attrs)

    assert col(by_key["youssef"], "anomalies").text() == ""
    assert not badges("youssef")
    assert col(by_key["karim"], "anomalies").text() == ""
    assert col(by_key["nora"], "anomalies").text() == ""

    omar = col(by_key["omar"], "anomalies")
    assert re.match(r"^2\b", omar.text())
    assert [b.attrs["data-gravite"] for b in badges("omar")] == ["moyenne"]
    assert "moyenne" in omar.text().lower()

    assert re.match(r"^1\b", col(by_key["sara"], "anomalies").text())
    assert [b.attrs["data-gravite"] for b in badges("sara")] == ["haute"]
    assert [b.attrs["data-gravite"] for b in badges("imane")] == ["moyenne"]


def test_statut_et_verdict_sont_distincts(client, direction_user, ref):
    """CA3 — ALAOUI Karim : statut « Convoquée », verdict « Éligible »."""
    root = parse(get(client, direction_user, ref))
    row = rows(root)[keys_of(ref, root).index("karim")]
    assert "Convoquée" in [td.text() for td in tds(row)]
    assert col(row, "verdict").text() == "Éligible"


def test_seule_la_derniere_verification_compte(client, direction_user, ref):
    """CA4 — Vérification « non éligible » puis plus récente « à vérifier » : « À vérifier »."""
    cand = new_candidature(ref["p1"], S.EN_ANALYSE, when=at(2026, 2, 7))
    add_verif(cand, Verdict.NON_ELIGIBLE, at(2026, 2, 8))
    add_verif(cand, Verdict.A_VERIFIER, at(2026, 2, 9))
    root = parse(get(client, direction_user, ref))
    row = [r for r in rows(root) if r.attrs["data-candidature-id"] == str(cand.pk)][0]
    assert col(row, "verdict").text() == "À vérifier"
    # Le filtre suit la même définition du dernier verdict.
    ids = row_ids(parse(get(client, direction_user, ref, {"verdict": "non_eligible"})))
    assert cand.pk not in ids


def test_derniere_verification_par_date_et_non_par_ordre_de_creation(client, direction_user, ref):
    """CA4 / règle 4 — La dernière vérification est celle de ``created_at`` le plus récent, même
    créée en premier ; à date égale, l'identifiant le plus élevé l'emporte."""
    a = new_candidature(ref["p1"], when=at(2026, 2, 7))
    add_verif(a, Verdict.ELIGIBLE, at(2026, 2, 20))
    add_verif(a, Verdict.NON_ELIGIBLE, at(2026, 2, 8))
    b = new_candidature(ref["p1"], when=at(2026, 2, 8))
    add_verif(b, Verdict.ELIGIBLE, at(2026, 2, 9))
    add_verif(b, Verdict.A_VERIFIER, at(2026, 2, 9))
    root = parse(get(client, direction_user, ref))
    by_id = {int(r.attrs["data-candidature-id"]): r for r in rows(root)}
    assert col(by_id[a.pk], "verdict").text() == "Éligible"
    assert col(by_id[b.pk], "verdict").text() == "À vérifier"


def test_candidature_d_un_autre_poste_absente(client, direction_user, ref):
    """CA5 — DUPONT Paul (P2) ne figure pas dans la liste de P1."""
    response = get(client, direction_user, ref)
    root = parse(response)
    assert ref["dupont"].pk not in row_ids(root)
    assert "DUPONT" not in response.content.decode()


def test_poste_d_un_autre_concours_ou_inexistant_404(client, direction_user, ref):
    """CA6 — P2 avec l'identifiant d'un autre concours : 404 ; poste inexistant : 404."""
    autre = make_concours("Autre concours fictif")
    assert get(client, direction_user, url(autre, ref["p2"])).status_code == 404
    missing = reverse(
        "recruitment:poste_candidatures", args=[ref["concours"].pk, ref["p2"].pk + 9999]
    )
    assert get(client, direction_user, missing).status_code == 404
    missing_concours = reverse(
        "recruitment:poste_candidatures", args=[ref["concours"].pk + 9999, ref["p1"].pk]
    )
    assert get(client, direction_user, missing_concours).status_code == 404


def test_aucune_autre_donnee_personnelle(client, direction_user, ref):
    """CA7 / règle 6 — Ni email, téléphone, CIN, date de naissance, ville, région, sexe."""
    html = get(client, direction_user, ref).content.decode()
    low = html.lower()
    for cand in ref["candidats"].values():
        cand.refresh_from_db()
        for value in (
            cand.email,
            cand.telephone,
            cand.cin,
            cand.ville,
            cand.region,
            cand.date_naissance.strftime("%d/%m/%Y"),
            cand.date_naissance.isoformat(),
        ):
            assert value
            assert value.lower() not in low, value
    for label in ("Femme", "Homme", "Sexe", "Région", "Date de naissance", "Âge", "@example.org"):
        assert label.lower() not in main_html(html).lower(), label


def test_admin_accede_a_la_liste(client, admin_user, ref):
    """CA8 — admin avec TOTP : 200 (page complète et fragment HTMX)."""
    assert get(client, admin_user, ref).status_code == 200
    assert get(client, admin_user, ref, **HX).status_code == 200


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
@pytest.mark.parametrize("htmx", [False, True], ids=["page", "htmx"])
def test_roles_vague_2_refuses_403(client, ref, role, htmx):
    """CA8 / CA34 — consultant ou client : 403 (page et HTMX), aucune donnée, pas d'audit."""
    user = make_user(f"{role}@example.com", role)
    response = get(client, user, ref, **(HX if htmx else {}))
    assert response.status_code == 403
    body = response.content.decode()
    for value in ("AMRANI", "BENNANI", "Ingénieur réseaux", "P-01"):
        assert value not in body
    assert not view_entries().exists()


def test_non_connecte_redirige_vers_la_connexion_avec_next(client, ref):
    """CA9 / CA34 — Non connecté : 302 vers la connexion avec ``next`` ; pas d'audit."""
    target = url(ref["concours"], ref["p1"], {"statut": "a_verifier"})
    for extra in ({}, HX):
        response = client.get(target, **extra)
        assert response.status_code == 302
        parsed = urlparse(response.url)
        assert parsed.path == reverse("account_login")
        assert parse_qs(parsed.query)["next"] == [target]
    assert not view_entries().exists()


def test_sans_totp_redirige_vers_l_activation(client, user_without_mfa, ref):
    """CA9 / CA34 — direction sans TOTP actif : redirection vers l'activation ; pas d'audit."""
    response = get(client, user_without_mfa, ref)
    assert response.status_code == 302
    assert urlparse(response.url).path == reverse("mfa_activate_totp")
    assert not view_entries().exists()


def test_utilisateur_inactif_refuse(client, ref):
    """Rôles — Utilisateur inactif : session refusée, aucune donnée ni audit."""
    user = make_user("inactif@example.com", Role.DIRECTION)
    client.force_login(user)
    user.is_active = False
    user.save()
    response = client.get(url(ref["concours"], ref["p1"]))
    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert urlparse(response.url).path == reverse("account_login")
    assert "AMRANI" not in response.content.decode()
    assert not view_entries().exists()


def test_post_refuse_405(client, direction_user, ref):
    """Cas limites — Méthodes HTTP : seul GET est accepté ; POST renvoie 405, sans audit."""
    client.force_login(direction_user)
    response = client.post(url(ref["concours"], ref["p1"]), {"statut": "rejetee"})
    assert response.status_code == 405
    assert not view_entries().exists()


def test_en_tete_fil_d_ariane_postes_et_date_limite(client, direction_user, ref):
    """Règle 3 — Fil d'Ariane « Tableau de bord › concours › P-01 — intitulé », nombre de
    postes, date limite du poste."""
    root = parse(get(client, direction_user, ref))
    text = parse(main_html(get(client, direction_user, ref).content.decode())).text()
    assert "Tableau de bord" in text
    assert "Concours fictif SRM Atlas 2026" in text
    assert "P-01 — Ingénieur réseaux" in text
    assert re.search(
        r"Tableau de bord\s*›\s*Concours fictif SRM Atlas 2026\s*›\s*P-01 — Ingénieur réseaux",
        text,
    )
    assert "28/02/2026" in text
    hrefs = [a.attrs.get("href") for a in root.find_all(lambda n: n.tag == "a")]
    assert reverse("dashboard") in hrefs
    assert reverse("recruitment:concours_detail", args=[ref["concours"].pk]) in hrefs
    assert re.search(r"Postes à pourvoir\s*:?\s*2\b", text)


def test_date_limite_de_repli_sur_le_concours(client, direction_user, ref):
    """Règle 3 — Poste sans date limite : celle du concours (règle de repli de KAN-9)."""
    new_candidature(ref["p2"])
    html = get(client, direction_user, url(ref["concours"], ref["p2"])).content.decode()
    assert "31/03/2026" in parse(main_html(html)).text()


def test_nom_sans_lien_tant_que_la_fiche_n_existe_pas(client, direction_user, ref):
    """Règle 5 — Le nom du candidat est affiché sans lien (fiche KAN-11 non livrée)."""
    root = parse(get(client, direction_user, ref))
    for row in rows(root):
        name_cell = tds(row)[1]
        assert name_cell.text()
        assert not name_cell.find_all(lambda n: n.tag == "a")


def test_liste_en_lecture_seule(client, direction_user, ref):
    """Règle 29 — Aucune action de modification proposée : pas de formulaire POST ni de bouton
    de décision, de changement de statut, de relance ou d'export dans la page."""
    html = main_html(get(client, direction_user, ref).content.decode())
    assert not re.search(r"<form[^>]*method=[\"']?post", html, re.I)
    assert "hx-post" not in html.lower()
    for word in ("Décider", "Exporter", "Relancer", "Changer le statut", "Supprimer"):
        assert word not in html


# --------------------------------------------------------------------------------------
# Filtres (CA10 à CA17)
# --------------------------------------------------------------------------------------


def test_filtre_statut_a_verifier(client, direction_user, ref):
    """CA10 — statut = à vérifier : Sara et Omar, compteur « 2 … sur 6 »."""
    root = parse(get(client, direction_user, ref, {"statut": "a_verifier"}))
    assert keys_of(ref, root) == ["sara", "omar"]
    assert compteur(root) == "2 candidatures affichées sur 6"


def test_filtre_statut_multiple_en_ou(client, direction_user, ref):
    """CA11 — statut = éligible OU convoquée : Youssef et Karim."""
    assert listed(client, direction_user, ref, {"statut": ["eligible", "convoquee"]}) == [
        "youssef",
        "karim",
    ]


@pytest.mark.parametrize("statut", [v for v, _ in S.choices])
def test_chaque_statut_est_filtrable(client, direction_user, ref, statut):
    """Règle 7 — Les 9 statuts sont proposés et filtrables."""
    root = parse(get(client, direction_user, ref, {"statut": statut}))
    assert statut in {n.attrs.get("value") for n in inputs(root, "statut")}
    expected = [k for k in ORDRE_DEFAUT if ref["c"][k].statut == statut]
    assert keys_of(ref, root) == expected


def test_filtre_verdict(client, direction_user, ref):
    """CA12 — verdict = éligible : Youssef et Karim ; verdict = non évaluée : Nora seule."""
    assert listed(client, direction_user, ref, {"verdict": "eligible"}) == ["youssef", "karim"]
    assert listed(client, direction_user, ref, {"verdict": "non_evaluee"}) == ["nora"]
    assert listed(client, direction_user, ref, {"verdict": ["non_eligible", "non_evaluee"]}) == [
        "imane",
        "nora",
    ]


def test_filtre_anomalies(client, direction_user, ref):
    """CA13 — avec anomalie non résolue : Sara, Imane, Omar ; sans : Youssef, Karim, Nora ;
    toutes : les 6."""
    assert listed(client, direction_user, ref, {"anomalies": "avec"}) == ["sara", "imane", "omar"]
    assert listed(client, direction_user, ref, {"anomalies": "sans"}) == [
        "youssef",
        "karim",
        "nora",
    ]
    assert listed(client, direction_user, ref, {"anomalies": "toutes"}) == ORDRE_DEFAUT


def test_filtres_et_recherche_combines_en_et(client, direction_user, ref):
    """CA14 — verdict à vérifier ET avec anomalie ET « amrani » : Sara et Omar ; + statut reçue :
    vide."""
    params = {"verdict": "a_verifier", "anomalies": "avec", "q": "amrani"}
    assert listed(client, direction_user, ref, params) == ["sara", "omar"]
    root = parse(get(client, direction_user, ref, {**params, "statut": "recue"}))
    assert row_ids(root) == []
    assert compteur(root) == "0 candidature affichée sur 6" or compteur(root).startswith("0 ")


def test_parametres_invalides_ignores(client, direction_user, ref):
    """CA15 — statut=inconnu, verdict=xyz, tri=sexe, page=abc, taille=1000 : 200, aucun filtre,
    tri par défaut, page 1."""
    params = {"statut": "inconnu", "verdict": "xyz", "tri": "sexe", "page": "abc", "taille": "1000"}
    response = get(client, direction_user, ref, params)
    assert response.status_code == 200
    root = parse(response)
    assert keys_of(ref, root) == ORDRE_DEFAUT
    assert compteur(root) == "6 candidatures affichées sur 6"
    assert checked_values(root, "statut") == set()
    assert checked_values(root, "verdict") == set()


def test_taille_de_page_1000_plafonnee_a_100(client, direction_user):
    """CA15 / règle 21 — taille=1000 (et page=abc) sur 120 candidatures : au plus 100 lignes,
    page 1."""
    concours = make_concours()
    poste, cands = bulk_poste(concours, 120)
    client.force_login(direction_user)
    root = parse(client.get(url(concours, poste, {"taille": "1000", "page": "abc"})))
    ids = row_ids(root)
    assert 0 < len(ids) <= 100
    assert ids[0] == cands[0].pk


@pytest.mark.parametrize(
    "params",
    [
        {"taille": "0"},
        {"taille": "-50"},
        {"taille": "abc"},
        {"taille": "30"},
        {"page": "-1"},
        {"page": "0"},
        {"page": "1.5"},
        {"tri": "--date"},
        {"tri": "region"},
        {"tri": "-sexe"},
        {"tri": "age"},
        {"anomalies": "peut-etre"},
        {"statut": ""},
        {"verdict": ""},
    ],
)
def test_autres_valeurs_invalides_sans_erreur(client, direction_user, ref, params):
    """Règle 13 / règle 20 — Valeurs inconnues ignorées sans 500 ni 400 (défauts : 6 lignes, date
    croissante) ; aucun tri sur sexe, âge, région."""
    response = get(client, direction_user, ref, params)
    assert response.status_code == 200
    assert keys_of(ref, parse(response)) == ORDRE_DEFAUT


def test_valeur_inconnue_ignoree_parmi_des_valeurs_valides(client, direction_user, ref):
    """Règle 13 — statut=a_verifier&statut=inconnu : seul le statut valide est appliqué."""
    params = {"statut": ["a_verifier", "inconnu"], "verdict": ["xyz", "eligible"]}
    assert listed(client, direction_user, ref, params) == []
    assert listed(client, direction_user, ref, {"statut": ["a_verifier", "inconnu"]}) == [
        "sara",
        "omar",
    ]


def test_aucun_filtre_ni_tri_sur_les_donnees_sensibles(client, direction_user, ref):
    """Règle 20 — Aucun contrôle de tri ou de filtre sur sexe, âge, région, ville, photo ou
    situation familiale."""
    root = parse(get(client, direction_user, ref))
    names = {n.attrs.get("name") for n in root.find_all(lambda n: "name" in n.attrs)}
    for forbidden in ("sexe", "age", "region", "ville", "photo", "situation_familiale"):
        assert forbidden not in names
    tri_keys = {n.attrs["data-tri"] for n in root.find_all(lambda n: "data-tri" in n.attrs)}
    assert tri_keys == {"candidat", "date", "statut", "verdict", "anomalies"}


def test_lien_a_verifier_de_la_fiche_concours_ouvre_la_liste_prefiltree(
    client, direction_user, ref
):
    """CA16 (et KAN-9 CA18) — Le lien du compteur « à vérifier » de P1 ouvre la liste filtrée
    sur verdict « à vérifier », case cochée dans le formulaire."""
    client.force_login(direction_user)
    detail = parse(
        client.get(
            reverse("recruitment:concours_detail", args=[ref["concours"].pk])
        ).content.decode()
    )
    row = [r for r in detail.find_all(lambda n: n.attrs.get("data-poste") == str(ref["p1"].pk))]
    assert len(row) == 1
    cell = [n for n in row[0].find_all(lambda n: n.attrs.get("data-col") == "a_verifier")][0]
    links = cell.find_all(lambda n: n.tag == "a")
    assert len(links) == 1
    href = links[0].attrs["href"]
    assert urlparse(href).path == url(ref["concours"], ref["p1"])

    root = parse(client.get(href))
    assert keys_of(ref, root) == ["sara", "omar"]
    assert checked_values(root, "verdict") == {"a_verifier"}


def test_reinitialiser_supprime_filtres_recherche_et_tri(client, direction_user, ref):
    """CA17 / règle 12 — « Réinitialiser » : les 6 candidatures, ordre par défaut, page 1."""
    params = {
        "statut": "a_verifier",
        "q": "amrani",
        "tri": "-candidat",
        "page": "2",
        "taille": "25",
    }
    root = parse(get(client, direction_user, ref, params))
    links = links_with_text(root, "Réinitialiser")
    assert links
    href = links[0].attrs["href"]
    after = parse(client.get(href))
    assert keys_of(ref, after) == ORDRE_DEFAUT
    assert compteur(after) == "6 candidatures affichées sur 6"
    qs = parse_qs(urlparse(href).query)
    for key in ("statut", "verdict", "anomalies", "q", "tri"):
        assert key not in qs, key
    assert qs.get("page", ["1"]) == ["1"]
    assert checked_values(after, "statut") == set()
    assert all(n.attrs.get("value", "") == "" for n in inputs(after, "q"))


# --------------------------------------------------------------------------------------
# Recherche (CA18 à CA20)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("amrani", ["sara", "omar"]),
        ("sara el-amrani", ["sara"]),
        ("EL AMRANI SARA", ["sara"]),
        ("ÉL AMRANI", ["sara", "omar"]),
        ("Sara El Amrani", ["sara"]),
        ("amrani sara", ["sara"]),
        ("  amrani   omar  ", ["omar"]),
    ],
)
def test_recherche_insensible_a_l_ordre_casse_accents(client, direction_user, ref, q, expected):
    """CA18 / règle 14 — « amrani » : Sara et Omar ; « sara el-amrani », « EL AMRANI SARA » :
    Sara seule."""
    assert listed(client, direction_user, ref, {"q": q}) == expected


def test_recherche_sans_accent_trouve_un_nom_accentue(client, direction_user, ref):
    """CA19 — « ettahiri » trouve ÉTTAHIRI Imane."""
    assert listed(client, direction_user, ref, {"q": "ettahiri"}) == ["imane"]
    assert listed(client, direction_user, ref, {"q": "ÉTTAHIRI"}) == ["imane"]


def test_recherche_d_un_caractere_et_nom_absent(client, direction_user, ref):
    """CA20 / règle 15 — « a » : les 6 ; nom absent : message dédié et « Réinitialiser »."""
    assert listed(client, direction_user, ref, {"q": "a"}) == ORDRE_DEFAUT
    response = get(client, direction_user, ref, {"q": "Inexistantissime"})
    root = parse(response)
    assert row_ids(root) == []
    assert "Aucune candidature ne correspond à ces critères." in root.text()
    assert links_with_text(root, "Réinitialiser")


def test_recherche_a_partir_de_deux_caracteres(client, direction_user, ref):
    """Règle 15 — 2 caractères utiles suffisent à filtrer."""
    assert listed(client, direction_user, ref, {"q": "zi"}) == ["nora"]


def test_recherche_porte_uniquement_sur_nom_et_prenom(client, direction_user, ref):
    """Règle 16 — Pas de recherche sur l'email, le téléphone ni le CIN."""
    sara = ref["candidats"]["sara"]
    for q in (sara.email, sara.telephone, sara.cin, "example.org"):
        assert listed(client, direction_user, ref, {"q": q}) == [], q


def test_recherche_limitee_a_100_caracteres(client, direction_user, ref):
    """Règle 15 — Champ limité à 100 caractères ; une saisie plus longue ne provoque pas
    d'erreur."""
    root = parse(get(client, direction_user, ref))
    fields = inputs(root, "q")
    assert len(fields) == 1
    assert fields[0].attrs.get("maxlength") == "100"
    response = get(client, direction_user, ref, {"q": "amrani " + "x" * 300})
    assert response.status_code == 200
    entry = view_entries().order_by("-pk").first()
    assert len(entry.metadata.get("recherche") or "") <= 100


def test_recherche_sans_mot_apres_normalisation_n_applique_aucun_filtre(
    client, direction_user, ref
):
    """Cas limites — Saisie sans aucun mot après normalisation : aucun filtre."""
    assert listed(client, direction_user, ref, {"q": "-- '' ..."}) == ORDRE_DEFAUT


def test_recherche_avec_apostrophe_et_tiret(client, direction_user, ref):
    """Cas limites — Apostrophes et tirets normalisés comme ``nom_normalise``."""
    c = new_candidature(
        ref["p1"], candidat=new_candidat(nom="Aït-Lhaj", prenom="M'barek"), when=at(2026, 2, 7)
    )
    ref["ids"]["mbarek"] = c.pk
    assert "mbarek" in listed(client, direction_user, ref, {"q": c.candidat.nom_normalise})
    assert listed(client, direction_user, ref, {"q": "ait lhaj"}) == ["mbarek"]


def test_recherche_malveillante_echappee(client, direction_user, ref):
    """Cas limites — Pas d'XSS via la recherche, pas d'injection SQL."""
    payload = '<script>alert("x")</script>'
    response = get(client, direction_user, ref, {"q": payload})
    assert response.status_code == 200
    html = response.content.decode()
    assert payload not in html
    assert "&lt;script&gt;" in html
    response = get(client, direction_user, ref, {"q": "'; DROP TABLE recruitment_candidat; --"})
    assert response.status_code == 200
    assert Candidat.objects.count() == 7
    response = get(client, direction_user, ref, {"q": "zz' OR '1'='1"})
    assert response.status_code == 200
    assert row_ids(parse(response)) == []


def test_homonymes_sur_deux_lignes(client, direction_user, ref):
    """Cas limites — Deux candidats homonymes apparaissent sur deux lignes distinctes."""
    c = new_candidature(
        ref["p1"], candidat=new_candidat(nom="Bennani", prenom="Youssef"), when=at(2026, 2, 7)
    )
    ref["ids"]["youssef2"] = c.pk
    assert listed(client, direction_user, ref, {"q": "bennani youssef"}) == ["youssef", "youssef2"]


def test_candidat_sans_prenom_et_nom_tres_long(client, direction_user, ref):
    """Cas limites — Sans prénom ou nom très long : affichage sans erreur, nom complet en
    infobulle."""
    long_nom = "Abcdefghij" * 15
    a = new_candidature(ref["p1"], candidat=new_candidat(nom="Sansprenom", prenom=""))
    b = new_candidature(ref["p1"], candidat=new_candidat(nom=long_nom, prenom="Lina"))
    response = get(client, direction_user, ref)
    assert response.status_code == 200
    root = parse(response)
    by_id = {int(r.attrs["data-candidature-id"]): r for r in rows(root)}
    assert "SANSPRENOM" in tds(by_id[a.pk])[1].text()
    full = f"{long_nom.upper()} Lina"
    titled = by_id[b.pk].find_all(lambda n: n.attrs.get("title") == full)
    assert titled, "nom complet attendu en infobulle"


# --------------------------------------------------------------------------------------
# Tri (CA21 à CA24)
# --------------------------------------------------------------------------------------


def test_tri_par_candidat(client, direction_user, ref):
    """CA21 — Tri candidat croissant : ordre de ``nom_normalise`` ; décroissant : inverse exact."""
    expected = [
        k
        for k, _ in sorted(
            ref["c"].items(),
            key=lambda kv: (Candidat.objects.get(pk=kv[1].candidat_id).nom_normalise, kv[1].pk),
        )
    ]
    assert listed(client, direction_user, ref, {"tri": "candidat"}) == expected
    assert listed(client, direction_user, ref, {"tri": "-candidat"}) == expected[::-1]


def test_tri_par_anomalies_decroissant(client, direction_user, ref):
    """CA22 — Anomalies décroissant : Omar (2), puis Sara et Imane (1), puis les autres."""
    order = listed(client, direction_user, ref, {"tri": "-anomalies"})
    assert order[0] == "omar"
    assert set(order[1:3]) == {"sara", "imane"}
    assert set(order[3:]) == {"youssef", "karim", "nora"}
    # Départage stable : même ordre à données inchangées.
    assert listed(client, direction_user, ref, {"tri": "-anomalies"}) == order
    asc = listed(client, direction_user, ref, {"tri": "anomalies"})
    assert set(asc[:3]) == {"youssef", "karim", "nora"}
    assert asc[-1] == "omar"


def test_tri_par_anomalies_departage_par_identifiant(client, direction_user, ref):
    """CA22 / règle 19 — À nombre égal, départage par identifiant de candidature."""
    order = listed(client, direction_user, ref, {"tri": "-anomalies"})
    pair = order[1:3]
    ids = [ref["ids"][k] for k in pair]
    assert ids == sorted(ids) or ids == sorted(ids, reverse=True)


def test_tri_par_verdict(client, direction_user, ref):
    """CA23 — Verdict croissant : à vérifier, non éligible, éligible, non évaluée."""
    order = listed(client, direction_user, ref, {"tri": "verdict"})
    assert set(order[0:2]) == {"sara", "omar"}
    assert order[2] == "imane"
    assert set(order[3:5]) == {"youssef", "karim"}
    assert order[5] == "nora"
    desc = listed(client, direction_user, ref, {"tri": "-verdict"})
    assert desc[0] == "nora"
    assert set(desc[-2:]) == {"sara", "omar"}


def test_tri_par_date_decroissante(client, direction_user, ref):
    """CA24 — Date décroissante : Omar en premier, Sara en dernier."""
    order = listed(client, direction_user, ref, {"tri": "-date"})
    assert order == ORDRE_DEFAUT[::-1]
    assert listed(client, direction_user, ref, {"tri": "date"}) == ORDRE_DEFAUT


def test_tri_par_statut_ordre_du_parcours(client, direction_user, ref):
    """Règle 17 — Statut : ordre de déclaration du parcours (reçue → rejetée)."""
    order = listed(client, direction_user, ref, {"tri": "statut"})
    assert order[0] == "nora"  # reçue
    assert order[1] == "youssef"  # éligible
    assert order[2] == "imane"  # non éligible
    assert set(order[3:5]) == {"sara", "omar"}  # à vérifier
    assert order[5] == "karim"  # convoquée
    desc = listed(client, direction_user, ref, {"tri": "-statut"})
    assert desc[0] == "karim"
    assert desc[-1] == "nora"


def test_liens_de_tri_presents_pour_chaque_colonne(client, direction_user, ref):
    """Règle 17 — Chaque colonne triable porte un lien de tri qui conserve les filtres."""
    root = parse(get(client, direction_user, ref, {"statut": "a_verifier"}))
    links = {n.attrs["data-tri"]: n for n in root.find_all(lambda n: "data-tri" in n.attrs)}
    assert set(links) == {"candidat", "date", "statut", "verdict", "anomalies"}
    href = links["candidat"].attrs["href"]
    qs = parse_qs(urlparse(href).query)
    assert qs.get("statut") == ["a_verifier"]
    assert qs.get("tri") in (["candidat"], ["-candidat"])
    after = parse(client.get(url(ref["concours"], ref["p1"]) + "?" + urlparse(href).query))
    assert set(keys_of(ref, after)) == {"sara", "omar"}


# --------------------------------------------------------------------------------------
# Pagination et performance (CA25 à CA27)
# --------------------------------------------------------------------------------------


def test_pagination_120_candidatures(client, direction_user):
    """CA25 — 120 candidatures : 50 lignes, 3 pages, page 3 = 20 ; taille 100 page 2 = 20 ;
    page=99 → page 3."""
    concours = make_concours()
    poste, cands = bulk_poste(concours, 120)
    client.force_login(direction_user)

    def page(params=None):
        return parse(client.get(url(concours, poste, params)))

    first = page()
    assert len(rows(first)) == 50
    nav = pagination(first)
    assert nav is not None
    assert re.search(r"\b3\b", nav.text())
    assert compteur(first) == "120 candidatures affichées sur 120"

    third = page({"page": 3})
    assert len(rows(third)) == 20
    assert row_ids(third) == [c.pk for c in cands[100:]]

    assert len(rows(page({"taille": 100, "page": 2}))) == 20
    assert len(rows(page({"taille": 100}))) == 100
    assert len(rows(page({"taille": 25}))) == 25

    beyond = page({"page": 99})
    assert row_ids(beyond) == row_ids(third)


def test_liens_de_pagination_conservent_les_parametres(client, direction_user):
    """Règle 11 / CA25 — Les liens de pagination conservent filtres, tri et taille."""
    concours = make_concours()
    poste, _ = bulk_poste(concours, 120)
    client.force_login(direction_user)
    root = parse(
        client.get(url(concours, poste, {"statut": "recue", "tri": "-date", "taille": 25}))
    )
    hrefs = [a.attrs["href"] for a in pagination(root).find_all(lambda n: n.tag == "a")]
    assert hrefs
    qs = parse_qs(urlparse(hrefs[-1]).query)
    assert qs.get("statut") == ["recue"]
    assert qs.get("tri") == ["-date"]
    assert qs.get("taille") == ["25"]
    assert qs.get("page") == ["2"]


def test_departage_stable_a_date_identique(client, direction_user):
    """CA26 / règle 19 — 120 candidatures à date identique : chacune exactement une fois sur
    les 3 pages."""
    concours = make_concours()
    poste, cands = bulk_poste(concours, 120, same_date=True)
    client.force_login(direction_user)
    for tri in ("date", "-date", "statut", "verdict", "anomalies", "-anomalies"):
        seen = []
        for p in (1, 2, 3):
            seen += row_ids(parse(client.get(url(concours, poste, {"page": p, "tri": tri}))))
        assert len(seen) == 120, tri
        assert sorted(seen) == sorted(c.pk for c in cands), tri


MAX_QUERIES = 20


def test_nombre_de_requetes_constant(client, direction_user):
    """CA27 / règles 22-23 — 10 et 1 000 candidatures (avec vérifications et anomalies), pages
    de 50 et 100 : même nombre de requêtes SQL, borné par une constante."""
    concours = make_concours()
    petit, _ = bulk_poste(concours, 10, reference="S-01", with_eval=True)
    grand, _ = bulk_poste(concours, 1000, reference="L-01", with_eval=True)
    client.force_login(direction_user)
    assert client.get(url(concours, petit)).status_code == 200  # préchauffage

    counts = {}
    for name, poste in (("petit", petit), ("grand", grand)):
        for taille in (50, 100):
            params = {"taille": taille, "tri": "-anomalies", "verdict": ["eligible", "a_verifier"]}
            with CaptureQueriesContext(connection) as ctx:
                response = client.get(url(concours, poste, params))
            assert response.status_code == 200
            counts[(name, taille)] = len(ctx)
            if name == "grand":
                assert len(rows(parse(response))) == taille
    assert len(set(counts.values())) == 1, counts
    assert max(counts.values()) <= MAX_QUERIES, counts


def test_poste_sans_candidature(client, direction_user, ref):
    """Cas limites — Poste sans candidature : message dédié, compteur 0 sur 0, filtres
    absents ou désactivés."""
    vide = PosteConcours.objects.create(concours=ref["concours"], reference="P-09", intitule="Vide")
    response = get(client, direction_user, url(ref["concours"], vide))
    assert response.status_code == 200
    root = parse(response)
    assert "Aucune candidature pour ce poste." in root.text()
    assert re.search(r"\b0\b[^0-9]*sur 0\b", compteur(root))
    for field in inputs(root, "statut") + inputs(root, "verdict"):
        assert "disabled" in field.attrs


# --------------------------------------------------------------------------------------
# HTMX et URL (CA29, CA30, règles 24 à 26)
# --------------------------------------------------------------------------------------


def test_fragment_htmx_et_page_complete(client, direction_user):
    """CA29 / règle 26 — HX-Request : tableau, compteur et pagination sans gabarit complet ; la
    même URL sans en-tête renvoie la page complète avec les mêmes lignes."""
    concours = make_concours()
    poste, _ = bulk_poste(concours, 120)
    client.force_login(direction_user)
    target = url(concours, poste, {"statut": "recue", "tri": "-date"})

    fragment = client.get(target, **HX)
    assert fragment.status_code == 200
    html = fragment.content.decode()
    assert "<html" not in html.lower()
    assert "<header" not in html.lower()
    assert "<nav" not in html.lower() or "data-pagination" in html
    froot = parse(html)
    assert len(rows(froot)) == 50
    assert compteur(froot)
    assert pagination(froot) is not None
    assert not froot.find_all(
        lambda n: n.tag == "a" and n.attrs.get("href") == reverse("dashboard")
    )

    full = client.get(target)
    assert full.status_code == 200
    assert "<html" in full.content.decode().lower()
    assert row_ids(parse(full)) == row_ids(froot)


def test_url_rouverte_dans_une_nouvelle_session(direction_user, ref):
    """CA30 / règle 11 — Même URL dans une nouvelle session : mêmes lignes, même ordre,
    contrôles du formulaire dans l'état correspondant."""
    params = {
        "statut": ["a_verifier", "non_eligible"],
        "verdict": ["a_verifier", "non_eligible"],
        "anomalies": "avec",
        "q": "amrani",
        "tri": "-date",
        "taille": "25",
        "page": "1",
    }
    target = url(ref["concours"], ref["p1"], params)
    first = Client()
    first.force_login(direction_user)
    r1 = parse(first.get(target))
    first.logout()
    second = Client()
    second.force_login(direction_user)
    r2 = parse(second.get(target))

    assert keys_of(ref, r1) == keys_of(ref, r2) == ["omar", "sara"]
    assert checked_values(r2, "statut") == {"a_verifier", "non_eligible"}
    assert checked_values(r2, "verdict") == {"a_verifier", "non_eligible"}
    assert checked_values(r2, "anomalies") == {"avec"}
    assert [n.attrs.get("value") for n in inputs(r2, "q")] == ["amrani"]
    selected = r2.find_all(
        lambda n: (
            n.tag == "option" and "selected" in n.attrs and n.parent.attrs.get("name") == "taille"
        )
    )
    assert [n.attrs.get("value") for n in selected] == ["25"]
    tri_values = {n.attrs.get("value") for n in inputs(r2, "tri")}
    assert tri_values == {"-date"}


def test_formulaire_get_utilisable_sans_javascript_et_htmx(client, direction_user, ref):
    """Règles 24-25 — Formulaire GET classique (sans JS) enrichi par HTMX : URL mise à jour,
    zone de résultats ciblée, recherche après une pause de ~300 ms."""
    root = parse(get(client, direction_user, ref))
    forms = root.find_all(lambda n: n.tag == "form" and inputs(n, "statut"))
    assert len(forms) == 1
    form = forms[0]
    assert form.attrs.get("method", "get").lower() == "get"
    assert urlparse(form.attrs.get("action") or url(ref["concours"], ref["p1"])).path == url(
        ref["concours"], ref["p1"]
    )
    assert form.attrs.get("hx-get") is not None
    assert form.attrs.get("hx-push-url") == "true"
    assert "300ms" in form.attrs.get("hx-trigger", "")
    assert form.find_all(lambda n: n.tag == "button" and n.attrs.get("type") == "submit")


# --------------------------------------------------------------------------------------
# Préparation KAN-15 (CA31, CA32, règle 27)
# --------------------------------------------------------------------------------------


def test_chaque_ligne_porte_l_identifiant_et_une_colonne_reservee(client, direction_user, ref):
    """CA31 / règle 27 — ``data-candidature-id`` = identifiant ; première colonne vide."""
    root = parse(get(client, direction_user, ref))
    assert sorted(row_ids(root)) == sorted(ref["ids"].values())
    body_rows = root.find_all(lambda n: n.tag == "tr" and n.parent and n.parent.tag == "tbody")
    assert len(body_rows) == len(rows(root)) == 6
    for row in rows(root):
        assert tds(row)[0].text() == ""


@pytest.mark.parametrize(
    "query",
    [
        "",
        "statut=a_verifier",
        "verdict=a_verifier&anomalies=avec&q=amrani",
        "verdict=eligible&verdict=non_evaluee",
        "anomalies=sans&tri=-candidat",
        "q=el+amrani&statut=recue",
        "statut=inconnu&taille=1000&page=abc",
    ],
)
def test_logique_de_filtrage_reutilisable(client, direction_user, ref, query):
    """CA32 / règle 28 — ``filter_candidatures`` (hors vue) renvoie exactement l'ensemble listé
    par la vue, toutes pages confondues."""
    qs, _ = filter_candidatures(ref["p1"], QueryDict(query))
    direct = [c.pk for c in qs]
    client.force_login(direction_user)
    shown = []
    base = QueryDict(query, mutable=True)
    base["taille"] = "25"
    for page in range(1, 5):
        base["page"] = str(page)
        ids = row_ids(parse(client.get(url(ref["concours"], ref["p1"], base.urlencode()))))
        if not ids or (shown and ids[0] in shown):
            break
        shown += ids
    assert len(direct) == len(set(direct))
    assert set(direct) == set(shown)


def test_logique_de_filtrage_volume_toutes_pages(client, direction_user):
    """CA32 — Sur 120 candidatures paginées, l'ensemble direct égale l'union des pages."""
    concours = make_concours()
    poste, _ = bulk_poste(concours, 120, with_eval=True)
    params = QueryDict("verdict=a_verifier&verdict=non_evaluee&anomalies=avec")
    qs, _ = filter_candidatures(poste, params)
    direct = {c.pk for c in qs}
    client.force_login(direction_user)
    shown = []
    for page in range(1, 7):
        query = params.copy()
        query["taille"] = "25"
        query["page"] = str(page)
        ids = row_ids(parse(client.get(url(concours, poste, query.urlencode()))))
        if shown and ids and ids[0] in shown:
            break  # au-delà de la dernière page, la dernière page est réaffichée (règle 21)
        shown += ids
    assert len(direct) > 25  # plusieurs pages
    assert len(shown) == len(set(shown))
    assert set(shown) == direct


def test_logique_de_filtrage_limitee_au_poste(ref):
    """CA32 / CA5 — La logique ne renvoie que les candidatures du poste demandé."""
    qs, _ = filter_candidatures(ref["p1"], QueryDict(""))
    ids = {c.pk for c in qs}
    assert ids == set(ref["ids"].values())
    assert ref["dupont"].pk not in ids


# --------------------------------------------------------------------------------------
# Audit (CA33, CA34)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("htmx", [False, True], ids=["page", "htmx"])
def test_consultation_journalisee(client, direction_user, ref, htmx):
    """CA33 — Entrée ``view`` : acteur, IP, objet PosteConcours, métadonnées (filtres,
    recherche, tri, page, taille, nombre de résultats) ; pas d'avant/après."""
    params = {
        "statut": ["a_verifier"],
        "verdict": ["a_verifier"],
        "anomalies": "avec",
        "q": "amrani",
        "tri": "-date",
        "taille": "25",
    }
    response = get(
        client, direction_user, ref, params, REMOTE_ADDR="203.0.113.7", **(HX if htmx else {})
    )
    assert response.status_code == 200
    entries = list(view_entries())
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor == direction_user
    assert entry.ip_address == "203.0.113.7"
    assert entry.content_type == ContentType.objects.get_for_model(PosteConcours)
    assert entry.object_id == str(ref["p1"].pk)
    assert entry.before is None and entry.after is None
    meta = entry.metadata
    assert meta["statuts"] == ["a_verifier"]
    assert meta["verdicts"] == ["a_verifier"]
    assert meta["anomalies"] == "avec"
    assert meta["recherche"] == "amrani"
    assert meta["tri"] == "-date"
    assert meta["page"] == 1
    assert meta["taille"] == 25
    assert meta["nb_resultats"] == 2


def test_chaque_reponse_servie_cree_une_entree(client, admin_user, ref):
    """CA33 / Q5 — Une entrée par réponse servie (pages complètes et fragments)."""
    get(client, admin_user, ref)
    get(client, admin_user, ref, {"page": 2}, **HX)
    get(client, admin_user, ref, {"q": "amrani"}, **HX)
    entries = list(view_entries().order_by("pk"))
    assert len(entries) == 3
    assert all(e.actor == admin_user for e in entries)
    assert entries[2].metadata["nb_resultats"] == 2
    assert entries[0].metadata["nb_resultats"] == 6


def test_parametres_invalides_journalises_par_leur_valeur_effective(client, direction_user, ref):
    """CA33 / CA15 — Paramètres invalides : le journal reflète la vue réellement servie."""
    get(client, direction_user, ref, {"statut": "inconnu", "tri": "sexe", "page": "abc"})
    meta = view_entries().get().metadata
    assert meta["statuts"] in ([], None)
    assert meta["tri"] != "sexe"
    assert meta["page"] == 1
    assert meta["nb_resultats"] == 6


def test_aucun_audit_pour_404(client, direction_user, ref):
    """CA34 — Requête introuvable (404) : aucune entrée ``view``."""
    autre = make_concours("Autre concours fictif")
    assert get(client, direction_user, url(autre, ref["p1"])).status_code == 404
    assert get(client, direction_user, url(autre, ref["p1"]), **HX).status_code == 404
    assert not view_entries().exists()


# --------------------------------------------------------------------------------------
# Langue (CA35)
# --------------------------------------------------------------------------------------


def _unquote(s: str) -> str:
    s = s.strip()
    assert s.startswith('"') and s.endswith('"')
    return s[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def po_msgids() -> set[str]:
    msgids, current, field = set(), None, None
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(msgid|msgid_plural) (\".*\")$", line)
        if m:
            if current is not None:
                msgids.add(current)
            current, field = _unquote(m.group(2)), m.group(1)
        elif line.startswith('"') and field:
            current += _unquote(line)
        else:
            if current is not None:
                msgids.add(current)
            current, field = None, None
    if current is not None:
        msgids.add(current)
    return msgids


def test_page_en_arabe_rtl(client, direction_user, ref):
    """CA35 — Langue active ``ar`` : ``<html dir="rtl">``."""
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    response = get(client, direction_user, ref, HTTP_ACCEPT_LANGUAGE="ar")
    assert response.status_code == 200
    html = parse(response).find_all(lambda n: n.tag == "html")[0]
    assert html.attrs.get("dir") == "rtl"
    assert html.attrs.get("lang") == "ar"


def test_libelles_des_gabarits_dans_le_catalogue():
    """CA35 — Chaque chaîne ``translate`` / ``blocktranslate`` des gabarits est dans le
    catalogue arabe."""
    msgids = po_msgids()
    missing = []
    for path in TEMPLATES:
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
    assert not missing, missing


def test_aucun_texte_fixe_hors_traduction():
    """CA35 — Aucun texte fixe hors balises de traduction dans les gabarits de la liste."""
    offenders = []
    for path in TEMPLATES:
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
        source = re.sub(r"\{%\s*blocktranslate.*?endblocktranslate\s*%\}", "", source, flags=re.S)
        for attr in re.findall(r"""(?:placeholder|aria-label|title|alt)="([^"]*)\"""", source):
            if re.search(r"[A-Za-zÀ-ÿ]", re.sub(r"\{[{%].*?[}%]\}", "", attr)):
                offenders.append((path.name, attr))
        text = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", source, flags=re.S)
        text = re.sub(r"<[^>]*>", "", text, flags=re.S)
        for word in re.findall(r"[A-Za-zÀ-ÿ]{2,}", text):
            offenders.append((path.name, word))
    assert not offenders, offenders


def test_libelles_des_choix_de_verdict_et_en_tetes_traduisibles():
    """CA35 — Libellés de verdict et en-têtes de tri affichés : présents dans le catalogue."""
    msgids = po_msgids()
    labels = [label for _, label in VERDICT_CHOICES]
    labels += ["Candidat", "Date de candidature", "Statut", "Verdict d’éligibilité", "Anomalies"]
    missing = [label for label in labels if label not in msgids]
    assert not missing, missing


def test_libelles_du_filtre_anomalies_traduisibles():
    """CA35 — Libellés du filtre anomalies (« toutes », « avec / sans anomalie non résolue ») :
    présents dans le catalogue arabe."""
    msgids = po_msgids()
    missing = [label for _, label in ANOMALIE_CHOICES if label not in msgids]
    assert not missing, missing


def test_libelles_des_statuts_traduisibles():
    """CA35 — Libellés des 9 statuts (cases du filtre et colonne statut) : présents dans le
    catalogue arabe."""
    msgids = po_msgids()
    missing = [str(label) for _, label in S.choices if str(label) not in msgids]
    assert not missing, missing


def test_pas_de_classes_d_alignement_figees():
    """CA35 — Pas de ``text-left``/``text-right``, ``ml-``/``mr-``, ``pl-``/``pr-``,
    ``left-``/``right-``, ``float-left``/``float-right`` dans les gabarits."""
    pattern = re.compile(
        r"(?<![\w-])(?:text-(?:left|right)|float-(?:left|right)|-?m[lr]-[\w.]+|p[lr]-[\w.]+"
        r"|-?(?:left|right)-[\w.]+|border-[lr](?:-[\w.]+)?|rounded-[lr](?:-[\w.]+)?)(?![\w-])"
    )
    offenders = []
    for path in TEMPLATES:
        for cls_attr in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            offenders += [(path.name, m.group(0)) for m in pattern.finditer(cls_attr)]
    assert not offenders, offenders


# --- Relecture conformité : aucune donnée nominative hors du serveur ------------------


def test_historique_htmx_sans_copie_locale(client, direction_user, ref):
    """La copie des pages dans le localStorage par htmx est désactivée (spec : « pas de
    localStorage ») : l'historique est toujours rechargé depuis le serveur."""
    html = get(client, direction_user, ref).content.decode()
    match = re.search(r'<meta name="htmx-config" content=\'([^\']+)\'', html)
    assert match, "configuration htmx absente"
    config = json.loads(match.group(1))
    assert config["historyCacheSize"] == 0
    assert config["refreshOnHistoryMiss"] is True


def test_restauration_d_historique_renvoie_la_page_complete(client, direction_user, ref):
    response = get(
        client,
        direction_user,
        ref,
        HTTP_HX_REQUEST="true",
        HTTP_HX_HISTORY_RESTORE_REQUEST="true",
    )
    assert "<html" in response.content.decode()
    assert "HX-Request" in response.headers["Vary"]


def test_pages_connectees_jamais_mises_en_cache(client, direction_user, ref):
    for extra in ({}, {"HTTP_HX_REQUEST": "true"}):
        cache_control = get(client, direction_user, ref, **extra).headers["Cache-Control"]
        assert "no-store" in cache_control and "private" in cache_control


def test_recherche_d_un_caractere_non_conservee_dans_le_journal(client, direction_user, ref):
    from apps.audit.models import AuditAction, JournalAudit

    get(client, direction_user, ref, {"q": "a"})
    entry = JournalAudit.objects.filter(action=AuditAction.VIEW).latest("id")
    assert entry.metadata["recherche"] == ""


def test_liens_construits_a_partir_des_parametres_valides(client, direction_user, ref):
    html = get(
        client, direction_user, ref, {"inconnu": "x<y", "statut": ["recue", "faux"]}
    ).content.decode()
    assert "inconnu" not in html
    assert "faux" not in re.sub(r"<input[^>]+>", "", html)
