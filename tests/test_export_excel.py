"""Tests d'acceptation de KAN-37 — Export Excel des candidatures par poste et par concours.

Les attentes sont fondées sur ``docs/specs/KAN-37-export-excel.md``. Toutes les données sont
fictives (domaines example.org). Aucun appel IA. Le fichier produit est relu avec openpyxl.
"""

import io
import re
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.models import Anomalie, Decision, Verdict, VerificationEligibilite
from apps.exports.candidatures import SheetTitles
from apps.recruitment.models import Candidat, Candidature, Concours, Organisation, PosteConcours

from .conftest import make_user

pytestmark = pytest.mark.django_db

S = Candidature.Statut
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# --------------------------------------------------------------------------------------
# Jeu de données fictif
# --------------------------------------------------------------------------------------


def at(y, m, d, hh=10, mm=0) -> datetime:
    return timezone.make_aware(datetime(y, m, d, hh, mm))


_seq = {"n": 0}


def new_candidat(**kwargs) -> Candidat:
    _seq["n"] += 1
    n = _seq["n"]
    defaults = {
        "nom": f"Fictif{n}",
        "prenom": f"Prenom{n}",
        "email": f"candidat{n}@example.org",
        "telephone": f"06000{n:05d}",
        "cin": f"ZZ{n:06d}",
        "ville": "Villefictive",
        "date_naissance": date(1990, 1, 1),
    }
    return Candidat.objects.create(**{**defaults, **kwargs})


def new_candidature(poste, statut=S.RECUE, candidat=None, when=None, donnees=None):
    return Candidature.objects.create(
        candidat=candidat or new_candidat(),
        poste_concours=poste,
        date_candidature=when or at(2026, 3, 1),
        statut=statut,
        donnees_declarees=donnees if donnees is not None else {},
    )


def add_verif(candidature, resultat, details=None, when=None):
    verif = VerificationEligibilite.objects.create(
        candidature=candidature, resultat=resultat, details=details or [], moteur_version="1.0.0"
    )
    if when is not None:
        VerificationEligibilite.objects.filter(pk=verif.pk).update(created_at=when)
    return verif


def critere(key, verdict, attendu, constate, raison, libelle=""):
    return {
        "critere": key,
        "libelle": libelle or key,
        "verdict": verdict,
        "attendu": attendu,
        "constate": constate,
        "raison": raison,
    }


DONNEES_A = {
    "formation": [
        {
            "intitule": "Diplôme d'ingénieur d'État",
            "niveau": "Bac+5",
            "specialite": "Génie civil",
            "etablissement": "École fictive",
            "annee_obtention": 2014,
        }
    ],
    "experiences": [
        {
            "employeur": "Entreprise fictive",
            "poste": "Ingénieur",
            "debut": "2015-01-01",
            "fin": "2019-12-31",
        },
    ],
}


@pytest.fixture
def dir1(db):
    return make_user("dir1@mafoa.test", Role.DIRECTION)


@pytest.fixture
def admin1(db):
    return make_user("admin1@mafoa.test", Role.ADMIN)


@pytest.fixture
def cons1(db):
    return make_user("cons1@mafoa.test", Role.CONSULTANT)


