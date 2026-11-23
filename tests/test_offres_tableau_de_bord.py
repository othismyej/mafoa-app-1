"""Tests d'acceptation de KAN-38 — Offres d'emploi, candidatures privées, tableau de bord clarifié.

Les attentes sont fondées sur ``docs/specs/KAN-38-offres-tableau-de-bord.md``.
Toutes les données sont fictives (domaines example.org). Aucun appel IA.
"""

import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.models import Anomalie, Verdict
from apps.recruitment.filters import filter_candidatures
from apps.recruitment.models import (
    Candidature,
    Concours,
    OffreEmploi,
    Organisation,
    PosteConcours,
)

from .conftest import make_user
from .test_poste_candidatures import (
    HX,
    add_anomalie,
    add_verif,
    at,
    compteur,
    new_candidat,
    parse,
    po_msgids,
    row_ids,
)

pytestmark = pytest.mark.django_db

S = Candidature.Statut
BASE_DIR = Path(settings.BASE_DIR)
NOUVEAUX_GABARITS = [
    BASE_DIR / "templates" / p
    for p in (
        "dashboard/index.html",
        "components/stat.html",
        "components/breadcrumb.html",
        "recruitment/offres_liste.html",
        "recruitment/offre_detail.html",
        "recruitment/a_verifier.html",
        "recruitment/_offres_tableau.html",
        "recruitment/_entreprise.html",
        "recruitment/_candidatures_filtres.html",
        "recruitment/_candidatures_resultats.html",
    )
]

# --------------------------------------------------------------------------------------
# Jeu de données de référence (spec, « Critères d'acceptation »)
# --------------------------------------------------------------------------------------


def candidature(cible, statut, nom=None, prenom=None, when=None):
    kwargs = {"offre": cible} if isinstance(cible, OffreEmploi) else {"poste_concours": cible}
    return Candidature.objects.create(
        candidat=new_candidat(nom=nom, prenom=prenom),
        date_candidature=when or at(2026, 11, 2),
        statut=statut,
        **kwargs,
    )


@pytest.fixture
def ref():
    prive = Organisation.objects.create(nom="Société fictive Atlas", type=Organisation.Type.PRIVE)
    public = Organisation.objects.create(nom="SRM fictive", type=Organisation.Type.PUBLIC)
    c1 = Concours.objects.create(
        organisation=public, intitule="Concours fictif C1", statut=Concours.Statut.OUVERT
    )
    p1 = PosteConcours.objects.create(concours=c1, reference="P-01", intitule="Ingénieur réseaux")
    cp = {
        "a_verifier": candidature(p1, S.A_VERIFIER),
        "eligible": candidature(p1, S.ELIGIBLE),
        "recue": candidature(p1, S.RECUE),
    }
    o1 = OffreEmploi.objects.create(
        organisation=prive,
        intitule="Responsable RH fictif",
        ville="Marrakech",
        type_contrat="CDI",
        statut=OffreEmploi.Statut.OUVERTE,
        nombre_postes=2,
        date_publication=date(2026, 11, 1),
        date_limite=date(2026, 12, 15),
        fiche_de_poste="Piloter la fonction RH d'une entreprise fictive. " * 30,
        competences=["Paie", "Droit social"],
        experience_min_annees=3,
    )
    o2 = OffreEmploi.objects.create(
        organisation=prive,
        intitule="Comptable fictif",
        entreprise_confidentielle=True,
        statut=OffreEmploi.Statut.FERMEE,
        date_publication=date(2026, 10, 1),
    )
    o3 = OffreEmploi.objects.create(
        organisation=prive,
        intitule="Chef de projet fictif",
        statut=OffreEmploi.Statut.POURVUE,
        date_publication=date(2026, 11, 10),
    )
    co = {
        "sara": candidature(o1, S.A_VERIFIER, "El Amrani", "Sara", at(2026, 11, 2, 9)),
        "youssef": candidature(o1, S.A_VERIFIER, "Bennani", "Youssef", at(2026, 11, 3, 9)),
        "karim": candidature(o1, S.ELIGIBLE, "Alaoui", "Karim", at(2026, 11, 4, 9)),
        "nora": candidature(o1, S.RECUE, "Ziani", "Nora", at(2026, 11, 5, 9)),
        "o2": candidature(o2, S.RECUE, "Fictif", "Odette"),
    }
    add_verif(co["sara"], Verdict.A_VERIFIER)
    add_anomalie(co["sara"], Anomalie.Gravite.HAUTE)
    return {"c1": c1, "p1": p1, "o1": o1, "o2": o2, "o3": o3, "cp": cp, "co": co}


