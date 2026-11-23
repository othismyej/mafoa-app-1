"""Tests d'acceptation de KAN-9 — Vue d'un concours : postes et répartition des candidatures.

Les attentes sont fondées sur la spécification ``docs/specs/KAN-9-vue-concours.md``.
Toutes les données sont fictives (domaines example.com / example.org).
"""

import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.models import Verdict, VerificationEligibilite
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    OffreEmploi,
    Organisation,
    PosteConcours,
)

from .conftest import make_user

pytestmark = pytest.mark.django_db

BASE_DIR = Path(settings.BASE_DIR)
COUNTER_COLS = ("eligibles", "non_eligibles", "a_verifier", "non_evaluees")


# --------------------------------------------------------------------------------------
# Analyse HTML minimale (pas de dépendance externe)
# --------------------------------------------------------------------------------------

VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source", "col", "area", "base", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []
        self.texts = []

    def text(self) -> str:
        parts = []
        for item in self.texts_and_children():
            parts.append(item if isinstance(item, str) else item.text())
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def texts_and_children(self):
        return self._ordered

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
        self.root._ordered = []
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        node._ordered = []
        self.current.children.append(node)
        self.current._ordered.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        self.current._ordered.append(data)


def parse(html: str) -> Node:
    builder = TreeBuilder()
    builder.feed(html)
    return builder.root


def poste_rows(root: Node) -> list[Node]:
    return root.find_all(lambda n: n.tag == "tr" and "data-poste" in n.attrs)


def row_for(root: Node, poste: PosteConcours) -> Node:
    rows = [r for r in poste_rows(root) if r.attrs["data-poste"] == str(poste.pk)]
    assert len(rows) == 1, f"ligne du poste {poste.reference} introuvable ou dupliquée"
    return rows[0]


def cell(row: Node, col: str) -> Node:
    cells = row.find_all(lambda n: n.attrs.get("data-col") == col)
    assert len(cells) == 1, f"colonne {col} introuvable"
    return cells[0]


def counters(row: Node) -> dict[str, int]:
    return {col: int(cell(row, col).text()) for col in ("candidatures", *COUNTER_COLS)}


def tds(row: Node) -> list[str]:
    return [c.text() for c in row.children if c.tag == "td"]


def totals_row(root: Node) -> Node:
    rows = root.find_all(lambda n: n.attrs.get("data-row") == "totaux")
    assert len(rows) == 1
    return rows[0]


def header_total(root: Node, name: str) -> int:
    nodes = root.find_all(lambda n: n.attrs.get("data-total") == name)
    assert len(nodes) == 1
    return int(nodes[0].text())


def main_html(html: str) -> str:
    """Contenu propre à la page (hors en-tête commun de base.html)."""
    match = re.search(r"<main\b.*?</main>", html, re.S)
    assert match
    return match.group(0)


# --------------------------------------------------------------------------------------
# Jeu de données
# --------------------------------------------------------------------------------------

_counter = {"n": 0}


def new_candidat(**kwargs) -> Candidat:
    _counter["n"] += 1
    n = _counter["n"]
    defaults = {
        "nom": f"Fictif{n}",
        "prenom": f"Prenom{n}",
        "email": f"candidat{n}@example.org",
    }
    defaults.update(kwargs)
    return Candidat.objects.create(**defaults)


def new_candidature(poste, statut=Candidature.Statut.RECUE, candidat=None) -> Candidature:
    return Candidature.objects.create(
        candidat=candidat or new_candidat(),
        poste_concours=poste,
        date_candidature=timezone.now(),
        statut=statut,
    )


def add_verif(candidature, resultat, at=None) -> VerificationEligibilite:
    verif = VerificationEligibilite.objects.create(
        candidature=candidature, resultat=resultat, moteur_version="test"
    )
    if at is not None:
        VerificationEligibilite.objects.filter(pk=verif.pk).update(created_at=at)
    return verif


def T(days: int) -> datetime:
    return timezone.make_aware(datetime(2026, 4, 1, 10, 0)) + timedelta(days=days)


def make_concours(intitule="Concours fictif", **kwargs) -> Concours:
    org = kwargs.pop("organisation", None) or Organisation.objects.create(
        nom=f"Organisation fictive {intitule}", type=Organisation.Type.PUBLIC
    )
    return Concours.objects.create(organisation=org, intitule=intitule, **kwargs)


