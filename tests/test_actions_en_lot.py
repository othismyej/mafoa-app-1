"""Tests d'acceptation de KAN-15 — Actions en lot sur la liste des candidatures.

Les attentes sont fondées sur la spécification ``docs/specs/KAN-15-actions-en-lot.md`` (jeu de
référence : concours C1 « En évaluation », poste P1 avec les candidatures A à J, poste P2 avec
DUPONT Paul, concours C2 « Terminé » avec P3). Toutes les données sont fictives (domaines
example.org, numéros +212600000xxx, CIN « ZZ… »). Aucun appel IA.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, datetime
from html import unescape
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation import decisions as rules
from apps.evaluation import lots
from apps.evaluation import services as evaluation_services
from apps.evaluation.decisions import DecisionError
from apps.evaluation.models import Anomalie, Decision, VerificationEligibilite
from apps.evaluation.services import record_decision
from apps.recruitment.models import Candidat, Candidature, Concours, Organisation, PosteConcours

from .conftest import make_user
from .test_candidature_detail import parse

pytestmark = pytest.mark.django_db

S = Candidature.Statut
HX = {"HTTP_HX_REQUEST": "true"}
MOTIF = "Convocation à l'épreuve orale du 12/03/2026"

PROFIL_BAC5 = {
    "formation": [
        {"intitule": "Master fictif", "niveau": "Bac+5", "specialite": "Hydraulique"},
    ],
    "experiences": [
        {"debut": "2015-01-01", "fin": "2024-12-31", "employeur": "Société fictive", "poste": "Ing"}
    ],
}
PROFIL_BAC2 = {
    "formation": [{"intitule": "DUT fictif", "niveau": "Bac+2", "specialite": "Hydraulique"}],
    "experiences": PROFIL_BAC5["experiences"],
}

# Réf. : (nom, prénom, statut, dernier verdict, décision humaine, anomalie non résolue)
REFERENCE = {
    "A": ("EL AMRANI", "Sara", S.A_VERIFIER, "a_verifier", None, "haute"),
    "B": ("BENNANI", "Youssef", S.ELIGIBLE, "eligible", None, None),
    "C": ("ÉTTAHIRI", "Imane", S.NON_ELIGIBLE, "non_eligible", None, "moyenne"),
    "D": ("ALAOUI", "Karim", S.CONVOQUEE, "eligible", S.CONVOQUEE, None),
    "E": ("ZIANI", "Nora", S.RECUE, None, None, None),
    "F": ("EL AMRANI", "Omar", S.A_VERIFIER, "a_verifier", None, "moyenne"),
    "G": ("IDRISSI", "Hamza", S.ELIGIBLE, "eligible", None, "haute"),
    "H": ("TAZI", "Leila", S.PRESELECTIONNEE, "eligible", S.PRESELECTIONNEE, None),
    "I": ("FASSI", "Rania", S.REJETEE, "non_eligible", S.REJETEE, None),
    "J": ("CHRAIBI", "Mehdi", S.ELIGIBLE, "non_eligible", S.ELIGIBLE, None),
}

_seq = {"n": 0}


def _candidat(nom: str, prenom: str) -> Candidat:
    _seq["n"] += 1
    n = _seq["n"]
    return Candidat.objects.create(
        nom=nom,
        prenom=prenom,
        email=f"lot{n}@example.org",
        telephone=f"+212600000{n % 1000:03d}",
        cin=f"ZZ{n:06d}",
        date_naissance=date(1990, 1, 1 + n % 27),
        ville="Villefictive",
        region="Région fictive",
        sexe="F",
    )


def new_candidature(poste, nom="FICTIF", prenom="Test", statut=S.ELIGIBLE, **kw) -> Candidature:
    data = {
        "candidat": _candidat(nom, prenom),
        "poste_concours": poste,
        "date_candidature": timezone.make_aware(datetime(2026, 3, 15, 10, 30)),
        "statut": statut,
        "donnees_declarees": PROFIL_BAC5,
    }
    data.update(kw)
    return Candidature.objects.create(**data)


def make_poste(concours, reference, nombre_postes=2) -> PosteConcours:
    return PosteConcours.objects.create(
        concours=concours,
        intitule=f"Ingénieur fictif {reference}",
        reference=reference,
        nombre_postes=nombre_postes,
        niveau_etudes_min=5,
        experience_min_annees=2,
    )


@pytest.fixture
def ref(db):
    auteur = make_user("historique@example.org", Role.DIRECTION)
    org = Organisation.objects.create(nom="Agence Fictive Draa", type=Organisation.Type.PUBLIC)
    c1 = Concours.objects.create(
        organisation=org, intitule="Concours fictif C1", statut=Concours.Statut.EN_EVALUATION
    )
    p1 = make_poste(c1, "P-01")
    p2 = make_poste(c1, "P-02")
    c2 = Concours.objects.create(
        organisation=org, intitule="Concours fictif C2", statut=Concours.Statut.TERMINE
    )
    p3 = make_poste(c2, "P-03")
    cands = {}
    for key, (nom, prenom, statut, verdict, decision, anomalie) in REFERENCE.items():
        c = new_candidature(
            p1, nom, prenom, statut, donnees_declarees=PROFIL_BAC2 if key == "J" else PROFIL_BAC5
        )
        if verdict:
            VerificationEligibilite.objects.create(
                candidature=c, resultat=verdict, moteur_version="test"
            )
        if decision:
            Decision.objects.create(
                candidature=c,
                auteur=auteur,
                decision=decision,
                motif="Décision antérieure fictive.",
            )
        if anomalie:
            Anomalie.objects.create(
                candidature=c, type=Anomalie.Type.AUTRE, gravite=anomalie, description="Fictive."
            )
        cands[key] = c
    dupont = new_candidature(p2, "DUPONT", "Paul", S.ELIGIBLE)
    p3_cands = [new_candidature(p3, f"TERMINE{i}", "Test", S.ELIGIBLE) for i in range(2)]
    return SimpleNamespace(
        c1=c1, p1=p1, p2=p2, c2=c2, p3=p3, dupont=dupont, p3_cands=p3_cands, auteur=auteur, **cands
    )


def url_recap(poste) -> str:
    return reverse("recruitment:lot_recapitulatif", args=[poste.concours_id, poste.pk])


def url_exec(poste) -> str:
    return reverse("recruitment:lot_executer", args=[poste.concours_id, poste.pk])


def url_liste(poste) -> str:
    return reverse("recruitment:poste_candidatures", args=[poste.concours_id, poste.pk])


def recap(client, user, poste, action, cands=(), *, mode="ids", params=None, htmx=False, ids=None):
    if user is not None:
        client.force_login(user)
    data = {"action": action, "mode": mode, **{f"lot_{k}": v for k, v in (params or {}).items()}}
    data["ids"] = ids if ids is not None else [str(c.pk) for c in cands]
    return client.post(url_recap(poste), data, **(HX if htmx else {}))


def jeton(response) -> str:
    match = re.search(r'name="jeton" value="([^"]+)"', response.content.decode())
    assert match, "jeton absent du récapitulatif"
    return unescape(match.group(1))


def executer(client, poste, token, action, *, motif=MOTIF, confirmer=True, choix=None, htmx=False):
    data = {"jeton": token, "action": action}
    if motif is not None:
        data["motif"] = motif
    if confirmer:
        data["confirmer"] = "1"
    for type_, valeur in (choix or {}).items():
        data[f"choix_{type_}"] = valeur
    return client.post(url_exec(poste), data, **(HX if htmx else {}))


def fa(node, tag=None, attrs=None):
    """Recherche dans l'arbre de ``parse`` : balise et attributs (``True`` = présent)."""

    def ok(n):
        if tag and n.tag != tag:
            return False
        for k, v in (attrs or {}).items():
            if k not in n.attrs or (v is not True and n.attrs.get(k) != v):
                return False
        return True

    return node.find_all(ok)