def get(client, user, url, **extra):
    client.force_login(user)
    return client.get(url, **extra)


def offres_url(**params):
    q = QueryDict(mutable=True)
    for k, v in params.items():
        q.setlist(k, v if isinstance(v, list) else [v])
    return reverse("recruitment:offres_liste") + (f"?{q.urlencode()}" if q else "")


def offre_url(offre, query=""):
    return reverse("recruitment:offre_detail", args=[offre.pk]) + (f"?{query}" if query else "")


def offre_rows(root) -> list[int]:
    return [
        int(n.attrs["data-offre-id"])
        for n in root.find_all(lambda n: n.tag == "tr" and "data-offre-id" in n.attrs)
    ]


def by_attr(root, attr, value=None):
    return root.find_all(
        lambda n: attr in n.attrs and (value is None or n.attrs.get(attr) == value)
    )


def text(response) -> str:
    return parse(response).text()


def nb_queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200
    return len(ctx.captured_queries)


# --------------------------------------------------------------------------------------
# Liste des offres (CA1 à CA5)
# --------------------------------------------------------------------------------------


def test_ca1_liste_des_offres_ordre_et_compteur(client, direction_user, ref):
    response = get(client, direction_user, offres_url())
    assert response.status_code == 200
    root = parse(response)
    assert compteur(root) == "3 offres affichées sur 3"
    assert offre_rows(root) == [ref["o1"].pk, ref["o3"].pk, ref["o2"].pk]


def test_ca2_colonnes_de_la_liste(client, direction_user, ref):
    root = parse(get(client, direction_user, offres_url()))
    (ligne,) = by_attr(root, "data-offre-id", str(ref["o1"].pk))
    t = ligne.text()
    for attendu in ("Société fictive Atlas", "Marrakech", "CDI", "Ouverte", "15/12/2026"):
        assert attendu in t
    assert by_attr(ligne, "data-col", "candidatures")[0].text() == "4"
    assert by_attr(ligne, "data-col", "a-verifier")[0].text() == "2"
    liens = ligne.find_all(lambda n: n.tag == "a" and n.text() == "Responsable RH fictif")
    assert liens[0].attrs["href"] == offre_url(ref["o1"])
    (o2,) = by_attr(root, "data-offre-id", str(ref["o2"].pk))
    assert by_attr(o2, "data-col", "entreprise")[0].text() == "Entreprise confidentielle"
    assert "Société fictive Atlas" not in o2.text()
    (o3,) = by_attr(root, "data-offre-id", str(ref["o3"].pk))
    assert by_attr(o3, "data-col", "candidatures")[0].text() == "0"
    assert by_attr(o3, "data-col", "a-verifier")[0].text() == "0"


def test_ca3_filtre_par_statut(client, direction_user, ref):
    root = parse(get(client, direction_user, offres_url(statut="fermee")))
    assert offre_rows(root) == [ref["o2"].pk]
    coches = [
        n.attrs["value"]
        for n in root.find_all(lambda n: n.tag == "input" and n.attrs.get("name") == "statut")
        if "checked" in n.attrs
    ]
    assert coches == ["fermee"]
    root = parse(get(client, direction_user, offres_url(statut=["ouverte", "pourvue"])))
    assert offre_rows(root) == [ref["o1"].pk, ref["o3"].pk]
    response = get(client, direction_user, offres_url(statut="inconnu", page="abc"))
    assert response.status_code == 200
    assert len(offre_rows(parse(response))) == 3