@pytest.fixture
def ref(admin1):
    org = Organisation.objects.create(nom="SRM fictive", type=Organisation.Type.PUBLIC)
    concours = Concours.objects.create(
        organisation=org,
        intitule="Concours fictif 2026",
        date_limite=date(2026, 3, 31),
        statut=Concours.Statut.EN_EVALUATION,
    )
    p1 = PosteConcours.objects.create(
        concours=concours,
        reference="ING-01",
        intitule="Ingénieur génie civil",
        nombre_postes=2,
        date_limite=date(2026, 2, 28),
    )
    p2 = PosteConcours.objects.create(
        concours=concours, reference="TEC-02", intitule="Technicien", nombre_postes=3
    )
    a = new_candidature(
        p1,
        S.CONVOQUEE,
        new_candidat(
            nom="Amrani",
            prenom="Salma",
            cin="QX123456",
            date_naissance=date(1991, 7, 13),
            email="salma.fictive@example.org",
            telephone="+212600000001",
            ville="Villefictive",
            region="REGIONREPERE",
            sexe="F",
        ),
        at(2026, 2, 1),
        DONNEES_A,
    )
    # Ancienne vérification « à vérifier » puis verdict courant « éligible ».
    add_verif(
        a,
        Verdict.A_VERIFIER,
        [critere("niveau_etudes", "a_verifier", "x", "y", "ANCIEN")],
        when=at(2026, 3, 1),
    )
    add_verif(
        a,
        Verdict.ELIGIBLE,
        [
            critere("niveau_etudes", "eligible", "Bac+5 minimum", "Bac+5", "Niveau Bac+5 ≥ Bac+5."),
            critere("specialite", "eligible", "Génie civil", "Génie civil", "Spécialité acceptée."),
            critere("experience", "eligible", "≥ 3,00 an(s)", "5,00 an(s)", "Conforme."),
        ],
        when=at(2026, 3, 2),
    )
    Decision.objects.create(
        candidature=a,
        auteur=admin1,
        decision=S.CONVOQUEE,
        motif="Profil conforme, convoqué à l'oral fictif.",
    )
    b = new_candidature(p1, S.NON_ELIGIBLE, when=at(2026, 2, 2), donnees={"experiences": []})
    add_verif(
        b,
        Verdict.NON_ELIGIBLE,
        [
            critere(
                "niveau_etudes",
                "non_eligible",
                "Bac+5 minimum",
                "Bac+3",
                "Niveau le plus élevé Bac+3 < Bac+5 exigé.",
            ),
            critere("autres", "a_verifier", "Permis B", "", "Critères non automatisés."),
        ],
    )
    Anomalie.objects.create(
        candidature=b,
        type=Anomalie.Type.INCOHERENCE_DATES,
        gravite=Anomalie.Gravite.HAUTE,
        description="Dates incohérentes fictives",
    )
    Anomalie.objects.create(
        candidature=b,
        type=Anomalie.Type.AUTRE,
        gravite=Anomalie.Gravite.INFO,
        description="ANOMALIE RESOLUE",
        resolue=True,
    )
    d = new_candidature(p1, S.RECUE, when=at(2026, 2, 3))
    e = new_candidature(p2, S.RECUE, when=at(2026, 2, 4))
    return {"concours": concours, "p1": p1, "p2": p2, "a": a, "b": b, "d": d, "e": e}


def poste_url(poste, **params) -> str:
    url = reverse("exports:poste_candidatures_xlsx", args=[poste.concours_id, poste.pk])
    return f"{url}?{urlencode(params, doseq=True)}" if params else url


def concours_url(concours) -> str:
    return reverse("exports:concours_xlsx", args=[concours.pk])


def download(client, user, url):
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200, response.status_code
    assert response["Content-Type"] == XLSX
    return response, load_workbook(io.BytesIO(response.content))


def table(ws) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[0]
    return [dict(zip(headers, r, strict=True)) for r in rows[1:]]


def exports():
    return JournalAudit.objects.filter(action=AuditAction.EXPORT)


# --------------------------------------------------------------------------------------
# Export par poste
# --------------------------------------------------------------------------------------


def test_ca1_export_poste_complet(client, dir1, ref):
    response, wb = download(client, dir1, poste_url(ref["p1"]))
    assert "attachment" in response["Content-Disposition"]
    assert wb.sheetnames == ["ING-01"]
    lignes = table(wb["ING-01"])
    assert [r["N° de dossier"] for r in lignes] == [ref["a"].pk, ref["b"].pk, ref["d"].pk]
    assert ref["e"].pk not in [r["N° de dossier"] for r in lignes]


def test_ca2_filtres_de_la_liste_toutes_pages(client, dir1, ref):
    _, wb = download(client, dir1, poste_url(ref["p1"], verdict="eligible", taille=25, page=2))
    assert [r["N° de dossier"] for r in table(wb.active)] == [ref["a"].pk]
    _, wb = download(client, dir1, poste_url(ref["p1"], anomalies="avec"))
    assert [r["N° de dossier"] for r in table(wb.active)] == [ref["b"].pk]
    _, wb = download(client, dir1, poste_url(ref["p1"], q="amrani"))
    assert [r["N° de dossier"] for r in table(wb.active)] == [ref["a"].pk]
    _, wb = download(client, dir1, poste_url(ref["p1"], tri="-date"))
    assert [r["N° de dossier"] for r in table(wb.active)] == [ref["d"].pk, ref["b"].pk, ref["a"].pk]
    _, wb = download(client, dir1, poste_url(ref["p1"], statut=["recue", "inconnu"]))
    assert [r["N° de dossier"] for r in table(wb.active)] == [ref["d"].pk]