@pytest.fixture
def reference():
    """Jeu de données de référence de la spec (CA2 à CA8)."""
    org = Organisation.objects.create(nom="SRM Région Fictive Nord", type=Organisation.Type.PUBLIC)
    concours = Concours.objects.create(
        organisation=org,
        intitule="Concours fictif SRM Nord 2026",
        date_publication=date(2026, 2, 1),
        date_limite=date(2026, 3, 31),
        statut=Concours.Statut.EN_EVALUATION,
    )
    # Création dans le désordre pour vérifier le tri.
    p03 = PosteConcours.objects.create(
        concours=concours,
        reference="P-03",
        intitule="Chef de service RH",
        nombre_postes=1,
        date_limite=date(2026, 3, 20),
    )
    p01 = PosteConcours.objects.create(
        concours=concours,
        reference="P-01",
        intitule="Ingénieur réseaux",
        nombre_postes=2,
        date_limite=date(2026, 3, 15),
    )
    p02 = PosteConcours.objects.create(
        concours=concours, reference="P-02", intitule="Technicien maintenance", nombre_postes=3
    )

    candidats = {}

    def cand(key, poste, statut, **identity):
        c = new_candidat(**identity)
        candidats[key] = c
        return new_candidature(poste, statut=statut, candidat=c)

    # P-01 : 2 éligibles, 1 non éligible, 1 à vérifier, 1 sans vérification.
    e1 = cand(
        "e1",
        p01,
        Candidature.Statut.ELIGIBLE,
        nom="Zerhouni",
        prenom="Yasmina",
        email="yasmina.zerhouni@example.org",
        telephone="+212600000101",
        cin="ZZ999001",
    )
    add_verif(e1, Verdict.A_VERIFIER, T(0))
    add_verif(e1, Verdict.ELIGIBLE, T(1))
    e2 = cand(
        "e2",
        p01,
        Candidature.Statut.CONVOQUEE,
        nom="Belkadiri",
        prenom="Anouar",
        email="anouar.belkadiri@example.org",
        telephone="+212600000102",
        cin="ZZ999002",
    )
    add_verif(e2, Verdict.ELIGIBLE, T(0))
    ne = cand(
        "ne",
        p01,
        Candidature.Statut.NON_ELIGIBLE,
        nom="Ouazzanix",
        prenom="Salmane",
        email="salmane.ouazzanix@example.org",
        telephone="+212600000103",
        cin="ZZ999003",
    )
    add_verif(ne, Verdict.NON_ELIGIBLE, T(0))
    av = cand(
        "av",
        p01,
        Candidature.Statut.REJETEE,
        nom="Tazimouti",
        prenom="Ghizlane",
        email="ghizlane.tazimouti@example.org",
        telephone="+212600000104",
        cin="ZZ999004",
    )
    add_verif(av, Verdict.A_VERIFIER, T(0))
    cand(
        "nv",
        p01,
        Candidature.Statut.RECUE,
        nom="Lamrabtix",
        prenom="Idriss",
        email="idriss.lamrabtix@example.org",
        telephone="+212600000105",
        cin="ZZ999005",
    )
    # P-03 : 1 à vérifier (précédée d'une vérification « non éligible »).
    p3c = cand(
        "p3",
        p03,
        Candidature.Statut.A_VERIFIER,
        nom="Fennichix",
        prenom="Kawtar",
        email="kawtar.fennichix@example.org",
        telephone="+212600000106",
        cin="ZZ999006",
    )
    add_verif(p3c, Verdict.NON_ELIGIBLE, T(0))
    add_verif(p3c, Verdict.A_VERIFIER, T(2))

    # Bruit : candidature sur une offre d'emploi et concours voisin, jamais comptés ici.
    offre = OffreEmploi.objects.create(organisation=org, intitule="Offre fictive")
    oc = Candidature.objects.create(
        candidat=candidats["e1"], offre=offre, date_candidature=timezone.now()
    )
    add_verif(oc, Verdict.ELIGIBLE)
    other = make_concours("Autre concours fictif")
    other_poste = PosteConcours.objects.create(
        concours=other, reference="P-01", intitule="Autre poste", nombre_postes=4
    )
    for _ in range(3):
        add_verif(new_candidature(other_poste), Verdict.A_VERIFIER)

    return {
        "concours": concours,
        "p01": p01,
        "p02": p02,
        "p03": p03,
        "candidats": candidats,
        "p3_candidature": p3c,
        "convoquee": e2,
    }


def detail_url(concours) -> str:
    return reverse("recruitment:concours_detail", args=[concours.pk])