def test_ca3_filtre_sans_resultat(client, direction_user, ref):
    ref["o3"].delete()  # seule offre pourvue, sans candidature
    response = get(client, direction_user, offres_url(statut="pourvue"))
    assert "Aucune offre ne correspond à ce filtre." in text(response)


def test_ca4_requetes_constantes_et_pagination(client, direction_user):
    org = Organisation.objects.create(nom="Société fictive Volume", type=Organisation.Type.PRIVE)

    def creer(n):
        for i in range(n):
            o = OffreEmploi.objects.create(organisation=org, intitule=f"Offre fictive {i}")
            candidature(o, S.A_VERIFIER if i % 2 else S.RECUE)

    client.force_login(direction_user)
    creer(10)
    petit = nb_queries(client, offres_url())
    creer(50)
    grand = nb_queries(client, offres_url())
    assert petit == grand
    root = parse(client.get(offres_url()))
    assert len(offre_rows(root)) == 50
    assert "Page 1 sur 2" in root.text()
    assert len(offre_rows(parse(client.get(offres_url(page="2"))))) == 10
    assert len(offre_rows(parse(client.get(offres_url(page="99"))))) == 10


def test_ca5_liste_sans_nom_de_candidat(client, direction_user, ref):
    html = get(client, direction_user, offres_url()).content.decode()
    for nom in ("AMRANI", "BENNANI", "ALAOUI", "ZIANI"):
        assert nom not in html


# --------------------------------------------------------------------------------------
# Page d'une offre (CA6 à CA14)
# --------------------------------------------------------------------------------------


def test_ca6_page_offre_fiche_et_candidatures(client, direction_user, ref):
    response = get(client, direction_user, offre_url(ref["o1"]))
    assert response.status_code == 200
    root = parse(response)
    (fil,) = root.find_all(lambda n: n.tag == "nav" and "breadcrumb" in n.attrs.get("class", ""))
    assert fil.text() == "Tableau de bord › Offres d’emploi › Responsable RH fictif"
    fiche = by_attr(root, "data-fiche-offre")[0].text()
    for attendu in ("Ouverte", "Marrakech", "CDI", "Présentiel", "2", "15/12/2026"):
        assert attendu in fiche
    assert by_attr(root, "data-entreprise")[0].text() == "Société fictive Atlas"
    assert "Lire la fiche complète" in root.text()
    assert "Droit social" in root.text()
    assert "Au moins 3 ans" in by_attr(root, "data-criteres")[0].text()
    co = ref["co"]
    assert row_ids(root) == [co[k].pk for k in ("sara", "youssef", "karim", "nora")]
    assert compteur(root) == "4 candidatures affichées sur 4"


def test_ca7_filtres_recherche_et_anomalies(client, direction_user, ref):
    co = ref["co"]
    ids = row_ids(parse(get(client, direction_user, offre_url(ref["o1"], "statut=a_verifier"))))
    assert ids == [co["sara"].pk, co["youssef"].pk]
    assert row_ids(parse(client.get(offre_url(ref["o1"], "q=amrani")))) == [co["sara"].pk]
    assert row_ids(parse(client.get(offre_url(ref["o1"], "anomalies=avec")))) == [co["sara"].pk]


def test_ca8_fragment_htmx(client, direction_user, ref):
    response = get(client, direction_user, offre_url(ref["o1"], "statut=a_verifier"), **HX)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in html
    assert len(row_ids(parse(html))) == 2
    complete = client.get(offre_url(ref["o1"], "statut=a_verifier")).content.decode()
    assert "<html" in complete
    assert row_ids(parse(complete)) == row_ids(parse(html))