def test_ca3_identite_formation_experiences(client, dir1, ref):
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    a = table(wb.active)[0]
    assert a["Nom"] == "Amrani" and a["Prénom"] == "Salma"
    assert a["CIN"] == "QX123456"
    assert a["Date de naissance"] == datetime(1991, 7, 13)
    assert a["Email"] == "salma.fictive@example.org"
    # Neutralisation : le « + » de tête est préfixé (règle 16).
    # Un numéro « +212… » reste lisible (texte, pas de formule possible dans un .xlsx).
    assert a["Téléphone"] == "+212600000001"
    assert a["Ville"] == "Villefictive"
    assert (
        "Diplôme d'ingénieur d'État — Bac+5 — Génie civil — École fictive — 2014"
        in (a["Formations déclarées"])
    )
    assert (
        a["Expériences déclarées"] == "Ingénieur chez Entreprise fictive (01/01/2015 → 31/12/2019)"
    )
    assert Decimal(str(a["Expérience calculée (années)"])) == Decimal("5.00")
    assert isinstance(a["Date de candidature"], datetime)
    b, d = table(wb.active)[1:]
    assert b["Expériences déclarées"] == "Aucune expérience déclarée"
    assert b["Expérience calculée (années)"] == 0
    assert d["Expériences déclarées"] == "Non renseignées"
    assert d["Expérience calculée (années)"] is None
    assert d["Formations déclarées"] == "Aucune formation déclarée"


def test_ca4_decision_humaine_en_vigueur(client, dir1, admin1, ref):
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    a, b, _d = table(wb.active)
    assert a["Statut actuel"] == "Convoquée"
    assert a["Décision humaine"] == "Convoquée"
    assert a["Motif de la décision"] == "Profil conforme, convoqué à l'oral fictif."
    assert a["Auteur de la décision"] == admin1.email
    assert isinstance(a["Date de la décision"], datetime)
    assert b["Décision humaine"] == "Aucune décision humaine"
    assert b["Motif de la décision"] is None and b["Auteur de la décision"] is None


def test_ca5_verdict_et_raisons_de_la_derniere_verification(client, dir1, ref):
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    a, b, d = table(wb.active)
    assert a["Verdict du moteur (pas une décision)"] == "Éligible"
    assert a["Niveau d'études — raison"] == "Niveau Bac+5 ≥ Bac+5."
    assert a["Version du moteur"] == "1.0.0"
    assert b["Verdict du moteur (pas une décision)"] == "Non éligible"
    assert b["Niveau d'études — attendu"] == "Bac+5 minimum"
    assert b["Niveau d'études — constaté"] == "Bac+3"
    assert b["Niveau d'études — verdict"] == "Non éligible"
    assert b["Niveau d'études — raison"] == "Niveau le plus élevé Bac+3 < Bac+5 exigé."
    assert b["Spécialité — raison"] is None
    assert b["Autres critères (vérification humaine)"] == "Permis B\nCritères non automatisés."
    assert d["Verdict du moteur (pas une décision)"] == "Non évaluée"
    assert d["Niveau d'études — raison"] is None and d["Date du verdict"] is None
    valeurs = [v for r in wb.active.iter_rows(values_only=True) for v in r]
    assert "ANCIEN" not in valeurs


def test_ca6_anomalies_non_resolues_seulement(client, dir1, ref):
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    b = table(wb.active)[1]
    assert b["Anomalies non résolues (nombre)"] == 1
    assert (
        b["Anomalies non résolues"] == "[Haute] Incohérence de dates : Dates incohérentes fictives"
    )
    assert table(wb.active)[0]["Anomalies non résolues (nombre)"] == 0


def test_ca7_aucune_donnee_sensible(client, dir1, ref):
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    headers = [h.lower() for h in next(wb.active.iter_rows(values_only=True))]
    for mot in ("sexe", "région", "region", "photo", "familiale", "âge"):
        assert not any(mot in h for h in headers), mot
    valeurs = {v for r in wb.active.iter_rows(values_only=True) for v in r}
    assert "REGIONREPERE" not in valeurs
    assert "F" not in valeurs and "Femme" not in valeurs