def get_page(client, user, concours, **extra):
    client.force_login(user)
    response = client.get(detail_url(concours), **extra)
    return response


def view_entries():
    return JournalAudit.objects.filter(action=AuditAction.VIEW)


# --------------------------------------------------------------------------------------
# CA1 — lien depuis le tableau de bord
# --------------------------------------------------------------------------------------


def test_intitule_du_concours_est_un_lien_vers_sa_fiche_sur_le_tableau_de_bord(
    client, direction_user
):
    """CA1 — Sur le tableau de bord, l'intitulé du concours pointe vers sa fiche."""
    concours = make_concours("Concours fictif tableau de bord", date_limite=date(2026, 5, 1))
    client.force_login(direction_user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    root = parse(response.content.decode())
    links = root.find_all(lambda n: n.tag == "a" and n.text() == concours.intitule)
    assert len(links) == 1
    assert links[0].attrs["href"] == detail_url(concours)


def test_concours_sans_slug_est_adresse_par_sa_cle_primaire(client, direction_user):
    """Règle 2 — La fiche est identifiée par la clé primaire, même avec un slug vide."""
    concours = make_concours("Concours fictif sans slug", slug="")
    assert detail_url(concours) == f"/concours/{concours.pk}/"
    assert get_page(client, direction_user, concours).status_code == 200


# --------------------------------------------------------------------------------------
# CA2 à CA8 — en-tête, tableau, répartition
# --------------------------------------------------------------------------------------


def test_en_tete_du_concours(client, direction_user, reference):
    """CA2 — 200, intitulé, organisation, libellé du statut, date limite, 6 postes, 6 candidatures."""
    response = get_page(client, direction_user, reference["concours"])
    assert response.status_code == 200
    html = response.content.decode()
    content = main_html(html)
    assert "Concours fictif SRM Nord 2026" in content
    assert "SRM Région Fictive Nord" in content
    assert "En évaluation" in content
    assert "31/03/2026" in content
    assert "01/02/2026" in content  # date de publication (règle 3)
    root = parse(html)
    assert header_total(root, "postes") == 6
    assert header_total(root, "candidatures") == 6


def test_tableau_des_postes_ordre_et_colonnes(client, direction_user, reference):
    """CA3 — Exactement 3 lignes, dans l'ordre P-01, P-02, P-03, avec référence, intitulé,
    nombre de postes et date limite."""
    root = parse(get_page(client, direction_user, reference["concours"]).content.decode())
    rows = poste_rows(root)
    assert [r.attrs["data-poste"] for r in rows] == [
        str(reference[k].pk) for k in ("p01", "p02", "p03")
    ]
    expected = [
        ("P-01", "Ingénieur réseaux", "2", "15/03/2026"),
        ("P-02", "Technicien maintenance", "3", "31/03/2026"),
        ("P-03", "Chef de service RH", "1", "20/03/2026"),
    ]
    for row, (ref, intitule, nb, date_limite) in zip(rows, expected, strict=True):
        cells = tds(row)
        assert ref in cells
        assert intitule in cells
        assert nb in cells
        assert any(c.startswith(date_limite) for c in cells)


def test_repartition_du_poste_p01(client, direction_user, reference):
    """CA4 — P-01 : 5 candidatures, 2 éligibles, 1 non éligible, 1 à vérifier, 1 non évaluée."""
    root = parse(get_page(client, direction_user, reference["concours"]).content.decode())
    assert counters(row_for(root, reference["p01"])) == {
        "candidatures": 5,
        "eligibles": 2,
        "non_eligibles": 1,
        "a_verifier": 1,
        "non_evaluees": 1,
    }


def test_poste_sans_date_limite_utilise_celle_du_concours(client, direction_user, reference):
    """CA5 — P-02 : « 31/03/2026 (date du concours) » et compteurs tous à 0."""
    root = parse(get_page(client, direction_user, reference["concours"]).content.decode())
    row = row_for(root, reference["p02"])
    assert any(re.fullmatch(r"31/03/2026\s*\(date du concours\)", c) for c in tds(row)), tds(row)
    assert set(counters(row).values()) == {0}
    # Les postes ayant leur propre date ne portent pas la mention.
    assert "(date du concours)" not in row_for(root, reference["p01"]).text()
    assert "(date du concours)" not in row_for(root, reference["p03"]).text()


def test_seule_la_derniere_verification_compte(client, direction_user, reference):
    """CA6 — « non éligible » puis « à vérifier » plus récente : compté seulement « à vérifier »."""
    root = parse(get_page(client, direction_user, reference["concours"]).content.decode())
    assert counters(row_for(root, reference["p03"])) == {
        "candidatures": 1,
        "eligibles": 0,
        "non_eligibles": 0,
        "a_verifier": 1,
        "non_evaluees": 0,
    }


def test_derniere_verification_meme_si_creee_avant(client, direction_user):
    """CA6 — Le verdict suit created_at, pas l'ordre d'insertion (vérification plus ancienne
    insérée en dernier)."""
    concours = make_concours("Concours fictif ordre")
    poste = PosteConcours.objects.create(concours=concours, reference="X", intitule="Poste")
    cand = new_candidature(poste)
    add_verif(cand, Verdict.A_VERIFIER, T(5))
    add_verif(cand, Verdict.NON_ELIGIBLE, T(1))  # id plus élevé, mais plus ancienne
    root = parse(get_page(client, direction_user, concours).content.decode())
    c = counters(row_for(root, poste))
    assert c["a_verifier"] == 1
    assert c["non_eligibles"] == 0


def test_egalite_de_date_l_identifiant_le_plus_eleve_l_emporte(client, direction_user):
    """Cas limite (règle 5) — created_at identiques : la vérification d'id le plus élevé gagne."""
    concours = make_concours("Concours fictif égalité")
    poste = PosteConcours.objects.create(concours=concours, reference="X", intitule="Poste")
    cand_a = new_candidature(poste)
    cand_b = new_candidature(poste)
    same = T(3)
    add_verif(cand_a, Verdict.NON_ELIGIBLE, same)
    add_verif(cand_a, Verdict.ELIGIBLE, same)
    add_verif(cand_b, Verdict.ELIGIBLE, same)
    add_verif(cand_b, Verdict.A_VERIFIER, same)
    root = parse(get_page(client, direction_user, concours).content.decode())
    assert counters(row_for(root, poste)) == {
        "candidatures": 2,
        "eligibles": 1,
        "non_eligibles": 0,
        "a_verifier": 1,
        "non_evaluees": 0,
    }


def test_candidature_convoquee_eligible_comptee_parmi_les_eligibles(client, direction_user):
    """CA7 — Statut « convoquée », dernier verdict « éligible » : compté parmi les éligibles."""
    concours = make_concours("Concours fictif convoqués")
    poste = PosteConcours.objects.create(concours=concours, reference="C-01", intitule="Poste")
    convoquee = new_candidature(poste, statut=Candidature.Statut.CONVOQUEE)
    add_verif(convoquee, Verdict.ELIGIBLE)
    # Statut contradictoire avec le verdict : la répartition suit le verdict.
    statut_eligible = new_candidature(poste, statut=Candidature.Statut.ELIGIBLE)
    add_verif(statut_eligible, Verdict.A_VERIFIER)
    sans_verif = new_candidature(poste, statut=Candidature.Statut.ELIGIBLE)
    assert sans_verif.statut == Candidature.Statut.ELIGIBLE
    root = parse(get_page(client, direction_user, concours).content.decode())
    assert counters(row_for(root, poste)) == {
        "candidatures": 3,
        "eligibles": 1,
        "non_eligibles": 0,
        "a_verifier": 1,
        "non_evaluees": 1,
    }


def test_ligne_de_totaux_et_coherence(client, direction_user, reference):
    """CA8 — Totaux 6 / 6 / 2 / 1 / 2 / 1 ; pour chaque ligne, somme des compteurs = candidatures ;
    total de l'en-tête = somme des candidatures des postes (règle 6)."""
    root = parse(get_page(client, direction_user, reference["concours"]).content.decode())
    totals = totals_row(root)
    assert int(cell(totals, "postes").text()) == 6
    assert counters(totals) == {
        "candidatures": 6,
        "eligibles": 2,
        "non_eligibles": 1,
        "a_verifier": 2,
        "non_evaluees": 1,
    }
    rows = poste_rows(root)
    for row in rows:
        c = counters(row)
        assert sum(c[col] for col in COUNTER_COLS) == c["candidatures"]
    assert header_total(root, "candidatures") == sum(counters(r)["candidatures"] for r in rows)
    for col in ("candidatures", *COUNTER_COLS):
        assert counters(totals)[col] == sum(counters(r)[col] for r in rows)


def test_candidatures_sur_offre_et_autres_concours_non_comptees(client, direction_user, reference):
    """Cas limite — Les candidatures sur offres d'emploi et d'autres concours ne sont jamais
    comptées."""
    root = parse(get_page(client, direction_user, reference["concours"]).content.decode())
    assert header_total(root, "candidatures") == 6
    assert int(cell(totals_row(root), "a_verifier").text()) == 2
    assert header_total(root, "postes") == 6


def test_comptes_recalcules_a_chaque_affichage(client, direction_user, reference):
    """Règle 12 — Pas de cache : une nouvelle vérification est visible à l'affichage suivant."""
    get_page(client, direction_user, reference["concours"])
    nouvelle = new_candidature(reference["p02"])
    add_verif(nouvelle, Verdict.NON_ELIGIBLE)
    root = parse(client.get(detail_url(reference["concours"])).content.decode())
    assert counters(row_for(root, reference["p02"]))["non_eligibles"] == 1
    assert header_total(root, "candidatures") == 7


# --------------------------------------------------------------------------------------
# Cas limites d'affichage
# --------------------------------------------------------------------------------------


def test_concours_sans_poste(client, direction_user):
    """CA9 — 200, message « Aucun poste pour ce concours. » à la place du tableau ; en-tête à 0."""
    concours = make_concours("Concours fictif vide")
    response = get_page(client, direction_user, concours)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Aucun poste pour ce concours." in html
    root = parse(html)
    assert root.find_all(lambda n: n.tag == "table") == []
    assert poste_rows(root) == []
    assert header_total(root, "postes") == 0
    assert header_total(root, "candidatures") == 0


def test_dates_absentes_affichees_tiret(client, direction_user):
    """Règle 3 / 4 et cas limite — Dates absentes : « — » dans l'en-tête et dans le tableau."""
    concours = make_concours("Concours fictif sans dates")
    poste = PosteConcours.objects.create(concours=concours, reference="", intitule="Poste")
    root = parse(get_page(client, direction_user, concours).content.decode())
    dds = root.find_all(lambda n: n.tag == "dd")
    assert sum(1 for d in dds if d.text() == "—") >= 2  # publication et date limite
    cells = tds(row_for(root, poste))
    assert cells[0] == "—"  # référence vide
    assert cells.count("—") >= 2  # référence et date limite
    assert "(date du concours)" not in " ".join(cells)


def test_postes_sans_reference_en_premier_puis_ordre_lexicographique(client, direction_user):
    """Règle 8 et cas limites — Sans référence d'abord (par intitulé), puis ordre
    lexicographique (« P-10 » avant « P-2 »)."""
    concours = make_concours("Concours fictif tri")
    p2 = PosteConcours.objects.create(concours=concours, reference="P-2", intitule="A")
    p10 = PosteConcours.objects.create(concours=concours, reference="P-10", intitule="B")
    sans_b = PosteConcours.objects.create(concours=concours, reference="", intitule="Zeta")
    sans_a = PosteConcours.objects.create(concours=concours, reference="", intitule="Alpha")
    meme_ref_b = PosteConcours.objects.create(concours=concours, reference="P-3", intitule="Y")
    meme_ref_a = PosteConcours.objects.create(concours=concours, reference="P-3", intitule="X")
    root = parse(get_page(client, direction_user, concours).content.decode())
    order = [r.attrs["data-poste"] for r in poste_rows(root)]
    assert order == [str(p.pk) for p in (sans_a, sans_b, p10, p2, meme_ref_a, meme_ref_b)]


@pytest.mark.parametrize("statut", [s.value for s in Concours.Statut])
def test_tous_les_statuts_sont_consultables(client, direction_user, statut):
    """Règle 15 — Tous les statuts, y compris « brouillon » et « terminé », sont consultables."""
    concours = make_concours(f"Concours fictif {statut}", statut=statut)
    response = get_page(client, direction_user, concours)
    assert response.status_code == 200
    assert concours.get_statut_display() in main_html(response.content.decode())


def test_fil_d_ariane(client, direction_user, reference):
    """Règle 14 — Fil d'Ariane « Tableau de bord › <intitulé> » avec lien retour."""
    content = main_html(get_page(client, direction_user, reference["concours"]).content.decode())
    root = parse(content)
    navs = root.find_all(lambda n: n.tag == "nav")
    assert navs, "fil d'Ariane absent"
    nav = navs[0]
    assert re.fullmatch(r"Tableau de bord\s*›\s*Concours fictif SRM Nord 2026", nav.text())
    links = nav.find_all(lambda n: n.tag == "a")
    assert [a.attrs["href"] for a in links] == [reverse("dashboard")]
    assert links[0].text() == "Tableau de bord"


def test_a_verifier_mis_en_avant_et_aucune_couleur_rouge(client, direction_user, reference):
    """Règle 9 — « à vérifier » > 0 distingué en ambre ; à 0, pas de mise en avant ; aucune
    couleur rouge."""
    html = get_page(client, direction_user, reference["concours"]).content.decode()
    root = parse(html)
    assert "amber" in cell(row_for(root, reference["p01"]), "a_verifier").attrs.get("class", "")
    assert "amber" not in cell(row_for(root, reference["p02"]), "a_verifier").attrs.get("class", "")
    assert not re.search(r"\b(text|bg|border)-(red|rose)-\d+", main_html(html))


def test_non_eligible_n_est_pas_presente_comme_une_decision(client, direction_user, reference):
    """Règle 9 / humain dans la boucle — Pas de formulation « éliminé », « rejeté » ou
    « décision » qui présenterait le verdict comme une décision."""
    content = main_html(
        get_page(client, direction_user, reference["concours"]).content.decode()
    ).lower()
    for word in ("éliminé", "elimine", "rejeté", "rejetée", "rejet"):
        assert word not in content


def test_fiche_en_lecture_seule(client, direction_user, reference):
    """Règle 11 — Aucune action : ni formulaire, ni bouton, ni élément HTMX dans la page."""
    content = main_html(get_page(client, direction_user, reference["concours"]).content.decode())
    root = parse(content)
    assert root.find_all(lambda n: n.tag in ("form", "button", "input", "select")) == []
    assert not re.search(r"\bhx-(post|put|patch|delete|get)\b", content)


def test_post_refuse_405(client, direction_user, reference):
    """Cas limite — Seule GET est acceptée : POST renvoie 405 sans entrée d'audit."""
    client.force_login(direction_user)
    response = client.post(detail_url(reference["concours"]))
    assert response.status_code == 405
    assert not view_entries().exists()


# --------------------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------------------


def test_identifiant_inexistant_404_sans_audit(client, direction_user, reference):
    """CA10 — Identifiant inexistant : 404 et aucune entrée d'audit."""
    client.force_login(direction_user)
    missing = Concours.objects.order_by("-pk").first().pk + 1000
    response = client.get(reverse("recruitment:concours_detail", args=[missing]))
    assert response.status_code == 404
    assert not view_entries().exists()


def test_admin_accede_a_la_fiche(client, admin_user, reference):
    """CA11 — admin avec TOTP : 200."""
    response = get_page(client, admin_user, reference["concours"])
    assert response.status_code == 200
    assert "Concours fictif SRM Nord 2026" in response.content.decode()


def test_non_connecte_redirige_vers_la_connexion_avec_next(client, reference):
    """CA12 — Non connecté : 302 vers la connexion avec ``next`` = URL demandée ; pas d'audit."""
    url = detail_url(reference["concours"])
    response = client.get(url)
    assert response.status_code == 302
    target = urlparse(response.url)
    assert target.path == reverse("account_login")
    assert parse_qs(target.query)["next"] == [url]
    assert not view_entries().exists()


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
def test_roles_vague_2_refuses_403(client, reference, role):
    """CA13 — consultant / client avec TOTP : 403, aucune donnée du concours, pas d'audit."""
    user = make_user(f"{role}@example.com", role)
    response = get_page(client, user, reference["concours"])
    assert response.status_code == 403
    body = response.content.decode()
    for value in (
        "Concours fictif SRM Nord 2026",
        "SRM Région Fictive Nord",
        "Ingénieur réseaux",
        "P-01",
    ):
        assert value not in body
    assert not view_entries().exists()


def test_sans_totp_redirige_vers_l_activation(client, user_without_mfa, reference):
    """CA14 — direction sans TOTP actif : redirection vers l'activation du TOTP ; pas d'audit."""
    response = get_page(client, user_without_mfa, reference["concours"])
    assert response.status_code == 302
    assert urlparse(response.url).path == reverse("mfa_activate_totp")
    assert not view_entries().exists()


def test_utilisateur_inactif_refuse(client, reference):
    """Rôles — Utilisateur inactif : session refusée, aucune donnée ni audit de consultation."""
    user = make_user("inactif@example.com", Role.DIRECTION)
    client.force_login(user)
    user.is_active = False
    user.save()
    response = client.get(detail_url(reference["concours"]))
    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert urlparse(response.url).path == reverse("account_login")
    assert "Concours fictif SRM Nord 2026" not in response.content.decode()
    assert not view_entries().exists()


# --------------------------------------------------------------------------------------
# Performance (requêtes SQL)
# --------------------------------------------------------------------------------------

MAX_QUERIES = 15


def bulk_concours(intitule, nb_postes, nb_candidatures):
    concours = make_concours(intitule, date_limite=date(2026, 6, 30))
    postes = PosteConcours.objects.bulk_create(
        [
            PosteConcours(concours=concours, reference=f"R-{i:03d}", intitule=f"Poste {i}")
            for i in range(nb_postes)
        ]
    )
    total = nb_postes * nb_candidatures
    candidats = Candidat.objects.bulk_create(
        [Candidat(nom=f"Volume{intitule[-1]}{i}", prenom="Fictif") for i in range(total)]
    )
    now = timezone.now()
    candidatures = Candidature.objects.bulk_create(
        [
            Candidature(
                candidat=candidats[i],
                poste_concours=postes[i % nb_postes],
                concours=concours,
                date_candidature=now,
            )
            for i in range(total)
        ]
    )
    verdicts = [Verdict.ELIGIBLE, Verdict.NON_ELIGIBLE, Verdict.A_VERIFIER, None]
    verifs = []
    for i, cand in enumerate(candidatures):
        v = verdicts[i % 4]
        if v is not None:
            verifs.append(
                VerificationEligibilite(
                    candidature=cand, resultat=Verdict.A_VERIFIER, moteur_version="t"
                )
            )
            verifs.append(VerificationEligibilite(candidature=cand, resultat=v, moteur_version="t"))
    VerificationEligibilite.objects.bulk_create(verifs)
    return concours


def test_nombre_de_requetes_constant_et_borne(client, direction_user):
    """CA15 — 3 × 10 et 30 × 100 : même nombre de requêtes SQL, borné par une constante."""
    petit = bulk_concours("Concours volume A", 3, 10)
    grand = bulk_concours("Concours volume B", 30, 100)
    client.force_login(direction_user)
    # Préchauffage (caches ContentType, etc.) sur un concours tiers.
    assert client.get(detail_url(make_concours("Concours volume C"))).status_code == 200

    with CaptureQueriesContext(connection) as q_petit:
        r_petit = client.get(detail_url(petit))
    with CaptureQueriesContext(connection) as q_grand:
        r_grand = client.get(detail_url(grand))
    assert r_petit.status_code == r_grand.status_code == 200
    assert len(q_petit) == len(q_grand), (len(q_petit), len(q_grand))
    assert len(q_grand) <= MAX_QUERIES, len(q_grand)

    # Sanité des comptes sur le gros volume (2 vérifications par candidature évaluée).
    root = parse(r_grand.content.decode())
    totals = counters(totals_row(root))
    assert totals == {
        "candidatures": 3000,
        "eligibles": 750,
        "non_eligibles": 750,
        "a_verifier": 750,
        "non_evaluees": 750,
    }
    assert len(poste_rows(root)) == 30


# --------------------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------------------


def test_consultation_journalisee(client, direction_user, reference):
    """CA16 — Entrée JournalAudit « view » : acteur, type Concours, identifiant, IP, navigateur ;
    pas d'avant/après."""
    concours = reference["concours"]
    response = get_page(
        client, direction_user, concours, REMOTE_ADDR="10.20.30.40", HTTP_USER_AGENT="pytest-kan9"
    )
    assert response.status_code == 200
    entries = list(view_entries())
    assert len(entries) == 1
    entry = entries[0]
    assert entry.get_action_display() == "Consultation"
    assert entry.actor == direction_user
    assert entry.content_type == ContentType.objects.get_for_model(Concours)
    assert entry.object_id == str(concours.pk)
    assert entry.ip_address == "10.20.30.40"
    assert entry.user_agent == "pytest-kan9"
    assert entry.timestamp is not None
    assert entry.before is None
    assert entry.after is None


def test_chaque_affichage_cree_une_entree(client, admin_user, reference):
    """CA16 / traçabilité — Une entrée « view » par affichage (y compris pour admin)."""
    get_page(client, admin_user, reference["concours"])
    client.get(detail_url(reference["concours"]))
    assert view_entries().filter(actor=admin_user).count() == 2


# --------------------------------------------------------------------------------------
# Données personnelles
# --------------------------------------------------------------------------------------


def test_aucune_donnee_personnelle_de_candidat(client, direction_user, reference):
    """CA17 — Le HTML ne contient aucun nom, prénom, email, téléphone ni CIN de candidat."""
    html = get_page(client, direction_user, reference["concours"]).content.decode()
    for candidat in reference["candidats"].values():
        for value in (
            candidat.nom,
            candidat.prenom,
            candidat.email,
            candidat.telephone,
            candidat.cin,
        ):
            assert value
            assert value.lower() not in html.lower(), value


# --------------------------------------------------------------------------------------
# Navigation vers KAN-10
# --------------------------------------------------------------------------------------


def _kan10_livre() -> bool:
    try:
        reverse("recruitment:poste_candidatures", args=[1])
    except NoReverseMatch:
        return False
    return True


def test_compteur_nul_n_est_pas_un_lien(client, direction_user, reference):
    """CA18 (partie applicable) — Un compteur nul n'est jamais un lien ; tant que KAN-10 n'est
    pas livré, aucun compteur n'est un lien (règle 10)."""
    root = parse(get_page(client, direction_user, reference["concours"]).content.decode())
    for row in poste_rows(root):
        for col in COUNTER_COLS:
            node = cell(row, col)
            has_link = bool(node.find_all(lambda n: n.tag == "a"))
            if int(node.text()) == 0 or not _kan10_livre():
                assert not has_link, (row.attrs["data-poste"], col)
    # P-02 n'a aucune candidature : aucun lien sur ses compteurs.
    p02 = row_for(root, reference["p02"])
    for col in ("candidatures", *COUNTER_COLS):
        assert not cell(p02, col).find_all(lambda n: n.tag == "a")


# --------------------------------------------------------------------------------------
# i18n / RTL
# --------------------------------------------------------------------------------------


def _po_msgids(path: Path) -> set[str]:
    msgids, current, in_msgid = set(), None, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            current = _unquote(line[6:])
            in_msgid = True
        elif line.startswith('"') and in_msgid:
            current += _unquote(line)
        else:
            if in_msgid and current is not None:
                msgids.add(current)
            in_msgid = False
            current = None
    if in_msgid and current is not None:
        msgids.add(current)
    return msgids


def _unquote(s: str) -> str:
    s = s.strip()
    assert s.startswith('"') and s.endswith('"')
    return s[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


TEMPLATE = BASE_DIR / "templates" / "recruitment" / "concours_detail.html"
CATALOG = BASE_DIR / "locale" / "ar" / "LC_MESSAGES" / "django.po"


def test_page_en_arabe_rtl(client, direction_user, reference):
    """CA19 — Langue active ``ar`` : 200 et ``<html dir="rtl">``."""
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    response = get_page(client, direction_user, reference["concours"], HTTP_ACCEPT_LANGUAGE="ar")
    assert response.status_code == 200
    html_tags = parse(response.content.decode()).find_all(lambda n: n.tag == "html")
    assert html_tags[0].attrs.get("dir") == "rtl"
    assert html_tags[0].attrs.get("lang") == "ar"


def test_libelles_imposes_par_la_spec_dans_le_catalogue():
    """CA19 — Les libellés fixes imposés par la spec figurent dans le catalogue arabe."""
    msgids = _po_msgids(CATALOG)
    for label in ("Aucun poste pour ce concours.", "(date du concours)", "Tableau de bord"):
        assert label in msgids, label


def test_tous_les_libelles_fixes_passent_par_la_traduction():
    """CA19 — Aucun texte fixe hors balises de traduction dans le gabarit, et chaque chaîne
    traduisible est présente dans le catalogue ``makemessages``."""
    source = TEMPLATE.read_text(encoding="utf-8")
    translated = re.findall(r"""\{%\s*(?:translate|trans)\s+(["'])(.*?)\1""", source)
    assert translated
    msgids = _po_msgids(CATALOG)
    missing = [s for _, s in translated if s not in msgids]
    assert not missing, missing

    stripped = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    stripped = re.sub(r"\{%.*?%\}", "", stripped, flags=re.S)
    stripped = re.sub(r"\{\{.*?\}\}", "", stripped, flags=re.S)
    stripped = re.sub(r"<[^>]*>", "", stripped, flags=re.S)
    leftover = re.findall(r"[A-Za-zÀ-ÿ]{2,}", stripped)
    assert not leftover, leftover