def test_ca9_logique_de_filtrage_partagee(client, direction_user, ref):
    params = QueryDict("statut=a_verifier&statut=eligible")
    qs, _flt = filter_candidatures(ref["o1"], params)
    co = ref["co"]
    assert set(qs.values_list("pk", flat=True)) == {co["sara"].pk, co["youssef"].pk, co["karim"].pk}
    vue = row_ids(parse(get(client, direction_user, offre_url(ref["o1"], params.urlencode()))))
    assert vue == list(qs.values_list("pk", flat=True))
    qs_poste, _ = filter_candidatures(ref["p1"], QueryDict(""))
    assert set(qs_poste.values_list("pk", flat=True)) == {c.pk for c in ref["cp"].values()}
    with pytest.raises(TypeError):
        filter_candidatures(ref["c1"], QueryDict(""))


def test_ca10_liens_vers_les_fiches_sans_actions_en_lot(client, direction_user, ref):
    response = get(client, direction_user, offre_url(ref["o1"]))
    root = parse(response)
    sara = ref["co"]["sara"]
    liens = root.find_all(lambda n: n.tag == "a" and n.text() == "EL AMRANI Sara")
    assert liens[0].attrs["href"] == reverse("recruitment:candidature_detail", args=[sara.pk])
    html = response.content.decode()
    assert "data-lot-barre" not in html and "data-lot-case" not in html


def test_ca11_offre_sans_candidature(client, direction_user, ref):
    assert "Aucune candidature pour cette offre." in text(
        get(client, direction_user, offre_url(ref["o3"]))
    )


def test_ca12_offre_inexistante_404_sans_audit(client, direction_user, ref):
    response = get(client, direction_user, reverse("recruitment:offre_detail", args=[999999]))
    assert response.status_code == 404
    assert not JournalAudit.objects.filter(action=AuditAction.VIEW).exists()


def test_ca13_requetes_constantes_page_offre(client, direction_user, ref):
    client.force_login(direction_user)
    o1 = ref["o1"]
    petit = nb_queries(client, offre_url(o1))
    for i in range(116):
        c = candidature(o1, S.A_VERIFIER if i % 3 else S.RECUE)
        add_verif(c, Verdict.ELIGIBLE)
        if i % 4 == 0:
            add_anomalie(c, Anomalie.Gravite.MOYENNE)
    grand = nb_queries(client, offre_url(o1))
    assert petit == grand
    assert nb_queries(client, offre_url(o1, "taille=100")) == grand


def test_ca14_retour_depuis_la_fiche_candidature(client, direction_user, ref):
    sara = ref["co"]["sara"]
    root = parse(
        get(client, direction_user, reverse("recruitment:candidature_detail", args=[sara.pk]))
    )
    (retour,) = by_attr(root, "data-retour-liste")
    assert retour.attrs["href"] == offre_url(ref["o1"])
    assert retour.text() == "Responsable RH fictif"
    (offres,) = by_attr(root, "data-lien-offres")
    assert offres.attrs["href"] == reverse("recruitment:offres_liste")
    o2 = ref["co"]["o2"]
    t = text(client.get(reverse("recruitment:candidature_detail", args=[o2.pk])))
    assert "Entreprise confidentielle" in t


# --------------------------------------------------------------------------------------
# Tableau de bord (CA15 à CA18)
# --------------------------------------------------------------------------------------


def stat_par_libelle(root, libelle):
    cartes = root.find_all(
        lambda n: (
            "stat" in n.attrs.get("class", "").split()
            and any(c.attrs.get("class") == "stat-label" and c.text() == libelle for c in n.iter())
        )
    )
    assert len(cartes) == 1, libelle
    return cartes[0]