def test_ca8_boutons(client, dir1, ref):
    client.force_login(dir1)
    url = reverse("recruitment:poste_candidatures", args=[ref["concours"].pk, ref["p1"].pk])
    html = client.get(f"{url}?verdict=eligible&inconnu=1").content.decode()
    match = re.search(
        r'<a class="btn-secondary btn-sm" href="([^"]+)"[^>]*data-export="poste"', html
    )
    assert match
    assert match.group(1) == poste_url(ref["p1"]) + "?verdict=eligible"
    assert "Exporter en Excel (filtres appliqués)" in html
    html = client.get(
        reverse("recruitment:concours_detail", args=[ref["concours"].pk])
    ).content.decode()
    assert re.search(
        rf'<a class="btn-secondary btn-sm" href="{re.escape(concours_url(ref["concours"]))}"', html
    )
    assert "Exporter le concours en Excel" in html


# --------------------------------------------------------------------------------------
# Export par concours
# --------------------------------------------------------------------------------------


def test_ca9_onglets_du_concours(client, admin1, ref):
    _, wb = download(client, admin1, concours_url(ref["concours"]))
    assert wb.sheetnames == ["Synthèse", "ING-01", "TEC-02"]
    assert [r["N° de dossier"] for r in table(wb["ING-01"])] == [
        ref["a"].pk,
        ref["b"].pk,
        ref["d"].pk,
    ]
    assert [r["N° de dossier"] for r in table(wb["TEC-02"])] == [ref["e"].pk]
    assert next(wb["ING-01"].iter_rows(values_only=True)) == next(
        wb["TEC-02"].iter_rows(values_only=True)
    )
    _, wb_poste = download(client, admin1, poste_url(ref["p1"]))
    assert table(wb["ING-01"])[0].keys() == table(wb_poste.active)[0].keys()


def test_ca10_synthese(client, admin1, ref):
    _, wb = download(client, admin1, concours_url(ref["concours"]))
    lignes = table(wb["Synthèse"])
    p1, p2, total = lignes
    assert p1["Référence"] == "ING-01" and p1["Candidatures"] == 3
    assert p1["Éligibles (moteur)"] == 1 and p1["Non éligibles (moteur)"] == 1
    assert p1["À vérifier (moteur)"] == 0 and p1["Non évaluées"] == 1
    assert p1["Statut : Convoquée"] == 1 and p1["Statut : Reçue"] == 1
    assert p1["Date limite"] == datetime(2026, 2, 28)
    assert p2["Date limite"] == datetime(2026, 3, 31)  # date du concours
    assert total["Référence"] == "Total"
    assert total["Candidatures"] == 4 and total["Postes à pourvoir"] == 5
    assert total["Statut : Reçue"] == 2


def test_ca11_noms_onglets_valides_et_uniques(client, admin1, ref):
    longue = "REF/[2026]:*?\\ référence longue de poste fictive"
    assert 31 < len(longue) <= 50
    for _i in range(2):
        PosteConcours.objects.create(concours=ref["concours"], reference=longue, intitule="X")
    PosteConcours.objects.create(concours=ref["concours"], reference="", intitule="synthèse")
    _, wb = download(client, admin1, concours_url(ref["concours"]))
    names = wb.sheetnames
    assert len(names) == len({n.casefold() for n in names}) == 6
    for n in names:
        assert len(n) <= 31 and not re.search(r"[\[\]:*?/\\]", n)
    titles = SheetTitles(reserved=["Synthèse"])
    assert titles("", "Poste 9") == "Poste 9"
    assert titles("Synthèse", "x") == "Synthèse (2)"


def test_ca12_poste_sans_candidature(client, admin1, ref):
    PosteConcours.objects.create(concours=ref["concours"], reference="VIDE-03", intitule="Vide")
    _, wb = download(client, admin1, concours_url(ref["concours"]))
    assert len(list(wb["VIDE-03"].iter_rows(values_only=True))) == 1


def test_concours_sans_poste(client, admin1):
    org = Organisation.objects.create(nom="Org fictive", type=Organisation.Type.PUBLIC)
    concours = Concours.objects.create(organisation=org, intitule="Vide")
    _, wb = download(client, admin1, concours_url(concours))
    assert wb.sheetnames == ["Synthèse"]
    assert table(wb["Synthèse"])[0]["Candidatures"] == 0


# --------------------------------------------------------------------------------------
# Sécurité, audit, volumes
# --------------------------------------------------------------------------------------