def texte(response) -> str:
    html = response.content.decode()
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(html)).strip()


def exact(t: str, phrase: str) -> bool:
    """La phrase figure telle quelle, sans lettre accolée (« 0 non enregistrée » ≠ « …ées »)."""
    return re.search(r"(?<![\w])" + re.escape(phrase) + r"(?![\w])", t) is not None


def groupe(response, attribut: str, valeur: str) -> set[int]:
    """Identifiants des candidatures listées dans un bloc ``data-<attribut>="<valeur>"``."""
    doc = parse(response.content.decode())
    blocs = fa(doc, attrs={f"data-{attribut}": valeur})
    ids = set()
    for bloc in blocs:
        for lien in fa(bloc, attrs={"data-candidature": True}):
            ids.add(int(lien.attrs["data-candidature"]))
    return ids


def groupes_par_libelle(response) -> dict[str, set[int]]:
    doc = parse(response.content.decode())
    result = {}
    for bloc in fa(doc, attrs={"data-lot-groupe": True}):
        libelle = fa(bloc, attrs={"data-lot-groupe-libelle": True})[0].text().strip()
        result[libelle] = {
            int(a.attrs["data-candidature"]) for a in fa(bloc, attrs={"data-candidature": True})
        }
    return result


def statut(c) -> str:
    return Candidature.objects.get(pk=c.pk).statut


def entrees_lot(lot_id=None):
    qs = JournalAudit.objects.filter(action=AuditAction.LOT).order_by("id")
    if lot_id:
        qs = qs.filter(metadata__lot_id=str(lot_id))
    return list(qs)


def refus(code):
    return JournalAudit.objects.filter(action=AuditAction.UPDATE, metadata__refus=code)


def ids(*cands) -> set[int]:
    return {c.pk for c in cands}


# --------------------------------------------------------------------------------------
# Règles du service (indépendantes de l'interface)
# --------------------------------------------------------------------------------------


def test_table_du_lot_est_un_sous_ensemble_de_kan13_sans_revision():
    """Règles 2 et 4 — chaque transition en lot existe dans KAN-13 et n'est pas une révision."""
    assert set(rules.DECISIONS_LOT) == {S.ELIGIBLE, S.PRESELECTIONNEE, S.CONVOQUEE}
    for decision, statuts in rules.TRANSITIONS_LOT.items():
        for s in statuts:
            assert decision in rules.TRANSITIONS[s]
            assert (s, decision) not in rules.REVISIONS


@pytest.mark.parametrize("decision", [S.NON_ELIGIBLE, S.REJETEE, S.RETENUE])
def test_service_refuse_les_decisions_interdites_en_lot(ref, direction_user, decision):
    """Règle 3 — refus appliqué dans ``record_decision`` (pas seulement masqué)."""
    c = ref.D if decision == S.RETENUE else ref.B
    with pytest.raises(DecisionError) as exc:
        record_decision(c, direction_user, decision, MOTIF, lot_id=uuid.uuid4())
    assert exc.value.code == "valeur_interdite_en_lot"
    assert Decision.objects.filter(candidature=c, lot_id__isnull=False).count() == 0


def test_service_refuse_une_revision_en_lot(ref, direction_user):
    """Règle 4 — présélectionnée → éligible refusée en lot même avec confirmation."""
    with pytest.raises(DecisionError) as exc:
        record_decision(
            ref.H,
            direction_user,
            S.ELIGIBLE,
            MOTIF,
            lot_id=uuid.uuid4(),
            confirmer_revision=True,
            confirmer_avertissements=True,
        )
    assert exc.value.code == "transition_interdite_en_lot"
    assert statut(ref.H) == S.PRESELECTIONNEE


# --------------------------------------------------------------------------------------
# Accès et sécurité (CA1 à CA7)
# --------------------------------------------------------------------------------------


def test_ca1_cases_et_barre_dans_la_liste(client, direction_user, ref):
    client.force_login(direction_user)
    response = client.get(url_liste(ref.p1))
    doc = parse(response.content.decode())
    for c in [ref.A, ref.B, ref.J]:
        case = fa(doc, attrs={"name": "ids", "value": str(c.pk)})[0]
        assert case.attrs["type"] == "checkbox"
        assert case.attrs["form"] == "lot"
        assert case.attrs["aria-label"] == f"Sélectionner {c.candidat}"
    tout = fa(doc, attrs={"data-lot-tout": True})[0]
    assert tout.attrs["aria-label"] == "Sélectionner toutes les candidatures de la page"
    form = fa(doc, attrs={"id": "lot"})[0]
    assert form.tag == "form" and form.attrs["method"] == "post"
    assert fa(form, attrs={"name": "csrfmiddlewaretoken"})
    assert form.attrs["action"] == url_recap(ref.p1)


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
@pytest.mark.parametrize("htmx", [False, True])
@pytest.mark.parametrize("etape", ["recap", "exec"])
def test_ca3_consultant_client_refuses_et_journalises(client, ref, role, htmx, etape):
    user = make_user(f"{role}-{etape}-{int(htmx)}@example.org", role)
    client.force_login(user)
    url = url_recap(ref.p1) if etape == "recap" else url_exec(ref.p1)
    data = {"action": "convoquee", "mode": "ids", "ids": [ref.B.pk], "jeton": "x", "motif": MOTIF}
    response = client.post(url, data, **(HX if htmx else {}))
    assert response.status_code == 403
    assert not Decision.objects.filter(lot_id__isnull=False).exists()
    assert statut(ref.B) == S.ELIGIBLE
    entree = refus("droits").get()
    assert entree.metadata["poste_id"] == ref.p1.pk
    assert entree.actor == user


def test_ca3_relance_refusee_ne_cree_aucune_verification(client, ref):
    user = make_user("consultant-relance@example.org", Role.CONSULTANT)
    avant = VerificationEligibilite.objects.count()
    client.force_login(user)
    client.post(url_recap(ref.p1), {"action": "relance_eligibilite", "mode": "filtre"})
    assert VerificationEligibilite.objects.count() == avant


def test_ca4_non_connecte_redirige(client, ref):
    for url in (url_recap(ref.p1), url_exec(ref.p1)):
        response = client.post(url, {"action": "convoquee", "mode": "ids", "ids": [ref.B.pk]})
        assert response.status_code == 302
        assert reverse("account_login") in response.url
    assert statut(ref.B) == S.ELIGIBLE


def test_ca4_sans_totp_redirige_vers_activation(client, user_without_mfa, ref):
    response = recap(client, user_without_mfa, ref.p1, "convoquee", [ref.B])
    assert response.status_code == 302
    assert response.url == reverse("mfa_activate_totp")