def test_ca15_tuiles_explicites(client, direction_user, ref):
    root = parse(get(client, direction_user, reverse("dashboard")))
    concours = stat_par_libelle(root, "Concours publics")
    assert "1" in concours.text() and "1 poste · 1 en cours" in concours.text()
    offres = stat_par_libelle(root, "Offres d’emploi (recrutement privé)")
    assert re.search(r"\b3\b", offres.text()) and "1 ouverte" in offres.text()
    cand = stat_par_libelle(root, "Candidatures reçues")
    assert "8" in cand.text()
    ventilation = by_attr(cand, "data-ventilation")[0].text()
    assert "Concours publics : 3" in ventilation and "Offres d’emploi : 5" in ventilation
    verif = stat_par_libelle(root, "Dossiers à vérifier")
    assert re.search(r"\b3\b", verif.text())
    assert "En attente d’un examen humain" in verif.text()


def test_ca16_tuiles_cliquables(client, direction_user, ref):
    root = parse(get(client, direction_user, reverse("dashboard")))
    assert stat_par_libelle(root, "Concours publics").attrs["href"] == "#concours"
    offres = stat_par_libelle(root, "Offres d’emploi (recrutement privé)")
    assert offres.tag == "a" and offres.attrs["href"] == reverse("recruitment:offres_liste")
    verif = stat_par_libelle(root, "Dossiers à vérifier")
    assert verif.tag == "a" and verif.attrs["href"] == reverse("recruitment:a_verifier")
    cand = stat_par_libelle(root, "Candidatures reçues")
    assert cand.tag == "div"
    hrefs = [a.attrs["href"] for a in cand.find_all(lambda n: n.tag == "a")]
    # Troisième ligne ajoutée par KAN-39 : candidatures spontanées.
    assert hrefs == [
        "#concours",
        reverse("recruitment:offres_liste"),
        reverse("recruitment:spontanees_liste"),
    ]
    assert by_attr(root, "id", "concours")


def test_ca17_sections_offres_et_spontanees(client, direction_user, ref):
    root = parse(get(client, direction_user, reverse("dashboard")))
    (section,) = by_attr(root, "id", "offres")
    assert offre_rows(section) == [ref["o1"].pk, ref["o3"].pk, ref["o2"].pk]
    (tout,) = by_attr(section, "data-toutes-offres")
    assert tout.attrs["href"] == reverse("recruitment:offres_liste")
    (spont,) = by_attr(root, "data-section", "spontanees")
    assert "Candidatures spontanées" in spont.text()
    # Emplacement rempli par KAN-39 : compteurs et lien vers la liste.
    assert "Bientôt disponible" not in spont.text()
    (lien,) = by_attr(spont, "data-toutes-spontanees")
    assert lien.attrs["href"] == reverse("recruitment:spontanees_liste")
    # L'entrée de menu « Offres d'emploi » (Q5).
    (nav,) = by_attr(root, "data-nav-offres")
    assert nav.attrs["href"] == reverse("recruitment:offres_liste")


def test_ca18_tableau_de_bord_requetes_constantes(client, direction_user, ref):
    client.force_login(direction_user)
    petit = nb_queries(client, reverse("dashboard"))
    org = Organisation.objects.create(nom="Société fictive Volume", type=Organisation.Type.PRIVE)
    for i in range(15):
        c = Concours.objects.create(organisation=org, intitule=f"Concours fictif {i}")
        p = PosteConcours.objects.create(concours=c, intitule=f"Poste fictif {i}")
        o = OffreEmploi.objects.create(organisation=org, intitule=f"Offre fictive {i}")
        candidature(p, S.A_VERIFIER)
        candidature(o, S.RECUE)
    assert nb_queries(client, reverse("dashboard")) == petit


# --------------------------------------------------------------------------------------
# Dossiers à vérifier (CA19, CA20)
# --------------------------------------------------------------------------------------