def test_ca13_refus_consultant_et_anonyme(client, cons1, ref):
    client.force_login(cons1)
    for url in (poste_url(ref["p1"]), concours_url(ref["concours"])):
        response = client.get(url)
        assert response.status_code == 403
        assert response.get("Content-Type") != XLSX
    assert JournalAudit.objects.filter(action=AuditAction.ACCESS_DENIED).count() == 2
    assert not exports().exists()
    client.logout()
    response = client.get(poste_url(ref["p1"]))
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]


def test_ca14_journal_export(client, dir1, ref):
    download(client, dir1, poste_url(ref["p1"], verdict="eligible", q="amrani"))
    download(client, dir1, concours_url(ref["concours"]))
    poste_entry, concours_entry = exports().order_by("id")
    assert poste_entry.actor == dir1
    assert poste_entry.object_id == str(ref["p1"].pk)
    meta = poste_entry.metadata
    assert meta["vue"] == "export_candidatures_poste" and meta["format"] == "xlsx"
    assert meta["nb_lignes"] == 1
    assert meta["filtres"]["verdicts"] == ["eligible"]
    assert meta["filtres"]["recherche_appliquee"] is True
    assert concours_entry.object_id == str(ref["concours"].pk)
    assert concours_entry.metadata["vue"] == "export_candidatures_concours"
    assert concours_entry.metadata["nb_lignes"] == 4
    assert concours_entry.metadata["nb_postes"] == 2
    for entry in (poste_entry, concours_entry):
        brut = f"{entry.metadata} {entry.object_repr} {entry.before} {entry.after}".lower()
        assert "amrani" not in brut and "salma" not in brut and "qx123456" not in brut
        assert str(ref["a"].pk) not in str(entry.metadata.get("ids", ""))
        assert "candidature_id" not in entry.metadata


def test_ca15_neutralisation_formules_et_controle(client, dir1, ref, admin1):
    c = new_candidature(
        ref["p1"],
        S.RECUE,
        new_candidat(nom='=HYPERLINK("x")', prenom="@SUM(A1)"),
        when=at(2026, 2, 5),
    )
    Decision.objects.create(
        candidature=c, auteur=admin1, decision=S.REJETEE, motif="-Motif\x07 avec contrôle fictif"
    )
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    ligne = table(wb.active)[-1]
    assert ligne["Nom"] == '\'=HYPERLINK("x")'
    assert ligne["Prénom"] == "'@SUM(A1)"
    assert ligne["Motif de la décision"] == "'-Motif� avec contrôle fictif"
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type != "f"


@override_settings(EXPORT_CANDIDATURES_MAX_ROWS=2)
def test_ca16_plafond(client, dir1, ref):
    client.force_login(dir1)
    response = client.get(poste_url(ref["p1"], verdict=["eligible", "non_eligible", "non_evaluee"]))
    assert response.status_code == 302
    liste = reverse("recruitment:poste_candidatures", args=[ref["concours"].pk, ref["p1"].pk])
    assert response["Location"].startswith(liste + "?verdict=")
    page = client.get(response["Location"]).content.decode()
    assert "L’export est limité à 2 candidatures (3 correspondent)" in page
    response = client.get(concours_url(ref["concours"]))
    assert response.status_code == 302
    assert response["Location"] == reverse("recruitment:concours_detail", args=[ref["concours"].pk])
    assert not exports().exists()
    # À la limite exacte, l'export est produit.
    assert client.get(poste_url(ref["p1"], verdict=["eligible", "non_eligible"])).status_code == 200


def test_ca17_nom_de_fichier(client, dir1, ref):
    response, _wb = download(client, dir1, poste_url(ref["p1"], q="amrani"))
    disposition = response["Content-Disposition"]
    assert re.fullmatch(
        rf'attachment; filename="candidatures-poste-{ref["p1"].pk}-\d{{8}}-\d{{4}}\.xlsx"',
        disposition,
    )
    assert "no-store" in response["Cache-Control"]
    response, _wb = download(client, dir1, concours_url(ref["concours"]))
    assert re.fullmatch(
        rf'attachment; filename="candidatures-concours-{ref["concours"].pk}-\d{{8}}-\d{{4}}\.xlsx"',
        response["Content-Disposition"],
    )


def _count_queries(client, url) -> int:
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return len(ctx.captured_queries)