def test_ca5_csrf_obligatoire(direction_user, ref):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(direction_user)
    for url in (url_recap(ref.p1), url_exec(ref.p1)):
        response = csrf_client.post(url, {"action": "convoquee", "mode": "ids", "ids": [ref.B.pk]})
        assert response.status_code == 403
    assert not entrees_lot()


def test_ca5_csrf_de_la_liste_accepte(direction_user, ref):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(direction_user)
    csrf_client.get(url_liste(ref.p1))
    response = csrf_client.post(
        url_recap(ref.p1),
        {
            "action": "convoquee",
            "mode": "ids",
            "ids": [ref.B.pk],
            "csrfmiddlewaretoken": csrf_client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 200


def test_ca6_get_refuse(client, direction_user, ref):
    client.force_login(direction_user)
    assert client.get(url_recap(ref.p1)).status_code == 405
    assert client.get(url_exec(ref.p1)).status_code == 405


def test_ca7_poste_hors_concours_ou_inexistant(client, direction_user, ref):
    client.force_login(direction_user)
    for args in ([ref.c2.pk, ref.p2.pk], [ref.c1.pk, 999999]):
        for name in ("recruitment:lot_recapitulatif", "recruitment:lot_executer"):
            response = client.post(
                reverse(name, args=args), {"action": "convoquee", "mode": "ids", "ids": [ref.B.pk]}
            )
            assert response.status_code == 404
    assert not entrees_lot()


# --------------------------------------------------------------------------------------
# Sélection (CA8 à CA15)
# --------------------------------------------------------------------------------------


def test_ca8_mode_ids(client, direction_user, ref):
    response = recap(client, direction_user, ref.p1, "convoquee", [ref.B, ref.G])
    assert response.status_code == 200
    assert exact(texte(response), "2 candidatures sélectionnées")


def test_ca9_identifiant_hors_poste_ou_inexistant(client, direction_user, ref):
    for autre in ([ref.dupont.pk], [987654], ["abc"]):
        response = recap(client, direction_user, ref.p1, "convoquee", ids=[ref.B.pk, *autre])
        assert response.status_code == 400
        assert 'name="jeton"' not in response.content.decode()
    assert refus("ids_hors_poste").count() == 3
    assert not JournalAudit.objects.filter(metadata__vue="lot_recapitulatif").exists()


def test_ca10_doublons_dedoublonnes(client, direction_user, ref):
    response = recap(client, direction_user, ref.p1, "convoquee", ids=[ref.B.pk, ref.B.pk])
    assert exact(texte(response), "1 candidature sélectionnée")


def test_ca11_mode_filtre_meme_ensemble_que_la_liste(client, direction_user, ref):
    from apps.recruitment.filters import filter_candidatures

    response = recap(
        client, direction_user, ref.p1, "convoquee", mode="filtre", params={"statut": "eligible"}
    )
    payload = lots.lire_jeton(jeton(response), user=direction_user, poste=ref.p1)
    selection = set(lots.ids_selection(payload))
    attendu, _ = filter_candidatures(ref.p1, CandidatureFilterParams(statut="eligible"))
    assert selection == ids(ref.B, ref.G, ref.J) == {c.pk for c in attendu}


def CandidatureFilterParams(**params):
    from django.http import QueryDict

    q = QueryDict(mutable=True)
    for k, v in params.items():
        q[k] = v
    return q


def test_ca12_toutes_pages_confondues(client, direction_user, ref):
    poste = make_poste(ref.c1, "P-120")
    for i in range(120):
        new_candidature(poste, f"VOLUME{i:03d}", "Test")
    response = recap(
        client,
        direction_user,
        poste,
        "convoquee",
        mode="filtre",
        params={"statut": "eligible", "taille": "50"},
    )
    assert exact(texte(response), "120 candidatures sélectionnées")
    client.force_login(direction_user)
    liste = client.get(url_liste(poste), {"statut": "eligible"})
    assert exact(texte(liste), "Appliquer aux 120 résultats du filtre")


def test_ca13_selection_vide(client, direction_user, ref):
    for response in (
        recap(client, direction_user, ref.p1, "convoquee", []),
        recap(
            client, direction_user, ref.p1, "convoquee", mode="filtre", params={"q": "zzzinconnu"}
        ),
    ):
        assert "Sélectionnez au moins une candidature." in texte(response)
        assert 'name="jeton"' not in response.content.decode()
    assert not entrees_lot()
    assert not JournalAudit.objects.filter(metadata__vue="lot_recapitulatif").exists()


def test_ca14_plafond_sans_troncature(client, direction_user, ref, settings):
    settings.LOT_TAILLE_MAX = 5
    poste = make_poste(ref.c1, "P-06")
    for i in range(6):
        new_candidature(poste, f"PLAFOND{i}", "Test")
    response = recap(client, direction_user, poste, "convoquee", mode="filtre")
    assert response.status_code == 400
    assert (
        "La sélection compte 6 candidatures, au-delà du plafond de 5 par lot. Affinez les filtres."
        in texte(response)
    )
    assert 'name="jeton"' not in response.content.decode()
    Candidature.objects.filter(poste_concours=poste).first().delete()
    response = recap(client, direction_user, poste, "convoquee", mode="filtre")
    assert response.status_code == 200
    assert exact(texte(response), "5 candidatures sélectionnées")


def test_ca15_fragment_sans_case_cochee(client, direction_user, ref):
    client.force_login(direction_user)
    for params in ({"statut": "eligible"}, {"tri": "-candidat"}, {"taille": "25"}):
        response = client.get(url_liste(ref.p1), params, **HX)
        html = response.content.decode()
        assert "data-lot-case" in html
        assert not re.search(r"<input[^>]*data-lot-case[^>]*checked", html)
        assert not re.search(r"<input[^>]*checked[^>]*data-lot-case", html)


# --------------------------------------------------------------------------------------
# Décisions autorisées et récapitulatif (CA16 à CA23)
# --------------------------------------------------------------------------------------


def test_ca16_decisions_proposees(client, direction_user, ref):
    client.force_login(direction_user)
    doc = parse(client.get(url_liste(ref.p1)).content.decode())
    select = fa(doc, attrs={"id": "lot-action"})[0]
    valeurs = [o.attrs["value"] for o in fa(select, "option") if o.attrs.get("value")]
    assert valeurs == ["relance_eligibilite", "eligible", "preselectionnee", "convoquee"]
    libelles = texte_select(select)
    for interdit in ("Non éligible", "Rejetée", "Retenue"):
        assert interdit not in libelles


def texte_select(select) -> str:
    return " ".join(o.text() for o in fa(select, "option"))


@pytest.mark.parametrize(
    "valeur",
    ["non_eligible", "rejetee", "retenue", "recue", "en_analyse", "a_verifier", "inconnue"],
)
def test_ca17_valeurs_interdites(client, direction_user, ref, valeur):
    response = recap(client, direction_user, ref.p1, valeur, [ref.B])
    assert response.status_code == 400
    # À l'exécution, la valeur envoyée est contrôlée avant même le jeton.
    bon = recap(client, direction_user, ref.p1, "convoquee", [ref.B])
    response_exec = executer(client, ref.p1, jeton(bon), valeur)
    assert response_exec.status_code == 400
    assert not Decision.objects.filter(lot_id__isnull=False).exists()
    assert statut(ref.B) == S.ELIGIBLE
    attendu = 0 if valeur == "inconnue" else 2
    assert refus("valeur_interdite_en_lot").count() == attendu
    assert not entrees_lot()


def tous(ref):
    return [getattr(ref, k) for k in REFERENCE]


def test_ca18_recapitulatif_convoquee(client, direction_user, ref):
    response = recap(client, direction_user, ref.p1, "convoquee", tous(ref))
    assert response.status_code == 200
    t = texte(response)
    doc = parse(response.content.decode())
    repartition = fa(doc, attrs={"data-lot-repartition": True})[0].text()
    for attendu in [
        "À vérifier 2",
        "Éligible 3",
        "Non éligible 1",
        "Convoquée 1",
        "Reçue 1",
        "Présélectionnée 1",
        "Rejetée 1",
    ]:
        assert attendu in repartition
    assert exact(t, "4 candidatures concernées")
    payload = lots.lire_jeton(jeton(response), user=direction_user, poste=ref.p1)
    assert {c[0] for c in payload["c"]} == ids(ref.B, ref.G, ref.H, ref.J)
    g = groupes_par_libelle(response)
    assert g["Transition interdite depuis À vérifier"] == ids(ref.A, ref.F)
    assert g["Transition interdite depuis Non éligible"] == ids(ref.C)
    assert g["Transition interdite depuis Reçue"] == ids(ref.E)
    assert g["Transition interdite depuis Rejetée"] == ids(ref.I)
    assert g["Déjà Convoquée"] == ids(ref.D)
    assert groupe(response, "lot-avertissement", "anomalie_haute") == ids(ref.G)
    assert exact(t, "(1 dossier)")


def test_ca19_recapitulatif_eligible(client, direction_user, ref):
    cands = [ref.A, ref.C, ref.E, ref.F, ref.H, ref.I, ref.J]
    response = recap(client, direction_user, ref.p1, "eligible", cands)
    payload = lots.lire_jeton(jeton(response), user=direction_user, poste=ref.p1)
    assert {c[0] for c in payload["c"]} == ids(ref.A, ref.C, ref.E, ref.F)
    g = groupes_par_libelle(response)
    assert g["Révision : à faire depuis la fiche"] == ids(ref.H, ref.I)
    assert g["Déjà Éligible"] == ids(ref.J)
    assert groupe(response, "lot-avertissement", "verdict_non_favorable") == ids(
        ref.A, ref.C, ref.E, ref.F
    )
    assert "Ces dossiers n’ont pas été jugés éligibles par le moteur." in texte(response)


def test_ca20_revisions_exclues(client, direction_user, ref):
    response = recap(client, direction_user, ref.p1, "preselectionnee", [ref.D])
    assert groupes_par_libelle(response)["Révision : à faire depuis la fiche"] == ids(ref.D)
    k = new_candidature(ref.p1, "KADIRI", "Test", S.NON_ELIGIBLE)
    Decision.objects.create(
        candidature=k, auteur=ref.auteur, decision=S.NON_ELIGIBLE, motif="Décision fictive K."
    )
    response = recap(client, direction_user, ref.p1, "eligible", [k, ref.C])
    assert groupes_par_libelle(response)["Révision : à faire depuis la fiche"] == ids(k)
    payload = lots.lire_jeton(jeton(response), user=direction_user, poste=ref.p1)
    assert {c[0] for c in payload["c"]} == ids(ref.C)


def test_ca21_recapitulatif_sans_ecriture_metier(client, direction_user, ref):
    avant = (Decision.objects.count(), VerificationEligibilite.objects.count())
    statuts = {c.pk: c.statut for c in tous(ref)}
    response = recap(client, direction_user, ref.p1, "relance_eligibilite", tous(ref))
    assert response.status_code == 200
    assert (Decision.objects.count(), VerificationEligibilite.objects.count()) == avant
    assert {c.pk: statut(c) for c in tous(ref)} == statuts
    vues = JournalAudit.objects.filter(action=AuditAction.VIEW, metadata__vue="lot_recapitulatif")
    entree = vues.get()
    assert entree.object_id == str(ref.p1.pk)
    payload = lots.lire_jeton(jeton(response), user=direction_user, poste=ref.p1)
    assert entree.metadata["lot_id"] == payload["lot"]
    assert entree.metadata["operation"] == "relance_eligibilite"
    assert entree.metadata["nb_selectionnees"] == 10
    assert not entrees_lot()


def test_ca22_cent_noms_par_groupe(client, direction_user, ref):
    poste = make_poste(ref.c1, "P-150")
    for i in range(150):
        new_candidature(poste, f"ATTENTE{i:03d}", "Test", S.A_VERIFIER)
    response = recap(client, direction_user, poste, "convoquee", mode="filtre")
    doc = parse(response.content.decode())
    bloc = fa(doc, attrs={"data-lot-groupe": "transition_interdite"})[0]
    assert len(fa(bloc, attrs={"data-candidature": True})) == 100
    assert "et 50 autres" in bloc.text()


def bulk_poste(concours, n, reference):
    poste = make_poste(concours, reference)
    candidats = Candidat.objects.bulk_create(
        [Candidat(nom=f"MASSE{i:04d}", prenom="Test") for i in range(n)]
    )
    Candidature.objects.bulk_create(
        [
            Candidature(
                candidat=c,
                poste_concours=poste,
                concours=concours,
                date_candidature=timezone.now(),
                statut=S.ELIGIBLE if i % 3 else S.A_VERIFIER,
            )
            for i, c in enumerate(candidats)
        ]
    )
    cands = list(Candidature.objects.filter(poste_concours=poste))
    VerificationEligibilite.objects.bulk_create(
        [
            VerificationEligibilite(candidature=c, resultat="eligible", moteur_version="t")
            for c in cands
        ]
    )
    Anomalie.objects.bulk_create(
        [
            Anomalie(candidature=c, type="autre", gravite="haute", description="Fictive.")
            for c in cands[::7]
        ]
    )
    return poste


def test_ca23_requetes_constantes(client, direction_user, ref):
    petit = bulk_poste(ref.c1, 10, "Q-10")
    grand = bulk_poste(ref.c1, 500, "Q-500")
    client.force_login(direction_user)
    client.get(url_liste(petit))  # chauffe (session, types de contenu)
    counts = {}
    for nom, poste in (("petit", petit), ("grand", grand)):
        with CaptureQueriesContext(connection) as ctx:
            response = client.post(url_recap(poste), {"action": "convoquee", "mode": "filtre"})
        assert response.status_code == 200
        counts[nom] = len(ctx)
    assert counts["petit"] == counts["grand"]
    assert counts["grand"] <= 15


# --------------------------------------------------------------------------------------
# Exécution d'une décision en lot (CA24 à CA36)
# --------------------------------------------------------------------------------------


def _lot_bhj(client, user, ref):
    response = recap(client, user, ref.p1, "convoquee", [ref.B, ref.H, ref.J])
    return jeton(response)


@pytest.mark.parametrize("fixture_user", ["direction_user", "admin_user"])
def test_ca24_ca2_trois_decisions_distinctes(client, ref, request, fixture_user):
    user = request.getfixturevalue(fixture_user)
    token = _lot_bhj(client, user, ref)
    response = executer(client, ref.p1, token, "convoquee")
    assert response.status_code == 200
    assert exact(texte(response), "3 décisions enregistrées, 0 non enregistrée")
    nouvelles = Decision.objects.filter(lot_id__isnull=False)
    assert nouvelles.count() == 3
    assert {d.candidature_id for d in nouvelles} == ids(ref.B, ref.H, ref.J)
    assert {d.auteur_id for d in nouvelles} == {user.pk}
    assert {d.decision for d in nouvelles} == {S.CONVOQUEE}
    assert {d.motif for d in nouvelles} == {MOTIF}
    assert len({d.lot_id for d in nouvelles}) == 1
    assert {statut(c) for c in (ref.B, ref.H, ref.J)} == {S.CONVOQUEE}


@pytest.mark.parametrize("motif", ["", "   court   ", "x" * 2001])
def test_ca25_motif_invalide(client, direction_user, ref, motif):
    token = _lot_bhj(client, direction_user, ref)
    response = executer(client, ref.p1, token, "convoquee", motif=motif)
    assert response.status_code == 400
    assert not Decision.objects.filter(lot_id__isnull=False).exists()
    t = texte(response)
    assert (
        "Le motif est obligatoire (10 caractères minimum)." in t
        or "Le motif ne peut pas dépasser 2 000 caractères" in t
    )
    assert re.search(
        r'id="lot-motif"[^>]*>' + re.escape(motif) + "</textarea>", response.content.decode()
    )
    assert 'name="jeton"' in response.content.decode()
    assert not entrees_lot()


def test_ca26_confirmation_obligatoire(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    response = executer(client, ref.p1, token, "convoquee", confirmer=False)
    assert response.status_code == 400
    assert "Cochez la case de confirmation" in texte(response)
    assert not Decision.objects.filter(lot_id__isnull=False).exists()


def test_ca27_choix_avertissement_obligatoire_puis_exclure(client, direction_user, ref):
    token = jeton(recap(client, direction_user, ref.p1, "convoquee", tous(ref)))
    response = executer(client, ref.p1, token, "convoquee")
    assert response.status_code == 400
    assert "Indiquez si les dossiers signalés sont exclus ou inclus" in texte(response)
    assert not Decision.objects.filter(lot_id__isnull=False).exists()
    # Aucun choix n'est présélectionné.
    assert not re.search(r'name="choix_anomalie_haute"[^>]*checked', response.content.decode())

    response = executer(client, ref.p1, token, "convoquee", choix={"anomalie_haute": "exclure"})
    assert response.status_code == 200
    assert {statut(c) for c in (ref.B, ref.H, ref.J)} == {S.CONVOQUEE}
    assert statut(ref.G) == S.ELIGIBLE
    doc = parse(response.content.decode())
    exclues = fa(doc, attrs={"data-lot-exclues": True})[0]
    assert "Exclus par l’utilisateur" in exclues.text()
    assert {
        int(a.attrs["data-candidature"]) for a in fa(exclues, attrs={"data-candidature": True})
    } == ids(ref.G)


def test_ca27_inclure_confirme(client, direction_user, ref):
    token = jeton(recap(client, direction_user, ref.p1, "convoquee", tous(ref)))
    response = executer(client, ref.p1, token, "convoquee", choix={"anomalie_haute": "inclure"})
    assert response.status_code == 200
    assert statut(ref.G) == S.CONVOQUEE
    entree = JournalAudit.objects.get(action=AuditAction.DECISION, object_id=str(ref.G.pk))
    assert entree.metadata["avertissements_confirmes"] == ["anomalie_haute"]


def test_ca28_eligible_avec_verdict_non_favorable(client, direction_user, ref):
    cands = [ref.A, ref.C, ref.E, ref.F, ref.H, ref.I, ref.J]
    token = jeton(recap(client, direction_user, ref.p1, "eligible", cands))
    response = executer(
        client,
        ref.p1,
        token,
        "eligible",
        motif="Dossiers examinés un à un : critères remplis.",
        choix={"verdict_non_favorable": "inclure"},
    )
    assert exact(texte(response), "4 décisions enregistrées, 0 non enregistrée")
    for c in (ref.A, ref.C, ref.E, ref.F):
        assert statut(c) == S.ELIGIBLE
        assert Decision.objects.filter(candidature=c, lot_id__isnull=False).count() == 1
    fin = [e for e in entrees_lot() if e.metadata["etape"] == "termine"][0]
    assert fin.metadata["nb_avertissements_confirmes"] == 4
    entree = JournalAudit.objects.get(action=AuditAction.DECISION, object_id=str(ref.A.pk))
    assert entree.metadata["avertissements_confirmes"] == ["verdict_non_favorable"]


def test_ca29_concurrence_entre_recap_et_execution(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    autre = make_user("autre-direction@example.org", Role.DIRECTION)
    record_decision(ref.B, autre, S.REJETEE, "Rejet depuis la fiche, fictif.")
    response = executer(client, ref.p1, token, "convoquee")
    assert exact(texte(response), "2 décisions enregistrées, 1 non enregistrée")
    assert statut(ref.B) == S.REJETEE
    assert Decision.objects.filter(candidature=ref.B).count() == 1
    assert {statut(ref.H), statut(ref.J)} == {S.CONVOQUEE}
    assert groupe(response, "lot-echec", "modifiee") == ids(ref.B)
    assert "Modifiée entre-temps" in texte(response)


def test_ca30_nouvel_avertissement(client, direction_user, ref):
    token = jeton(recap(client, direction_user, ref.p1, "convoquee", [ref.B]))
    Anomalie.objects.create(
        candidature=ref.B, type=Anomalie.Type.AUTRE, gravite="haute", description="Nouvelle."
    )
    response = executer(client, ref.p1, token, "convoquee")
    assert groupe(response, "lot-echec", "nouvel_avertissement") == ids(ref.B)
    assert not Decision.objects.filter(candidature=ref.B, lot_id__isnull=False).exists()
    assert statut(ref.B) == S.ELIGIBLE


def test_ca31_erreur_technique_sur_une_candidature(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    original = evaluation_services.log

    def log_avec_panne(action, **kw):
        obj = kw.get("obj")
        if action == AuditAction.DECISION and obj is not None and obj.pk == ref.H.pk:
            raise RuntimeError("panne simulée")
        return original(action, **kw)

    with mock.patch.object(evaluation_services, "log", side_effect=log_avec_panne):
        response = executer(client, ref.p1, token, "convoquee")
    assert response.status_code == 200
    assert not Decision.objects.filter(candidature=ref.H, lot_id__isnull=False).exists()
    assert statut(ref.H) == S.PRESELECTIONNEE
    assert {statut(ref.B), statut(ref.J)} == {S.CONVOQUEE}
    assert groupe(response, "lot-echec", "erreur_technique") == ids(ref.H)
    assert exact(texte(response), "2 décisions enregistrées, 1 non enregistrée")


def test_ca32_jeton_altere(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    altere = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    response = executer(client, ref.p1, altere, "convoquee")
    assert response.status_code == 400
    assert "Le récapitulatif a expiré ou n’est plus valide. Recommencez la sélection." in texte(
        response
    )
    assert refus("jeton_invalide").count() == 1
    assert not Decision.objects.filter(lot_id__isnull=False).exists()
    assert not entrees_lot()


def test_ca32_jeton_expire(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    futur = time.time() + 31 * 60
    with mock.patch("django.core.signing.time.time", return_value=futur):
        response = executer(client, ref.p1, token, "convoquee")
    assert response.status_code == 400
    assert "Le récapitulatif a expiré" in texte(response)
    assert not Decision.objects.filter(lot_id__isnull=False).exists()


def test_ca32_jeton_autre_utilisateur_ou_poste(client, direction_user, admin_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    client.force_login(admin_user)
    response = executer(client, ref.p1, token, "convoquee")
    assert response.status_code == 400
    client.force_login(direction_user)
    response = executer(client, ref.p2, token, "convoquee")
    assert response.status_code == 400
    assert "Le récapitulatif a expiré" in texte(response)
    assert not Decision.objects.filter(lot_id__isnull=False).exists()


def test_ca33_rejeu_refuse(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    executer(client, ref.p1, token, "convoquee")
    nb = Decision.objects.count()
    response = executer(client, ref.p1, token, "convoquee")
    assert "Ce lot a déjà été exécuté" in texte(response)
    assert Decision.objects.count() == nb
    assert len(entrees_lot()) == 2


def test_ca33_rejeu_concurrent_serialise(direction_user, ref):
    """Deux exécutions du même lot : la seconde est refusée par le contrôle de rejeu."""
    payload = lots.preparer_lot(
        poste=ref.p1,
        user=direction_user,
        operation=lots.OPERATION_DECISION,
        decision=S.CONVOQUEE,
        mode="ids",
        flt=__import__("apps.recruitment.filters", fromlist=["x"]).CandidatureFilter(),
        candidatures=list(
            lots.annoter_pour_lot(
                __import__("apps.recruitment.filters", fromlist=["x"])
                .filter_candidatures(ref.p1, CandidatureFilterParams())[0]
                .filter(pk__in=[ref.B.pk])
            )
        ),
    )
    lots.executer_decisions(
        payload, poste=ref.p1, user=direction_user, motif=MOTIF, choix={}, request=None
    )
    with pytest.raises(lots.LotDejaExecute):
        lots.executer_decisions(
            payload, poste=ref.p1, user=direction_user, motif=MOTIF, choix={}, request=None
        )
    assert Decision.objects.filter(candidature=ref.B, lot_id__isnull=False).count() == 1


def test_ca34_concours_termine(client, direction_user, ref):
    response = recap(client, direction_user, ref.p3, "convoquee", ref.p3_cands)
    assert groupes_par_libelle(response)["Concours terminé"] == ids(*ref.p3_cands)
    assert "data-lot-executer" not in response.content.decode()
    # POST forgé : un jeton signé pour P3 avec une candidature « concernée ».
    payload = lots.lire_jeton(jeton(response), user=direction_user, poste=ref.p3)
    payload["c"] = [[c.pk, S.ELIGIBLE, None] for c in ref.p3_cands]
    response = executer(client, ref.p3, lots.signer_jeton(payload), "convoquee")
    assert response.status_code == 400
    assert not Decision.objects.filter(lot_id__isnull=False).exists()
    assert not entrees_lot()


def test_ca34_concours_termine_entre_temps(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    Concours.objects.filter(pk=ref.c1.pk).update(statut=Concours.Statut.TERMINE)
    response = executer(client, ref.p1, token, "convoquee")
    assert response.status_code == 400
    assert not Decision.objects.filter(lot_id__isnull=False).exists()


def test_ca34_concours_termine_pendant_execution(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    original = evaluation_services.record_decision
    appels = {"n": 0}

    def puis_termine(*args, **kw):
        appels["n"] += 1
        if appels["n"] == 2:
            Concours.objects.filter(pk=ref.c1.pk).update(statut=Concours.Statut.TERMINE)
        return original(*args, **kw)

    with mock.patch.object(evaluation_services, "record_decision", side_effect=puis_termine):
        response = executer(client, ref.p1, token, "convoquee")
    assert exact(texte(response), "1 décision enregistrée, 2 non enregistrées")
    assert "Concours terminé entre-temps" in texte(response)


def test_ca35_decisions_existantes_intactes(client, direction_user, ref):
    existantes = {d.pk: (d.decision, d.motif, d.created_at) for d in Decision.objects.all()}
    executer(client, ref.p1, _lot_bhj(client, direction_user, ref), "convoquee")
    for d in Decision.objects.filter(pk__in=existantes):
        assert (d.decision, d.motif, d.created_at) == existantes[d.pk]
    for d in Decision.objects.filter(lot_id__isnull=False):
        assert d.auteur.is_active and d.auteur.has_role(Role.ADMIN, Role.DIRECTION)


def test_ca36_mention_decision_prise_en_lot(client, direction_user, ref):
    executer(client, ref.p1, _lot_bhj(client, direction_user, ref), "convoquee")
    page = client.get(reverse("recruitment:candidature_detail", args=[ref.H.pk]))
    doc = parse(page.content.decode())
    items = fa(doc, attrs={"data-decision": True})
    lot = Decision.objects.get(candidature=ref.H, lot_id__isnull=False)
    for item in items:
        mention = "décision prise en lot" in item.text()
        assert mention == (item.attrs["data-decision"] == str(lot.pk))


# --------------------------------------------------------------------------------------
# Relance du calcul d'éligibilité (CA37 à CA42)
# --------------------------------------------------------------------------------------


def test_ca37_ca38_relance_synchrone(client, direction_user, ref):
    cands = [ref.B, ref.C, ref.D, ref.E, ref.J]
    response = recap(client, direction_user, ref.p1, "relance_eligibilite", cands)
    t = texte(response)
    assert exact(t, "5 candidatures concernées")
    assert exact(t, "2 candidatures déjà décidées")
    avant_decisions = Decision.objects.count()
    avant = VerificationEligibilite.objects.count()
    response = executer(client, ref.p1, jeton(response), "relance_eligibilite")
    assert response.status_code == 200
    assert VerificationEligibilite.objects.count() == avant + 5
    assert Decision.objects.count() == avant_decisions
    assert statut(ref.E) == S.ELIGIBLE  # verdict du moteur (profil Bac+5)
    assert statut(ref.D) == S.CONVOQUEE
    assert statut(ref.J) == S.ELIGIBLE  # CA38 : décision humaine conservée
    derniere_j = VerificationEligibilite.objects.filter(candidature=ref.J).order_by("-id")[0]
    assert derniere_j.resultat == "non_eligible"
    t = texte(response)
    assert exact(t, "5 verdicts recalculés")
    # B éligible → éligible ; C (Bac+5) non éligible → éligible ; E reçue → éligible.
    assert exact(t, "dont 2 statuts modifiés")


def test_ca39_relance_asynchrone(client, direction_user, ref, settings):
    settings.LOT_ELIGIBILITE_SYNC_MAX = 3
    cands = [ref.A, ref.B, ref.C, ref.E, ref.F]
    token = jeton(recap(client, direction_user, ref.p1, "relance_eligibilite", cands))
    with mock.patch("apps.evaluation.tasks.relancer_eligibilite_lot.delay") as delay:
        response = executer(client, ref.p1, token, "relance_eligibilite")
    assert exact(texte(response), "Relance lancée en arrière-plan pour 5 candidatures")
    kwargs = delay.call_args.kwargs
    payload = lots.lire_jeton(token, user=direction_user, poste=ref.p1)
    assert kwargs["lot_id"] == payload["lot"]
    assert kwargs["user_id"] == direction_user.pk
    assert sorted(kwargs["candidature_ids"]) == sorted(c.pk for c in cands)


def test_ca39_relance_asynchrone_en_mode_eager(client, direction_user, ref, settings):
    settings.LOT_ELIGIBILITE_SYNC_MAX = 3
    cands = [ref.A, ref.B, ref.C, ref.E, ref.H]
    token = jeton(recap(client, direction_user, ref.p1, "relance_eligibilite", cands))
    avant = VerificationEligibilite.objects.count()
    response = executer(client, ref.p1, token, "relance_eligibilite")
    assert exact(texte(response), "Relance lancée en arrière-plan pour 5 candidatures")
    assert VerificationEligibilite.objects.count() == avant + 5
    assert statut(ref.H) == S.PRESELECTIONNEE  # déjà décidée : jamais reclassée
    fin = [e for e in entrees_lot() if e.metadata["etape"] == "termine"]
    assert len(fin) == 1
    assert fin[0].actor == direction_user
    assert fin[0].metadata["nb_succes"] == 5
    assert not Decision.objects.filter(lot_id__isnull=False).exists()


def test_tache_relivree_entree_de_fin_unique(direction_user, ref):
    from apps.evaluation.tasks import relancer_eligibilite_lot

    kwargs = {
        "lot_id": str(uuid.uuid4()),
        "user_id": direction_user.pk,
        "poste_id": ref.p1.pk,
        "candidature_ids": [ref.B.pk],
        "meta": {"lot_id": None},
    }
    kwargs["meta"]["lot_id"] = kwargs["lot_id"]
    relancer_eligibilite_lot(**kwargs)
    relancer_eligibilite_lot(**kwargs)
    assert len(entrees_lot(kwargs["lot_id"])) == 1


def test_ca40_une_entree_update_par_candidature(client, direction_user, ref):
    cands = [ref.B, ref.C, ref.E]
    token = jeton(recap(client, direction_user, ref.p1, "relance_eligibilite", cands))
    debut = JournalAudit.objects.order_by("-id").first().pk
    executer(client, ref.p1, token, "relance_eligibilite")
    lot_id = lots.lire_jeton(token, user=direction_user, poste=ref.p1)["lot"]
    updates = JournalAudit.objects.filter(action=AuditAction.UPDATE, pk__gt=debut)
    assert updates.count() == 3
    for c in cands:
        e = updates.get(object_id=str(c.pk))
        assert e.actor == direction_user
        assert "via moteur d'éligibilité" in e.actor_repr
        assert e.object_repr == f"Candidature #{c.pk}"
        assert e.metadata["lot_id"] == lot_id
        assert e.metadata["operation"] == "relance_eligibilite"
        assert e.metadata["verification_id"]
        assert "statut_modifie" in e.metadata
    assert not updates.filter(actor_repr="moteur d'éligibilité").exists()


def test_ca41_relance_concours_termine(client, direction_user, ref):
    response = recap(client, direction_user, ref.p3, "relance_eligibilite", ref.p3_cands)
    assert "data-lot-executer" not in response.content.decode()
    avant = VerificationEligibilite.objects.count()
    payload = lots.lire_jeton(jeton(response), user=direction_user, poste=ref.p3)
    payload["c"] = [[c.pk, S.ELIGIBLE, None] for c in ref.p3_cands]
    executer(client, ref.p3, lots.signer_jeton(payload), "relance_eligibilite")
    assert VerificationEligibilite.objects.count() == avant


def test_ca42_erreur_moteur_sur_une_candidature(client, direction_user, ref):
    token = jeton(
        recap(client, direction_user, ref.p1, "relance_eligibilite", [ref.B, ref.C, ref.E])
    )
    original = evaluation_services.run_eligibility

    def panne(candidature, **kw):
        if candidature.pk == ref.C.pk:
            raise RuntimeError("panne du moteur simulée")
        return original(candidature, **kw)

    avant = VerificationEligibilite.objects.count()
    with mock.patch.object(evaluation_services, "run_eligibility", side_effect=panne):
        response = executer(client, ref.p1, token, "relance_eligibilite")
    assert VerificationEligibilite.objects.count() == avant + 2
    assert groupe(response, "lot-echec", "erreur_technique") == ids(ref.C)
    assert exact(texte(response), "2 verdicts recalculés")


# --------------------------------------------------------------------------------------
# Audit du lot (CA43, CA44)
# --------------------------------------------------------------------------------------


def test_ca43_entrees_lot(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    executer(client, ref.p1, token, "convoquee")
    lot_id = lots.lire_jeton(token, user=direction_user, poste=ref.p1)["lot"]
    entrees = entrees_lot(lot_id)
    assert [e.metadata["etape"] for e in entrees] == ["lance", "termine"]
    for e in entrees:
        assert e.object_id == str(ref.p1.pk)
        assert e.actor == direction_user
        assert e.metadata["operation"] == "decision"
        assert e.metadata["decision"] == "convoquee"
        assert e.metadata["motif"] == MOTIF
        assert e.metadata["mode"] == "ids"
        assert e.metadata["nb_selectionnees"] == 3
        assert e.metadata["nb_concernees"] == 3
        assert sorted(e.metadata["candidatures_ids"]) == sorted(ids(ref.B, ref.H, ref.J))
    assert entrees[1].metadata["nb_succes"] == 3
    assert sorted(entrees[1].metadata["succes_ids"]) == sorted(ids(ref.B, ref.H, ref.J))
    decisions_audit = JournalAudit.objects.filter(
        action=AuditAction.DECISION, metadata__lot_id=lot_id
    )
    assert decisions_audit.count() == 3
    assert {e.metadata["mode"] for e in decisions_audit} == {"lot"}


def test_ca44_aucune_donnee_personnelle_dans_les_entrees_lot(client, direction_user, ref):
    response = recap(
        client,
        direction_user,
        ref.p1,
        "relance_eligibilite",
        mode="filtre",
        params={"q": "amrani"},
    )
    executer(client, ref.p1, jeton(response), "relance_eligibilite")
    entrees = entrees_lot()
    assert len(entrees) == 2
    serialise = json.dumps([[e.metadata, e.object_repr, e.actor_repr] for e in entrees]).lower()
    assert all(e.metadata["recherche_appliquee"] is True for e in entrees)
    assert "amrani" not in serialise
    for c in Candidat.objects.filter(candidatures__poste_concours=ref.p1):
        for valeur in (c.nom, c.prenom, c.email, c.telephone, c.cin):
            assert valeur.lower() not in serialise


# --------------------------------------------------------------------------------------
# Interface (CA45 à CA49)
# --------------------------------------------------------------------------------------


def test_ca45_fragment_htmx_et_page_complete(client, direction_user, ref):
    fragment = recap(client, direction_user, ref.p1, "convoquee", [ref.B], htmx=True)
    assert "<html" not in fragment.content.decode()
    assert 'data-lot-etat="recap"' in fragment.content.decode()
    page = recap(client, direction_user, ref.p1, "convoquee", [ref.B])
    assert "<html" in page.content.decode()
    rapport = executer(client, ref.p1, jeton(fragment), "convoquee", htmx=True)
    assert "<html" not in rapport.content.decode()
    assert rapport["HX-Trigger"] == "lot-execute"
    assert 'data-lot-etat="rapport"' in rapport.content.decode()
    liste = client.get(url_liste(ref.p1)).content.decode()
    assert 'hx-trigger="lot-execute from:body"' in liste
    assert 'id="lot-panneau"' in liste


def test_ca46_parcours_sans_javascript(client, direction_user, ref):
    params = {"statut": "eligible", "tri": "-candidat", "taille": "25"}
    response = recap(client, direction_user, ref.p1, "convoquee", [ref.B, ref.J], params=params)
    assert "<html" in response.content.decode()
    response = executer(client, ref.p1, jeton(response), "convoquee")
    assert response.status_code == 200
    assert "<html" in response.content.decode()
    assert {statut(ref.B), statut(ref.J)} == {S.CONVOQUEE}
    doc = parse(response.content.decode())
    lien = fa(doc, attrs={"data-lot-retour": True})[0]
    assert lien.text().strip() == "Revenir à la liste"
    href = unescape(lien.attrs["href"])
    assert href.startswith(url_liste(ref.p1) + "?")
    for morceau in ("statut=eligible", "tri=-candidat", "taille=25"):
        assert morceau in href


INTERDITS = re.compile(r'class="[^"]*\b(ml-|mr-|pl-|pr-|left-|right-|text-left|text-right)')


def _controle_a11y(html: str, *, fieldset: bool):
    assert 'aria-live="polite"' in html
    assert re.search(r'<h2[^>]*tabindex="-1"', html)
    assert html.count("btn-primary") == 1
    assert "style=" not in html
    assert "<script" not in html
    assert not re.search(r"\son[a-z]+=", html)
    assert not INTERDITS.search(html)
    if fieldset:
        assert "<fieldset" in html and "<legend" in html


def test_ca47_accessibilite_recap_et_rapport(client, direction_user, ref):
    response = recap(client, direction_user, ref.p1, "convoquee", tous(ref), htmx=True)
    _controle_a11y(response.content.decode(), fieldset=True)
    rapport = executer(
        client, ref.p1, jeton(response), "convoquee", choix={"anomalie_haute": "exclure"}, htmx=True
    )
    _controle_a11y(rapport.content.decode(), fieldset=False)
    client.force_login(direction_user)
    barre = client.get(url_liste(ref.p1), **HX).content.decode()
    assert barre.count("btn-primary") == 1
    assert not INTERDITS.search(barre)


def test_ca48_aucune_donnee_personnelle_affichee(client, direction_user, ref):
    client.force_login(direction_user)
    pages = [client.get(url_liste(ref.p1), **HX).content.decode()]
    response = recap(client, direction_user, ref.p1, "convoquee", tous(ref), htmx=True)
    pages.append(response.content.decode())
    rapport = executer(
        client, ref.p1, jeton(response), "convoquee", choix={"anomalie_haute": "inclure"}, htmx=True
    )
    pages.append(rapport.content.decode())
    for html in pages:
        for c in Candidat.objects.filter(candidatures__poste_concours=ref.p1):
            for valeur in (c.email, c.telephone, c.cin, c.date_naissance.strftime("%d/%m/%Y")):
                assert valeur not in html
            assert "Villefictive" not in html and "Région fictive" not in html


def test_ca49_arabe_rtl(client, direction_user, ref):
    client.force_login(direction_user)
    liste = client.get(url_liste(ref.p1), HTTP_ACCEPT_LANGUAGE="ar")
    doc = parse(liste.content.decode())
    assert fa(doc, "html")[0].attrs.get("dir") == "rtl"
    page = client.post(
        url_recap(ref.p1),
        {"action": "convoquee", "mode": "ids", "ids": [ref.B.pk]},
        HTTP_ACCEPT_LANGUAGE="ar",
    )
    assert fa(parse(page.content.decode()), "html")[0].attrs.get("dir") == "rtl"


def test_ca49_libelles_traduits():
    """Tous les libellés fixes des gabarits du lot passent par le système de traduction."""
    from pathlib import Path

    racine = Path(__file__).resolve().parent.parent / "templates" / "recruitment"
    for nom in (
        "_lot_barre.html",
        "_lot_panneau.html",
        "_lot_recapitulatif.html",
        "_lot_rapport.html",
        "lot_page.html",
        "_lot_noms.html",
    ):
        html = (racine / nom).read_text()
        html = re.sub(r"\{% blocktranslate.*?\{% endblocktranslate %\}", " ", html, flags=re.S)
        html = re.sub(r"\{% comment %\}.*?\{% endcomment %\}", " ", html, flags=re.S)
        sans_balises = re.sub(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", " ", html, flags=re.S)
        sans_balises = re.sub(r"<[^>]+>", " ", sans_balises)
        restant = re.sub(r"[\s·(),:/—]+", "", sans_balises)
        assert restant == "", f"{nom} : texte non traduit « {restant[:80]} »"


# --------------------------------------------------------------------------------------
# Cas limites
# --------------------------------------------------------------------------------------


def test_tous_exclus_par_avertissement(client, direction_user, ref):
    """Tous les dossiers concernés exclus : rien n'est enregistré, entrées « lot » écrites."""
    token = jeton(recap(client, direction_user, ref.p1, "convoquee", [ref.G]))
    response = executer(client, ref.p1, token, "convoquee", choix={"anomalie_haute": "exclure"})
    assert response.status_code == 200
    assert exact(texte(response), "0 décision enregistrée, 0 non enregistrée")
    assert statut(ref.G) == S.ELIGIBLE
    assert [e.metadata["etape"] for e in entrees_lot()] == ["lance", "termine"]
    assert entrees_lot()[0].metadata["exclues_ids"] == [ref.G.pk]


def test_toutes_non_concernees_sans_bouton(client, direction_user, ref):
    response = recap(client, direction_user, ref.p1, "convoquee", [ref.A, ref.F])
    assert exact(texte(response), "0 candidature concernée")
    assert "data-lot-executer" not in response.content.decode()
    response = executer(client, ref.p1, jeton(response), "convoquee")
    assert response.status_code == 400
    assert not entrees_lot()


def test_compteur_initial_au_singulier(client, direction_user, ref):
    client.force_login(direction_user)
    response = client.get(url_liste(ref.p1))
    assert exact(texte(response), "0 candidature sélectionnée")


def test_barre_absente_sans_resultat(client, direction_user, ref):
    client.force_login(direction_user)
    vide = make_poste(ref.c1, "P-VIDE")
    assert 'id="lot"' not in client.get(url_liste(vide)).content.decode()
    filtre = client.get(url_liste(ref.p1), {"q": "zzzinconnu"}).content.decode()
    assert 'id="lot"' not in filtre


def test_motif_html_echappe(client, direction_user, ref):
    token = _lot_bhj(client, direction_user, ref)
    motif = "<script>alert(1)</script> motif fictif"
    response = executer(client, ref.p1, token, "convoquee", motif=motif, confirmer=False)
    assert "<script>alert(1)</script>" not in response.content.decode()
    assert "&lt;script&gt;" in response.content.decode()