def test_ca19_dossiers_a_verifier_par_cible(client, direction_user, ref):
    response = get(client, direction_user, reverse("recruitment:a_verifier"))
    assert response.status_code == 200
    root = parse(response)
    assert compteur(root) == "3 dossiers à vérifier"
    lignes = by_attr(root, "data-cible")
    assert [n.attrs["data-cible"] for n in lignes] == [
        f"offre-{ref['o1'].pk}",
        f"poste-{ref['p1'].pk}",
    ]
    assert "Offre d’emploi" in lignes[0].text() and "Concours public" in lignes[1].text()
    assert by_attr(lignes[0], "data-col", "nombre")[0].text() == "2"
    lien_o1 = by_attr(lignes[0], "data-lien-dossiers")[0].attrs["href"]
    lien_p1 = by_attr(lignes[1], "data-lien-dossiers")[0].attrs["href"]
    assert lien_o1 == offre_url(ref["o1"], "statut=a_verifier")
    p1 = ref["p1"]
    assert urlparse(lien_p1).path == reverse(
        "recruitment:poste_candidatures", args=[p1.concours_id, p1.pk]
    )
    assert parse_qs(urlparse(lien_p1).query) == {"statut": ["a_verifier"]}
    co = ref["co"]
    assert row_ids(parse(client.get(lien_o1))) == [co["sara"].pk, co["youssef"].pk]
    assert row_ids(parse(client.get(lien_p1))) == [ref["cp"]["a_verifier"].pk]


def test_ca20_aucun_dossier_et_requetes_constantes(client, direction_user, ref):
    client.force_login(direction_user)
    Candidature.objects.filter(statut=S.A_VERIFIER).update(statut=S.RECUE)
    response = client.get(reverse("recruitment:a_verifier"))
    assert "Aucun dossier à vérifier." in text(response)
    # Une offre et un poste : les deux familles sont chargées (au plus une requête chacune).
    candidature(ref["o1"], S.A_VERIFIER)
    candidature(ref["p1"], S.A_VERIFIER)
    un = nb_queries(client, reverse("recruitment:a_verifier"))
    org = ref["o1"].organisation
    for i in range(15):
        o = OffreEmploi.objects.create(organisation=org, intitule=f"Offre fictive {i}")
        c = Concours.objects.create(organisation=org, intitule=f"Concours fictif {i}")
        p = PosteConcours.objects.create(concours=c, intitule=f"Poste fictif {i}")
        candidature(o, S.A_VERIFIER)
        candidature(p, S.A_VERIFIER)
    response = client.get(reverse("recruitment:a_verifier"))
    assert len(by_attr(parse(response), "data-cible")) == 32
    assert nb_queries(client, reverse("recruitment:a_verifier")) == un
    html = response.content.decode()
    for nom in ("AMRANI", "BENNANI"):
        assert nom not in html


# --------------------------------------------------------------------------------------
# Permissions et audit (CA21 à CA24)
# --------------------------------------------------------------------------------------


def nouveaux_ecrans(ref):
    return [
        reverse("recruitment:offres_liste"),
        offre_url(ref["o1"]),
        reverse("recruitment:a_verifier"),
    ]


def test_ca21_admin_autorise(client, admin_user, ref):
    for url in nouveaux_ecrans(ref):
        assert get(client, admin_user, url).status_code == 200


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
@pytest.mark.parametrize("htmx", [False, True], ids=["page", "htmx"])
def test_ca21_roles_vague_2_refuses_et_journalises(client, ref, role, htmx):
    user = make_user(f"{role}@example.com", role)
    for url in nouveaux_ecrans(ref):
        response = get(client, user, url, **(HX if htmx else {}))
        assert response.status_code == 403
        body = response.content.decode()
        assert "AMRANI" not in body and "Responsable RH fictif" not in body
    refus = JournalAudit.objects.filter(action=AuditAction.ACCESS_DENIED)
    assert refus.count() == 3
    assert set(refus.values_list("actor", flat=True)) == {user.pk}
    assert not JournalAudit.objects.filter(action=AuditAction.VIEW).exists()