def _peupler(poste, n, admin1):
    for i in range(n):
        c = new_candidature(poste, S.RECUE, when=at(2026, 2, 10 + i % 15), donnees=DONNEES_A)
        add_verif(c, Verdict.A_VERIFIER, [critere("niveau_etudes", "a_verifier", "a", "b", "c")])
        Decision.objects.create(
            candidature=c, auteur=admin1, decision=S.ELIGIBLE, motif="Motif fictif"
        )
        Anomalie.objects.create(
            candidature=c, type=Anomalie.Type.AUTRE, gravite=Anomalie.Gravite.INFO, description="x"
        )


def test_ca18_requetes_constantes(client, dir1, admin1, ref):
    client.force_login(dir1)
    p3 = PosteConcours.objects.create(concours=ref["concours"], reference="P3", intitule="P3")
    _peupler(p3, 2, admin1)
    poste_petit = _count_queries(client, poste_url(p3))
    concours_petit = _count_queries(client, concours_url(ref["concours"]))
    _peupler(p3, 10, admin1)
    PosteConcours.objects.create(concours=ref["concours"], reference="P4", intitule="P4")
    assert _count_queries(client, poste_url(p3)) == poste_petit
    assert _count_queries(client, concours_url(ref["concours"])) == concours_petit


def test_ca19_methodes_et_404(client, dir1, ref):
    client.force_login(dir1)
    assert client.post(poste_url(ref["p1"])).status_code == 405
    assert client.post(concours_url(ref["concours"])).status_code == 405
    org = Organisation.objects.create(nom="Autre", type=Organisation.Type.PUBLIC)
    autre = Concours.objects.create(organisation=org, intitule="Autre")
    url = reverse("exports:poste_candidatures_xlsx", args=[autre.pk, ref["p1"].pk])
    assert client.get(url).status_code == 404
    assert client.get(reverse("exports:concours_xlsx", args=[999999])).status_code == 404
    assert not exports().exists()


def test_ca20_mise_en_forme(client, dir1, ref):
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    ws = wb.active
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref.startswith("A1:") and ws.auto_filter.ref.endswith("4")
    headers = [c.value for c in ws[1]]
    assert all(c.font.b for c in ws[1])
    statut_col = headers.index("Statut actuel") + 1
    statut_b = ws.cell(row=3, column=statut_col)
    assert statut_b.value == "Non éligible"
    assert statut_b.fill.fgColor.rgb.endswith("E7E5E4")
    statut_a = ws.cell(row=2, column=statut_col)
    assert statut_a.fill.fgColor.rgb.endswith("D1FAE5")
    for row in ws.iter_rows():
        for cell in row:
            rgb = cell.fill.fgColor.rgb if cell.fill and cell.fill.fgColor else ""
            assert not str(rgb).upper().endswith("D01C2B")


def test_donnees_declarees_mal_formees(client, dir1, ref):
    new_candidature(
        ref["p1"],
        when=at(2026, 2, 6),
        donnees={"formation": "texte libre", "experiences": ["pas un dict", {"debut": "n/a"}]},
    )
    c = new_candidature(ref["p1"], when=at(2026, 2, 7), donnees={"formation": [1, None]})
    add_verif(c, Verdict.A_VERIFIER, details={"mal": "formé"})
    _, wb = download(client, dir1, poste_url(ref["p1"]))
    lignes = table(wb.active)
    assert lignes[-2]["Formations déclarées"] == "Aucune formation déclarée"
    assert lignes[-1]["Verdict du moteur (pas une décision)"] == "À vérifier"
    assert lignes[-1]["Niveau d'études — raison"] is None


def test_telephone_non_numerique_reste_neutralise():
    """Une valeur de téléphone qui n'est pas un numéro est neutralisée comme les autres."""
    from openpyxl import Workbook

    from apps.exports.candidatures import PHONE, Colonne, _cell

    ws = Workbook(write_only=True).create_sheet()
    cell = _cell(ws, "=HYPERLINK(1)", Colonne("Téléphone", 16, PHONE))
    assert cell.value == "'=HYPERLINK(1)"


def test_telephone_texte_et_saut_de_ligne_refuse():
    """Relecture : un numéro est une cellule texte ; un « \\n » final n'échappe pas au nettoyage."""
    from openpyxl import Workbook

    from apps.exports.candidatures import PHONE, Colonne, _cell

    ws = Workbook(write_only=True).create_sheet()
    col = Colonne("Téléphone", 16, PHONE)
    ok = _cell(ws, "+212600000001", col)
    assert ok.value == "+212600000001" and ok.data_type == "s"
    assert _cell(ws, "+212\n", col).value != "+212\n"