def test_ca22_non_connecte_et_sans_totp(client, user_without_mfa, ref):
    for url in nouveaux_ecrans(ref):
        response = client.get(url)
        assert response.status_code == 302
        parsed = urlparse(response.url)
        assert parsed.path == reverse("account_login")
        assert parse_qs(parsed.query)["next"] == [url]
    client.force_login(user_without_mfa)
    for url in nouveaux_ecrans(ref):
        response = client.get(url)
        assert response.status_code == 302
        assert urlparse(response.url).path == reverse("mfa_activate_totp")
    assert not JournalAudit.objects.filter(action=AuditAction.VIEW).exists()


def test_ca23_audit_page_offre(client, direction_user, ref):
    o1 = ref["o1"]
    get(client, direction_user, offre_url(o1, "q=amrani&statut=a_verifier"))
    client.get(offre_url(o1, "q=amrani&statut=a_verifier"), **HX)
    vues = JournalAudit.objects.filter(action=AuditAction.VIEW).order_by("id")
    assert vues.count() == 2
    for entree in vues:
        assert entree.actor == direction_user
        assert entree.object_id == str(o1.pk)
        assert entree.content_type.model == "offreemploi"
        assert entree.metadata["recherche"] == "amrani"
        assert entree.metadata["statuts"] == ["a_verifier"]
        assert entree.metadata["page"] == 1
        assert entree.metadata["nb_resultats"] == 1


def test_ca24_audit_liste_offres_et_a_verifier(client, direction_user, ref):
    get(client, direction_user, offres_url(statut="ouverte"))
    client.get(reverse("recruitment:a_verifier"))
    liste, verif = JournalAudit.objects.filter(action=AuditAction.VIEW).order_by("id")
    assert liste.object_repr == "Liste des offres d'emploi" and liste.content_type is None
    assert liste.metadata["statuts"] == ["ouverte"] and liste.metadata["nb_resultats"] == 1
    assert verif.object_repr == "Dossiers à vérifier" and verif.content_type is None
    assert verif.metadata["nb_resultats"] == 3


def test_methode_post_refusee(client, direction_user, ref):
    client.force_login(direction_user)
    for url in nouveaux_ecrans(ref):
        assert client.post(url).status_code == 405


# --------------------------------------------------------------------------------------
# Langue et charte (CA25)
# --------------------------------------------------------------------------------------


def test_ca25_rtl_en_arabe(client, direction_user, ref):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    for url in [reverse("dashboard"), *nouveaux_ecrans(ref)]:
        response = get(client, direction_user, url, HTTP_ACCEPT_LANGUAGE="ar")
        html = parse(response).find_all(lambda n: n.tag == "html")[0]
        assert html.attrs.get("dir") == "rtl"


def test_ca25_pas_de_classes_directionnelles():
    pattern = re.compile(
        r"(?<![\w-])(?:text-(?:left|right)|float-(?:left|right)|-?m[lr]-[\w.]+|p[lr]-[\w.]+"
        r"|-?(?:left|right)-[\w.]+|border-[lr](?:-[\w.]+)?|rounded-[lr](?:-[\w.]+)?)(?![\w-])"
    )
    offenders = []
    for path in NOUVEAUX_GABARITS:
        for cls_attr in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            offenders += [(path.name, m.group(0)) for m in pattern.finditer(cls_attr)]
    assert not offenders, offenders


def test_ca25_libelles_dans_le_catalogue():
    msgids = po_msgids()
    missing = []
    for path in NOUVEAUX_GABARITS:
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


# --------------------------------------------------------------------------------------
# Relecture conformité : confidentialité de l'entreprise (S2), échappement (S3)
# --------------------------------------------------------------------------------------

SECRET = "Groupe Fictif Secret"


def test_s2_nom_d_entreprise_confidentielle_jamais_affiche(client, direction_user, ref):
    org = Organisation.objects.create(nom=SECRET, type=Organisation.Type.PRIVE)
    offre = OffreEmploi.objects.create(
        organisation=org,
        intitule="Analyste fictif",
        entreprise_confidentielle=True,
        statut=OffreEmploi.Statut.OUVERTE,
        date_publication=date(2026, 11, 20),
        fiche_de_poste="Analyser des données fictives.",
    )
    cand = candidature(offre, S.A_VERIFIER, "Secretnom", "Test")
    add_verif(cand, Verdict.A_VERIFIER)
    urls = [
        offres_url(),
        offre_url(offre),
        reverse("recruitment:a_verifier"),
        reverse("dashboard"),
        reverse("recruitment:candidature_detail", args=[cand.pk]),
    ]
    client.force_login(direction_user)
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        html = response.content.decode()
        # Recherche sur le HTML brut et désentité (attributs, <title>, scripts compris).
        for variante in (html, unescape(html)):
            assert SECRET.lower() not in variante.lower(), url
    page_offre = parse(client.get(offre_url(offre)))
    assert by_attr(page_offre, "data-entreprise")[0].text() == "Entreprise confidentielle"


XSS = "<script>alert('xss')</script>"


def test_s3_champs_d_offre_echappes(client, direction_user, ref):
    org = ref["o1"].organisation
    courte = OffreEmploi.objects.create(
        organisation=org,
        intitule=f"Offre {XSS}",
        statut=OffreEmploi.Statut.OUVERTE,
        date_publication=date(2026, 11, 21),
        fiche_de_poste=f"Fiche courte {XSS}",
        competences=[f"Compétence {XSS}"],
        salaire=f"Salaire {XSS}",
    )
    longue = OffreEmploi.objects.create(
        organisation=org,
        intitule=f"Offre longue {XSS}",
        statut=OffreEmploi.Statut.OUVERTE,
        date_publication=date(2026, 11, 22),
        fiche_de_poste=f"Début {XSS} " + "Mission fictive détaillée. " * 80 + XSS,
    )
    client.force_login(direction_user)
    for url in (offre_url(courte), offre_url(longue), offres_url(), reverse("dashboard")):
        html = client.get(url).content.decode()
        assert "<script>alert" not in html, url
    html = client.get(offre_url(courte)).content.decode()
    echappe = "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
    for attendu in (
        f"Offre {echappe}",
        f"Fiche courte {echappe}",
        f"Compétence {echappe}",
        f"Salaire {echappe}",
    ):
        assert attendu in html
    html_longue = client.get(offre_url(longue)).content.decode()
    assert "Lire la fiche complète" in unescape(html_longue)
    assert html_longue.count(echappe) >= 3  # intitulé + début (résumé et fiche complète) + fin
    liste = client.get(offres_url()).content.decode()
    assert f"Offre {echappe}" in liste and f"Offre longue {echappe}" in liste


# --------------------------------------------------------------------------------------
# Export Excel (KAN-37) : poste de concours seulement
# --------------------------------------------------------------------------------------


def test_bouton_export_excel_poste_seulement(client, direction_user, ref):
    client.force_login(direction_user)
    html_offre = client.get(offre_url(ref["o1"])).content.decode()
    assert ref["co"]["sara"].pk in row_ids(parse(html_offre))
    assert "data-export" not in html_offre
    assert "Exporter en Excel" not in unescape(html_offre)
    p1 = ref["p1"]
    liste = reverse("recruitment:poste_candidatures", args=[p1.concours_id, p1.pk])
    root = parse(client.get(liste))
    (bouton,) = by_attr(root, "data-export", "poste")
    assert urlparse(bouton.attrs["href"]).path == reverse(
        "exports:poste_candidatures_xlsx", args=[p1.concours_id, p1.pk]
    )
    assert bouton.text().strip() == "Exporter en Excel (filtres appliqués)"
