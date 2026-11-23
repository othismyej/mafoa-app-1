"""Tests d'acceptation de KAN-13 — Décision humaine motivée depuis la fiche candidature.

Les attentes sont fondées sur la spécification ``docs/specs/KAN-13-decision-humaine.md``.
Toutes les données sont fictives (domaines example.org, numéros +212600000xxx). Aucun appel IA.
"""

import re
from datetime import datetime
from unittest import mock

import pytest
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit.models import AuditAction, JournalAudit
from apps.evaluation.decisions import ConfirmationRequise, DecisionError
from apps.evaluation.models import Anomalie, Decision, VerificationEligibilite
from apps.evaluation.services import record_decision, run_eligibility
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    OffreEmploi,
    Organisation,
    PosteConcours,
)

from .conftest import make_user
from .test_candidature_detail import parse

pytestmark = pytest.mark.django_db

S = Candidature.Statut
HX = {"HTTP_HX_REQUEST": "true"}
MOTIF = "Dossier complet et conforme aux critères du poste."
LIBELLES = {s.value: s.label for s in S}

# Table des transitions de la règle 4 (spec), indépendante du code de production.
TABLE = {
    S.RECUE: {S.ELIGIBLE, S.NON_ELIGIBLE, S.REJETEE},
    S.EN_ANALYSE: {S.ELIGIBLE, S.NON_ELIGIBLE, S.REJETEE},
    S.A_VERIFIER: {S.ELIGIBLE, S.NON_ELIGIBLE, S.REJETEE},
    S.ELIGIBLE: {S.NON_ELIGIBLE, S.PRESELECTIONNEE, S.CONVOQUEE, S.REJETEE},
    S.NON_ELIGIBLE: {S.ELIGIBLE, S.REJETEE},
    S.PRESELECTIONNEE: {S.CONVOQUEE, S.REJETEE, S.ELIGIBLE},
    S.CONVOQUEE: {S.RETENUE, S.REJETEE, S.PRESELECTIONNEE},
    S.RETENUE: {S.REJETEE, S.CONVOQUEE},
    S.REJETEE: {S.ELIGIBLE, S.NON_ELIGIBLE},
}
DECISIONS = [S.ELIGIBLE, S.NON_ELIGIBLE, S.PRESELECTIONNEE, S.CONVOQUEE, S.RETENUE, S.REJETEE]
COUPLES = [(statut, decision) for statut in TABLE for decision in DECISIONS]

PROFIL_BAC5 = {
    "formation": [
        {
            "intitule": "Master génie de l'eau",
            "niveau": "Bac+5",
            "specialite": "Hydraulique",
            "etablissement": "École fictive Alpha",
            "annee_obtention": 2014,
        }
    ],
    "experiences": [
        {
            "debut": "2015-01-01",
            "fin": "2024-12-31",
            "employeur": "Bureau d'études fictif Gamma",
            "poste": "Ingénieur",
        }
    ],
}
PROFIL_BAC2 = {
    "formation": [
        {
            "intitule": "DUT fictif",
            "niveau": "Bac+2",
            "specialite": "Hydraulique",
            "etablissement": "Institut fictif Beta",
            "annee_obtention": 2014,
        }
    ],
    "experiences": PROFIL_BAC5["experiences"],
}


# --------------------------------------------------------------------------------------
# Jeu de données fictif
# --------------------------------------------------------------------------------------


@pytest.fixture
def org(db):
    return Organisation.objects.create(nom="Agence Fictive Tafilalt", type=Organisation.Type.PUBLIC)


@pytest.fixture
def concours(org):
    return Concours.objects.create(organisation=org, intitule="Concours fictif Tafilalt 2026")


@pytest.fixture
def poste(concours):
    return PosteConcours.objects.create(
        concours=concours,
        intitule="Ingénieur hydraulique",
        reference="P-13",
        nombre_postes=3,
        niveau_etudes_min=5,
        experience_min_annees=2,
    )


_seq = {"n": 0}


def new_candidature(poste=None, statut=S.ELIGIBLE, offre=None, **kw) -> Candidature:
    _seq["n"] += 1
    n = _seq["n"]
    candidat = Candidat.objects.create(
        nom=f"Fictif{n}",
        prenom="Test",
        email=f"decision{n}@example.org",
        telephone=f"+212600000{n % 1000:03d}",
    )
    data = {
        "candidat": candidat,
        "poste_concours": poste,
        "offre": offre,
        "date_candidature": timezone.make_aware(datetime(2026, 3, 15, 10, 30)),
        "statut": statut,
        "donnees_declarees": PROFIL_BAC5,
    }
    data.update(kw)
    return Candidature.objects.create(**data)


@pytest.fixture
def cand(poste):
    return new_candidature(poste, S.ELIGIBLE)


def url_post(candidature_or_pk) -> str:
    pk = getattr(candidature_or_pk, "pk", candidature_or_pk)
    return reverse("recruitment:decision_enregistrer", args=[pk])


def url_fiche(candidature_or_pk) -> str:
    pk = getattr(candidature_or_pk, "pk", candidature_or_pk)
    return reverse("recruitment:candidature_detail", args=[pk])


_DEFAUT = object()


def payload(
    candidature,
    decision,
    motif=MOTIF,
    *,
    statut_attendu=_DEFAUT,
    derniere=_DEFAUT,
    confirmer=True,
):
    candidature.refresh_from_db()
    if statut_attendu is _DEFAUT:
        statut_attendu = candidature.statut
    if derniere is _DEFAUT:
        last = candidature.decisions.order_by("-created_at", "-id").first()
        derniere = last.pk if last else ""
    data = {
        "decision": decision,
        "motif": motif,
        "statut_attendu": statut_attendu,
        "derniere_decision_id": derniere,
    }
    if confirmer:
        data["confirmer_revision"] = "1"
        data["confirmer_avertissements"] = "1"
    return data


def poster(client, user, candidature, decision, motif=MOTIF, *, htmx=False, extra=None, **kw):
    if user is not None:
        client.force_login(user)
    headers = dict(HX) if htmx else {}
    headers.update(extra or {})
    return client.post(
        url_post(candidature), payload(candidature, decision, motif, **kw), **headers
    )


def statut_en_base(candidature) -> str:
    return Candidature.objects.get(pk=candidature.pk).statut


def audits_decision():
    return JournalAudit.objects.filter(action=AuditAction.DECISION)


def texte(response) -> str:
    return parse(response).text()


def assert_rien_enregistre(candidature, statut, nb_decisions=0):
    assert Decision.objects.filter(candidature=candidature).count() == nb_decisions
    assert statut_en_base(candidature) == statut


def form_hidden_values(response) -> dict:
    root = parse(response)
    forms = root.with_attr("data-decision-form")
    assert len(forms) == 1, "formulaire de décision absent"
    values = {}
    for node in forms[0].iter():
        if node.tag == "input" and node.attrs.get("name"):
            values[node.attrs["name"]] = node.attrs.get("value", "")
    return values


def options_proposees(response) -> list[tuple[str, str]]:
    root = parse(response)
    forms = root.with_attr("data-decision-form")
    assert len(forms) == 1
    selects = [
        n for n in forms[0].iter() if n.tag == "select" and n.attrs.get("name") == "decision"
    ]
    assert len(selects) == 1
    return [
        (o.attrs.get("value", ""), o.text())
        for o in selects[0].iter()
        if o.tag == "option" and o.attrs.get("value")
    ]


def decision_humaine(candidature, auteur, value, motif=MOTIF) -> Decision:
    """Décision antérieure créée directement (le statut courant est aligné sur elle)."""
    d = Decision.objects.create(candidature=candidature, auteur=auteur, decision=value, motif=motif)
    Candidature.objects.filter(pk=candidature.pk).update(statut=value)
    candidature.refresh_from_db()
    return d


# --------------------------------------------------------------------------------------
# CA1, CA2 — enregistrement par direction et admin
# --------------------------------------------------------------------------------------


def test_direction_preselectionne_un_dossier_eligible(client, direction_user, poste):
    """CA1 — direction : Decision créée (auteur, décision, motif), statut mis à jour, nouveau
    statut affiché et décision en tête de l'historique."""
    c = new_candidature(poste, S.A_VERIFIER)
    record_decision(c, direction_user, S.ELIGIBLE, "Première décision fictive sur le dossier.")
    response = poster(
        client,
        direction_user,
        c,
        S.PRESELECTIONNEE,
        "Profil solide, à présélectionner.",
        htmx=True,
        confirmer=False,
    )
    assert response.status_code == 200
    d = Decision.objects.filter(candidature=c).order_by("-created_at", "-id").first()
    assert d.auteur == direction_user
    assert d.decision == S.PRESELECTIONNEE
    assert d.motif == "Profil solide, à présélectionner."
    assert statut_en_base(c) == S.PRESELECTIONNEE
    html = response.content.decode()
    root = parse(html)
    statut = root.with_attr("id", "statut-candidature")
    assert statut and "Présélectionnée" in statut[0].text()
    section = root.with_attr("id", "decisions")
    assert section
    hist = section[0].text()
    assert "Profil solide, à présélectionner." in hist
    assert hist.index("Profil solide, à présélectionner.") < hist.index(
        "Première décision fictive sur le dossier."
    )


def test_admin_obtient_le_meme_resultat(client, admin_user, cand):
    """CA2 — admin : décision créée et statut mis à jour."""
    response = poster(client, admin_user, cand, S.PRESELECTIONNEE, confirmer=False)
    assert response.status_code == 302
    d = Decision.objects.get(candidature=cand)
    assert (d.auteur, d.decision, d.motif) == (admin_user, S.PRESELECTIONNEE, MOTIF)
    assert statut_en_base(cand) == S.PRESELECTIONNEE


# --------------------------------------------------------------------------------------
# CA3 à CA6, CA30 — permissions, CSRF, méthode, 404
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.CONSULTANT, Role.CLIENT])
@pytest.mark.parametrize("htmx", [False, True])
def test_roles_vague_2_refuses_403(client, cand, role, htmx):
    """CA3 — consultant / client : 403, aucune Decision, statut inchangé, pas d'audit décision."""
    user = make_user(f"{role}-{int(htmx)}@example.org", role)
    response = poster(client, user, cand, S.PRESELECTIONNEE, htmx=htmx)
    assert response.status_code == 403
    assert_rien_enregistre(cand, S.ELIGIBLE)
    assert not audits_decision().exists()


def test_non_connecte_redirige_vers_connexion(client, cand):
    """CA4 — non connecté : redirection vers la connexion, rien n'est enregistré."""
    response = poster(client, None, cand, S.PRESELECTIONNEE)
    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))
    assert_rien_enregistre(cand, S.ELIGIBLE)
    assert not audits_decision().exists()


def test_utilisateur_desactive_redirige_vers_connexion(client, direction_user, cand):
    """Cas limite — utilisateur désactivé pendant la saisie : redirection, rien n'est enregistré."""
    client.force_login(direction_user)
    direction_user.is_active = False
    direction_user.save()
    response = client.post(url_post(cand), payload(cand, S.PRESELECTIONNEE))
    assert response.status_code == 302
    assert reverse("account_login") in response.url
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_sans_totp_pas_de_decision(client, user_without_mfa, cand):
    """Règle 1 — un utilisateur sans TOTP activé ne peut pas enregistrer de décision."""
    response = poster(client, user_without_mfa, cand, S.PRESELECTIONNEE)
    assert response.status_code in (302, 403)
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_post_sans_jeton_csrf_refuse(direction_user, cand):
    """CA5 — POST sans jeton CSRF valide : 403, rien n'est enregistré."""
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(direction_user)
    response = csrf_client.post(url_post(cand), payload(cand, S.PRESELECTIONNEE), **HX)
    assert response.status_code == 403
    assert_rien_enregistre(cand, S.ELIGIBLE)
    assert not audits_decision().exists()


def test_post_avec_jeton_csrf_faux_refuse(direction_user, cand):
    """CA5 — jeton CSRF invalide : 403, rien n'est enregistré."""
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(direction_user)
    csrf_client.get(url_fiche(cand))
    data = payload(cand, S.PRESELECTIONNEE)
    data["csrfmiddlewaretoken"] = "x" * 64
    response = csrf_client.post(url_post(cand), data)
    assert response.status_code == 403
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_post_avec_jeton_csrf_de_la_fiche_accepte(direction_user, cand):
    """CA5 (contrôle) — avec le jeton CSRF de la fiche, la décision est acceptée."""
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(direction_user)
    csrf_client.get(url_fiche(cand))
    data = payload(cand, S.PRESELECTIONNEE)
    data["csrfmiddlewaretoken"] = csrf_client.cookies["csrftoken"].value
    response = csrf_client.post(url_post(cand), data)
    assert response.status_code == 302
    assert statut_en_base(cand) == S.PRESELECTIONNEE


@pytest.mark.parametrize("htmx", [False, True])
def test_get_sur_url_de_decision_405(client, direction_user, cand, htmx):
    """CA6 — GET sur l'URL d'enregistrement : 405, rien n'est enregistré."""
    client.force_login(direction_user)
    response = client.get(url_post(cand), payload(cand, S.PRESELECTIONNEE), **(HX if htmx else {}))
    assert response.status_code == 405
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_candidature_inexistante_404(client, direction_user, cand):
    """CA30 — identifiant inexistant : 404, rien n'est enregistré."""
    client.force_login(direction_user)
    data = payload(cand, S.PRESELECTIONNEE)
    response = client.post(url_post(cand.pk + 100000), data)
    assert response.status_code == 404
    assert Decision.objects.count() == 0
    assert not audits_decision().exists()


# --------------------------------------------------------------------------------------
# CA7, CA8 — motif
# --------------------------------------------------------------------------------------

MESSAGE_MOTIF = "Le motif est obligatoire (10 caractères minimum)"


@pytest.mark.parametrize(
    "motif", ["", "     ", "court", "123456789", "   123456789   ", "\n\t ok \n"]
)
def test_motif_vide_ou_trop_court_refuse(client, direction_user, cand, motif):
    """CA7 — motif vide, espaces ou < 10 caractères après suppression des espaces : refus avec le
    message attendu, aucune Decision, statut inchangé."""
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, motif, htmx=True)
    assert response.status_code == 400
    assert MESSAGE_MOTIF in texte(response)
    assert_rien_enregistre(cand, S.ELIGIBLE)
    assert not audits_decision().exists()


@pytest.mark.parametrize("motif", ["", "   ", "123456789", "  123456789  "])
def test_service_refuse_motif_trop_court(direction_user, cand, motif):
    """CA7 / CA31 — appel direct du service : erreur levée, rien n'est enregistré."""
    with pytest.raises(DecisionError) as exc:
        record_decision(cand, direction_user, S.PRESELECTIONNEE, motif)
    assert MESSAGE_MOTIF in str(exc.value)
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_motif_de_10_caracteres_apres_nettoyage_accepte_et_stocke_sans_espaces(
    client, direction_user, cand
):
    """CA7 / règle 3 — exactement 10 caractères après suppression des espaces : accepté, motif
    enregistré sans espaces de bord."""
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, "   1234567890  \n")
    assert response.status_code == 302
    assert Decision.objects.get(candidature=cand).motif == "1234567890"
    assert audits_decision().get().metadata["motif"] == "1234567890"


def test_motif_de_plus_de_2000_caracteres_refuse(client, direction_user, cand):
    """CA8 — motif > 2 000 caractères : refus avec message explicite, rien n'est enregistré."""
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, "a" * 2001, htmx=True)
    assert response.status_code == 400
    root = parse(response)
    assert root.with_attr("data-decision-erreur")
    assert re.search(r"2\s?000", root.text()), "le message doit indiquer la limite de 2 000"
    assert_rien_enregistre(cand, S.ELIGIBLE)
    with pytest.raises(DecisionError):
        record_decision(cand, direction_user, S.PRESELECTIONNEE, "a" * 2001)
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_motif_de_2000_caracteres_accepte(client, direction_user, cand):
    """CA8 (limite) — exactement 2 000 caractères (espaces de bord en plus) : accepté."""
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, "  " + "b" * 2000 + "  ")
    assert response.status_code == 302
    assert Decision.objects.get(candidature=cand).motif == "b" * 2000


# --------------------------------------------------------------------------------------
# CA9, CA10, CA31 — table des transitions
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("statut", "decision"), COUPLES, ids=[f"{s}->{d}" for s, d in COUPLES])
def test_table_des_transitions_par_la_vue(client, direction_user, poste, statut, decision):
    """CA9 — couple de la table : accepté (avec confirmation) ; couple absent : 400 avec message,
    rien n'est enregistré."""
    c = new_candidature(poste, statut)
    response = poster(client, direction_user, c, decision, htmx=True)
    if decision in TABLE[statut]:
        assert response.status_code == 200, texte(response)
        assert parse(response).with_attr("data-decision-succes")
        assert Decision.objects.filter(candidature=c, decision=decision).count() == 1
        assert statut_en_base(c) == decision
        assert audits_decision().count() == 1
    else:
        assert response.status_code == 400
        root = parse(response)
        assert root.with_attr("data-decision-erreur")
        assert root.with_attr("data-decision-erreur")[0].text().strip()
        assert_rien_enregistre(c, statut)
        assert not audits_decision().exists()


@pytest.mark.parametrize(("statut", "decision"), COUPLES, ids=[f"{s}->{d}" for s, d in COUPLES])
def test_table_des_transitions_par_le_service(direction_user, poste, statut, decision):
    """CA9 / CA31 — même table appliquée directement par le service ``record_decision``."""
    c = new_candidature(poste, statut)
    if decision in TABLE[statut]:
        d = record_decision(
            c,
            direction_user,
            decision,
            MOTIF,
            confirmer_revision=True,
            confirmer_avertissements=True,
        )
        assert d.pk and statut_en_base(c) == decision
    else:
        with pytest.raises(DecisionError):
            record_decision(
                c,
                direction_user,
                decision,
                MOTIF,
                confirmer_revision=True,
                confirmer_avertissements=True,
            )
        assert_rien_enregistre(c, statut)
        assert not audits_decision().exists()


@pytest.mark.parametrize("valeur", [S.RECUE, S.EN_ANALYSE, S.A_VERIFIER])
@pytest.mark.parametrize("statut", [S.ELIGIBLE, S.REJETEE, S.RECUE, S.A_VERIFIER])
def test_statuts_non_decisionnels_refuses(client, direction_user, poste, valeur, statut):
    """CA10 — « reçue », « en analyse », « à vérifier » ne sont jamais des décisions (vue et
    service)."""
    c = new_candidature(poste, statut)
    response = poster(client, direction_user, c, valeur, htmx=True)
    assert response.status_code == 400
    assert_rien_enregistre(c, statut)
    with pytest.raises(DecisionError):
        record_decision(
            c, direction_user, valeur, MOTIF, confirmer_revision=True, confirmer_avertissements=True
        )
    assert_rien_enregistre(c, statut)
    assert not audits_decision().exists()


@pytest.mark.parametrize("valeur", ["admise", "", "ELIGIBLE", "retenue;drop"])
def test_valeur_de_decision_inconnue_400(client, direction_user, cand, valeur):
    """Cas limite — valeur de décision inconnue ou falsifiée : 400, rien n'est enregistré."""
    response = poster(client, direction_user, cand, valeur)
    assert response.status_code == 400
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_service_refuse_transition_absente(direction_user, poste):
    """CA31 — appel direct au service avec transition absente : erreur, rien n'est enregistré."""
    c = new_candidature(poste, S.A_VERIFIER)
    with pytest.raises(DecisionError):
        record_decision(c, direction_user, S.PRESELECTIONNEE, MOTIF)
    assert_rien_enregistre(c, S.A_VERIFIER)


# --------------------------------------------------------------------------------------
# CA11 à CA14 — conséquences de la table
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("decision", [S.PRESELECTIONNEE, S.CONVOQUEE, S.RETENUE])
def test_non_eligible_ne_peut_pas_etre_selectionne(client, direction_user, poste, decision):
    """CA11 — non éligible → présélectionnée / convoquée / retenue : refusé."""
    c = new_candidature(poste, S.NON_ELIGIBLE)
    response = poster(client, direction_user, c, decision)
    assert response.status_code == 400
    assert_rien_enregistre(c, S.NON_ELIGIBLE)


def test_non_eligible_requalifie_puis_preselectionne(client, direction_user, poste):
    """CA11 — « éligible » (motif valide) puis « présélectionnée » : deux décisions dans l'ordre."""
    c = new_candidature(poste, S.NON_ELIGIBLE)
    r1 = poster(
        client,
        direction_user,
        c,
        S.ELIGIBLE,
        "Diplôme équivalent reconnu après examen.",
        confirmer=False,
    )
    assert r1.status_code == 302
    r2 = poster(client, direction_user, c, S.PRESELECTIONNEE, confirmer=False)
    assert r2.status_code == 302
    decisions = list(
        Decision.objects.filter(candidature=c)
        .order_by("created_at", "id")
        .values_list("decision", flat=True)
    )
    assert decisions == [S.ELIGIBLE, S.PRESELECTIONNEE]
    assert statut_en_base(c) == S.PRESELECTIONNEE
    assert audits_decision().count() == 2


def test_eligible_peut_etre_rejete(client, direction_user, cand):
    """CA12 — éligible → rejetée avec motif valide : accepté."""
    response = poster(
        client,
        direction_user,
        cand,
        S.REJETEE,
        "Candidat absent à l'épreuve fictive.",
        confirmer=False,
    )
    assert response.status_code == 302
    assert Decision.objects.get(candidature=cand).decision == S.REJETEE
    assert statut_en_base(cand) == S.REJETEE


def test_eligible_ne_peut_pas_etre_retenu(client, direction_user, cand):
    """CA13 — éligible → retenue : refusé (retenue seulement depuis convoquée)."""
    response = poster(client, direction_user, cand, S.RETENUE, htmx=True)
    assert response.status_code == 400
    assert_rien_enregistre(cand, S.ELIGIBLE)
    with pytest.raises(DecisionError):
        record_decision(cand, direction_user, S.RETENUE, MOTIF, confirmer_avertissements=True)


def test_decision_identique_au_statut_refusee(client, direction_user, poste):
    """CA14 — convoquée → convoquée : refus « La candidature est déjà Convoquée »."""
    c = new_candidature(poste, S.CONVOQUEE)
    response = poster(client, direction_user, c, S.CONVOQUEE, htmx=True)
    assert response.status_code == 400
    assert "La candidature est déjà Convoquée" in texte(response)
    assert_rien_enregistre(c, S.CONVOQUEE)
    with pytest.raises(DecisionError) as exc:
        record_decision(c, direction_user, S.CONVOQUEE, MOTIF)
    assert "La candidature est déjà Convoquée" in str(exc.value)


# --------------------------------------------------------------------------------------
# CA15, CA16 — révision
# --------------------------------------------------------------------------------------


def test_revision_d_un_rejet_cree_une_nouvelle_decision(client, direction_user, admin_user, cand):
    """CA15 — rejetée (D1) → éligible avec confirmation : D2 créée, D1 inchangée, statut éligible,
    audit avec revision = true et decision_remplacee_id = D1."""
    d1 = record_decision(cand, admin_user, S.REJETEE, "Rejet initial fictif pour pièce absente.")
    d1_avant = Decision.objects.values("motif", "created_at", "decision", "auteur").get(pk=d1.pk)
    response = poster(
        client,
        direction_user,
        cand,
        S.ELIGIBLE,
        "Recours fictif accepté, pièce fournie.",
        confirmer=True,
    )
    assert response.status_code == 302
    assert Decision.objects.filter(candidature=cand).count() == 2
    d2 = Decision.objects.filter(candidature=cand).exclude(pk=d1.pk).get()
    assert d2.decision == S.ELIGIBLE and d2.auteur == direction_user
    assert Decision.objects.values("motif", "created_at", "decision", "auteur").get(pk=d1.pk) == (
        d1_avant
    )
    assert statut_en_base(cand) == S.ELIGIBLE
    entry = audits_decision().get(metadata__decision_id=d2.pk)
    assert entry.metadata["revision"] is True
    assert entry.metadata["decision_remplacee_id"] == d1.pk


REVISIONS = [
    (S.PRESELECTIONNEE, S.ELIGIBLE),
    (S.CONVOQUEE, S.PRESELECTIONNEE),
    (S.RETENUE, S.CONVOQUEE),
    (S.RETENUE, S.REJETEE),
    (S.REJETEE, S.ELIGIBLE),
    (S.REJETEE, S.NON_ELIGIBLE),
    (S.ELIGIBLE, S.NON_ELIGIBLE),
    (S.NON_ELIGIBLE, S.ELIGIBLE),
]


@pytest.mark.parametrize(("statut", "decision"), REVISIONS, ids=[f"{s}->{d}" for s, d in REVISIONS])
def test_revision_sans_confirmation_refusee(client, direction_user, poste, statut, decision):
    """CA16 — révision (règle 6) sans confirmation : demande de confirmation mentionnant la date de
    la décision remplacée, rien n'est enregistré."""
    c = new_candidature(poste, statut)
    d1 = decision_humaine(c, direction_user, statut)
    response = poster(client, direction_user, c, decision, htmx=True, confirmer=False)
    root = parse(response)
    assert root.with_attr("data-decision-confirmation")
    date_d1 = timezone.localtime(d1.created_at).strftime("%d/%m/%Y")
    assert f"Je confirme revenir sur la décision du {date_d1}" in root.text()
    assert_rien_enregistre(c, statut, nb_decisions=1)
    assert not audits_decision().exists()
    with pytest.raises(ConfirmationRequise):
        record_decision(c, direction_user, decision, MOTIF)
    assert_rien_enregistre(c, statut, nb_decisions=1)


@pytest.mark.parametrize(("statut", "decision"), REVISIONS, ids=[f"{s}->{d}" for s, d in REVISIONS])
def test_revision_avec_confirmation_acceptee_et_tracee(
    client, direction_user, poste, statut, decision
):
    """CA16 / règle 6 — avec confirmation, la révision est acceptée et tracée comme telle."""
    c = new_candidature(poste, statut)
    d1 = decision_humaine(c, direction_user, statut)
    response = poster(client, direction_user, c, decision, confirmer=True)
    assert response.status_code == 302
    assert statut_en_base(c) == decision
    entry = audits_decision().get()
    assert entry.metadata["revision"] is True
    assert entry.metadata["decision_remplacee_id"] == d1.pk


def test_requalification_sans_decision_humaine_n_est_pas_une_revision(
    client, direction_user, poste
):
    """Règle 6 — éligible ↔ non éligible n'est une révision qu'après une décision humaine."""
    c = new_candidature(poste, S.ELIGIBLE)
    response = poster(client, direction_user, c, S.NON_ELIGIBLE, confirmer=False)
    assert response.status_code == 302
    assert statut_en_base(c) == S.NON_ELIGIBLE
    assert audits_decision().get().metadata.get("revision") in (False, None)


# --------------------------------------------------------------------------------------
# CA17 à CA19 — journal d'audit et atomicité
# --------------------------------------------------------------------------------------


def test_decision_acceptee_journalisee(client, direction_user, cand):
    """CA17 — exactement une entrée JournalAudit « decision » complète (acteur, objet, avant/après,
    decision_id, motif, IP, navigateur)."""
    response = poster(
        client,
        direction_user,
        cand,
        S.CONVOQUEE,
        confirmer=False,
        extra={"REMOTE_ADDR": "203.0.113.7", "HTTP_USER_AGENT": "NavigateurFictif/1.0"},
    )
    assert response.status_code == 302
    d = Decision.objects.get(candidature=cand)
    entries = audits_decision()
    assert entries.count() == 1
    e = entries.get()
    assert e.actor == direction_user
    assert e.content_type == ContentType.objects.get_for_model(Candidature)
    assert e.object_id == str(cand.pk)
    assert e.before["statut"] == S.ELIGIBLE
    assert e.after["statut"] == S.CONVOQUEE
    assert e.metadata["decision_id"] == d.pk
    assert e.metadata["motif"] == MOTIF
    assert e.ip_address == "203.0.113.7"
    assert e.user_agent == "NavigateurFictif/1.0"
    # Libellé sans nom : le journal en ajout seul ne peut pas être purgé (loi 09-08).
    assert e.object_repr == f"Candidature #{cand.pk}"
    assert cand.candidat.nom not in e.object_repr


def test_decisions_refusees_non_journalisees(client, direction_user, poste):
    """CA18 — motif invalide, transition interdite, concurrence, droits : aucune entrée
    « decision »."""
    c = new_candidature(poste, S.ELIGIBLE)
    poster(client, direction_user, c, S.PRESELECTIONNEE, "court")
    poster(client, direction_user, c, S.RETENUE)
    poster(client, direction_user, c, S.PRESELECTIONNEE, statut_attendu=S.A_VERIFIER)
    poster(client, make_user("consultant13@example.org", Role.CONSULTANT), c, S.PRESELECTIONNEE)
    client.logout()
    poster(client, None, c, S.PRESELECTIONNEE)
    assert not audits_decision().exists()
    assert_rien_enregistre(c, S.ELIGIBLE)


def test_echec_du_journal_annule_toute_la_decision_service(direction_user, cand):
    """CA19 — erreur à l'écriture du journal : aucune Decision, statut inchangé."""
    with (
        mock.patch("apps.evaluation.services.log", side_effect=RuntimeError("audit en panne")),
        pytest.raises(RuntimeError),
    ):
        record_decision(cand, direction_user, S.PRESELECTIONNEE, MOTIF)
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_echec_du_journal_annule_toute_la_decision_vue(direction_user, cand):
    """CA19 — même scénario par la vue : erreur serveur, rien n'est enregistré."""
    c = Client(raise_request_exception=False)
    c.force_login(direction_user)
    with mock.patch("apps.evaluation.services.log", side_effect=RuntimeError("audit en panne")):
        response = c.post(url_post(cand), payload(cand, S.PRESELECTIONNEE))
    assert response.status_code >= 500
    assert_rien_enregistre(cand, S.ELIGIBLE)


# --------------------------------------------------------------------------------------
# CA20 — concurrence
# --------------------------------------------------------------------------------------


def test_deux_utilisateurs_le_second_est_refuse(direction_user, admin_user, poste):
    """CA20 — deux fiches ouvertes sur « convoquée » : la seconde décision est refusée (« Une autre
    décision a été enregistrée entre-temps »), une seule Decision, statut « retenue »."""
    c = new_candidature(poste, S.CONVOQUEE)
    c1, c2 = Client(), Client()
    c1.force_login(direction_user)
    c2.force_login(admin_user)
    v1 = form_hidden_values(c1.get(url_fiche(c)))
    v2 = form_hidden_values(c2.get(url_fiche(c)))
    assert v1["statut_attendu"] == v2["statut_attendu"] == S.CONVOQUEE
    assert v1["derniere_decision_id"] == v2["derniere_decision_id"] == ""

    r1 = c1.post(
        url_post(c),
        {
            **v1,
            "decision": S.RETENUE,
            "motif": "Meilleure note à l'oral fictif.",
            "confirmer_revision": "1",
            "confirmer_avertissements": "1",
        },
        **HX,
    )
    assert r1.status_code == 200
    r2 = c2.post(
        url_post(c),
        {
            **v2,
            "decision": S.REJETEE,
            "motif": "Désistement fictif annoncé par courriel.",
            "confirmer_revision": "1",
            "confirmer_avertissements": "1",
        },
        **HX,
    )
    assert r2.status_code == 409
    root = parse(r2)
    assert "Une autre décision a été enregistrée entre-temps" in root.text()
    # La section est rafraîchie : la décision du premier utilisateur y figure.
    assert "Meilleure note à l'oral fictif." in root.text()
    assert Decision.objects.filter(candidature=c).count() == 1
    assert statut_en_base(c) == S.RETENUE
    assert audits_decision().count() == 1


def test_concurrence_detectee_par_la_derniere_decision(client, direction_user, admin_user, poste):
    """CA20 / règle 7 — statut revenu à l'identique mais nouvelle décision : refus."""
    c = new_candidature(poste, S.ELIGIBLE)
    perime = payload(c, S.PRESELECTIONNEE)
    record_decision(c, admin_user, S.NON_ELIGIBLE, "Requalification fictive numéro un.")
    record_decision(
        c, admin_user, S.ELIGIBLE, "Requalification fictive numéro deux.", confirmer_revision=True
    )
    assert statut_en_base(c) == S.ELIGIBLE
    client.force_login(direction_user)
    response = client.post(url_post(c), perime, **HX)
    assert response.status_code == 409
    assert "Une autre décision a été enregistrée entre-temps" in texte(response)
    assert_rien_enregistre(c, S.ELIGIBLE, nb_decisions=2)


def test_concurrence_au_niveau_du_service(direction_user, poste):
    """Règle 7 — le service refuse si le statut ou la dernière décision attendus diffèrent."""
    c = new_candidature(poste, S.CONVOQUEE)
    with pytest.raises(DecisionError):
        record_decision(c, direction_user, S.RETENUE, MOTIF, statut_attendu=S.ELIGIBLE)
    with pytest.raises(DecisionError):
        record_decision(c, direction_user, S.RETENUE, MOTIF, derniere_decision_attendue=999999)
    assert_rien_enregistre(c, S.CONVOQUEE)


def test_double_envoi_refuse(client, direction_user, cand):
    """Cas limite — double clic : le second envoi identique est refusé."""
    data = payload(cand, S.PRESELECTIONNEE)
    client.force_login(direction_user)
    assert client.post(url_post(cand), data).status_code == 302
    second = client.post(url_post(cand), data, **HX)
    assert second.status_code in (400, 409)
    assert Decision.objects.filter(candidature=cand).count() == 1


# --------------------------------------------------------------------------------------
# CA21, CA22 — moteur après décision humaine
# --------------------------------------------------------------------------------------


def test_moteur_ne_reclasse_pas_une_decision_eligible(direction_user, poste):
    """CA21 — décision humaine « éligible », moteur « non éligible » : nouvelle vérification,
    statut reste « éligible »."""
    c = new_candidature(poste, S.A_VERIFIER, donnees_declarees=PROFIL_BAC2)
    record_decision(c, direction_user, S.ELIGIBLE, "Équivalence de diplôme fictive admise.")
    avant = VerificationEligibilite.objects.filter(candidature=c).count()
    v = run_eligibility(Candidature.objects.get(pk=c.pk))
    assert v.resultat == S.NON_ELIGIBLE
    assert VerificationEligibilite.objects.filter(candidature=c).count() == avant + 1
    assert statut_en_base(c) == S.ELIGIBLE


def test_moteur_ne_reclasse_pas_une_decision_non_eligible(direction_user, poste):
    """CA21 — décision humaine « non éligible », moteur « éligible » : statut reste
    « non éligible »."""
    c = new_candidature(poste, S.A_VERIFIER, donnees_declarees=PROFIL_BAC5)
    record_decision(c, direction_user, S.NON_ELIGIBLE, "Diplôme non authentifié, fictif.")
    v = run_eligibility(Candidature.objects.get(pk=c.pk))
    assert v.resultat == S.ELIGIBLE
    assert statut_en_base(c) == S.NON_ELIGIBLE


@pytest.mark.parametrize("statut", [S.REJETEE, S.PRESELECTIONNEE])
def test_moteur_ne_reclasse_jamais_apres_decision(direction_user, poste, statut):
    """Règle 9 — quel que soit le statut, une candidature décidée n'est plus reclassée."""
    c = new_candidature(poste, statut, donnees_declarees=PROFIL_BAC2)
    decision_humaine(c, direction_user, statut)
    run_eligibility(Candidature.objects.get(pk=c.pk))
    assert statut_en_base(c) == statut


def test_moteur_classe_toujours_sans_decision(poste):
    """CA22 — sans décision, « à vérifier » + moteur « éligible » : statut « éligible »."""
    c = new_candidature(poste, S.A_VERIFIER, donnees_declarees=PROFIL_BAC5)
    v = run_eligibility(c)
    assert v.resultat == S.ELIGIBLE
    assert statut_en_base(c) == S.ELIGIBLE


def test_aucun_traitement_automatique_ne_cree_de_decision(synced):
    """Règle 1 — la synchronisation et le moteur ne créent jamais de Decision."""
    for c in Candidature.objects.all()[:20]:
        run_eligibility(c)
    assert Decision.objects.count() == 0
    assert not audits_decision().exists()


def test_service_refuse_un_auteur_non_authentifie(cand):
    """Rôles — un processus automatique (sans utilisateur authentifié) ne peut pas décider."""
    for auteur in (None, AnonymousUser()):
        with pytest.raises(DecisionError):
            record_decision(cand, auteur, S.PRESELECTIONNEE, MOTIF)
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_synchronisation_ne_modifie_pas_le_statut_d_une_candidature_decidee(synced, direction_user):
    """Cas limite — une nouvelle synchronisation ne modifie pas le statut d'une candidature ayant
    une décision."""
    from integrations.site_source.sync import sync_from_source

    c = Candidature.objects.exclude(source_id="").exclude(statut=S.REJETEE).first()
    assert c is not None
    cible = sorted(TABLE[c.statut] - {S.RETENUE})[0] if c.statut in TABLE else None
    assert cible is not None, f"statut importé inattendu : {c.statut}"
    record_decision(
        c, direction_user, cible, MOTIF, confirmer_revision=True, confirmer_avertissements=True
    )
    sync_from_source(synced, compute_hashes=False)
    assert statut_en_base(c) == cible


# --------------------------------------------------------------------------------------
# CA23, CA24 — avertissements non bloquants
# --------------------------------------------------------------------------------------


def _metadata_mentionne_avertissement(metadata: dict) -> bool:
    return any("avertissement" in k and v for k, v in metadata.items())


def test_anomalie_haute_demande_confirmation(client, direction_user, cand):
    """CA23 — anomalie haute non résolue + « convoquée » sans confirmation : refus avec
    avertissement ; avec confirmation : acceptée, avertissement confirmé dans metadata."""
    Anomalie.objects.create(
        candidature=cand,
        type=Anomalie.Type.INCOHERENCE_DATES,
        gravite=Anomalie.Gravite.HAUTE,
        description="Dates fictives incohérentes",
    )
    r1 = poster(client, direction_user, cand, S.CONVOQUEE, htmx=True, confirmer=False)
    assert parse(r1).with_attr("data-decision-confirmation")
    assert_rien_enregistre(cand, S.ELIGIBLE)
    with pytest.raises(ConfirmationRequise):
        record_decision(cand, direction_user, S.CONVOQUEE, MOTIF)

    r2 = poster(client, direction_user, cand, S.CONVOQUEE, htmx=True, confirmer=True)
    assert r2.status_code == 200
    assert statut_en_base(cand) == S.CONVOQUEE
    assert _metadata_mentionne_avertissement(audits_decision().get().metadata)


@pytest.mark.parametrize("decision", [S.PRESELECTIONNEE, S.CONVOQUEE])
def test_anomalie_haute_avertit_pour_les_decisions_de_selection(direction_user, cand, decision):
    """Règle 10 — l'avertissement concerne présélectionnée, convoquée (et retenue)."""
    Anomalie.objects.create(
        candidature=cand,
        type=Anomalie.Type.AUTRE,
        gravite=Anomalie.Gravite.HAUTE,
        description="Anomalie fictive haute",
    )
    with pytest.raises(ConfirmationRequise):
        record_decision(cand, direction_user, decision, MOTIF)
    assert_rien_enregistre(cand, S.ELIGIBLE)


def test_anomalie_haute_n_avertit_pas_pour_un_rejet(direction_user, cand):
    """Règle 10 — « rejetée » n'est pas concernée par l'avertissement d'anomalie."""
    Anomalie.objects.create(
        candidature=cand,
        type=Anomalie.Type.AUTRE,
        gravite=Anomalie.Gravite.HAUTE,
        description="Anomalie fictive haute",
    )
    record_decision(cand, direction_user, S.REJETEE, MOTIF)
    assert statut_en_base(cand) == S.REJETEE


@pytest.mark.parametrize(
    ("gravite", "resolue"),
    [(Anomalie.Gravite.HAUTE, True), (Anomalie.Gravite.MOYENNE, False)],
)
def test_anomalie_resolue_ou_non_haute_sans_avertissement(direction_user, cand, gravite, resolue):
    """Règle 10 — anomalie résolue ou de gravité inférieure : pas de confirmation demandée."""
    Anomalie.objects.create(
        candidature=cand,
        type=Anomalie.Type.AUTRE,
        gravite=gravite,
        description="Anomalie fictive",
        resolue=resolue,
    )
    record_decision(cand, direction_user, S.CONVOQUEE, MOTIF)
    assert statut_en_base(cand) == S.CONVOQUEE


def test_quota_de_postes_atteint_demande_confirmation(client, direction_user, concours):
    """CA24 — nombre_postes = 1 et une candidature déjà retenue : avertissement, puis acceptée et
    tracée avec confirmation."""
    poste1 = PosteConcours.objects.create(
        concours=concours, intitule="Poste unique", nombre_postes=1
    )
    new_candidature(poste1, S.RETENUE)
    c = new_candidature(poste1, S.CONVOQUEE)
    r1 = poster(client, direction_user, c, S.RETENUE, htmx=True, confirmer=False)
    assert parse(r1).with_attr("data-decision-confirmation")
    assert_rien_enregistre(c, S.CONVOQUEE)

    r2 = poster(client, direction_user, c, S.RETENUE, htmx=True, confirmer=True)
    assert r2.status_code == 200
    assert statut_en_base(c) == S.RETENUE
    assert _metadata_mentionne_avertissement(audits_decision().get().metadata)


def test_quota_non_atteint_sans_avertissement(direction_user, concours):
    """CA24 (contrôle) — quota non atteint : « retenue » sans confirmation."""
    poste2 = PosteConcours.objects.create(
        concours=concours, intitule="Deux postes", nombre_postes=2
    )
    new_candidature(poste2, S.RETENUE)
    c = new_candidature(poste2, S.CONVOQUEE)
    record_decision(c, direction_user, S.RETENUE, MOTIF)
    assert statut_en_base(c) == S.RETENUE


def test_quota_atteint_sur_une_offre_privee(direction_user, org):
    """CA24 / Q9 — même avertissement pour une offre privée."""
    offre = OffreEmploi.objects.create(organisation=org, intitule="Offre fictive", nombre_postes=1)
    new_candidature(None, S.RETENUE, offre=offre)
    c = new_candidature(None, S.CONVOQUEE, offre=offre)
    with pytest.raises(ConfirmationRequise):
        record_decision(c, direction_user, S.RETENUE, MOTIF)
    record_decision(c, direction_user, S.RETENUE, MOTIF, confirmer_avertissements=True)
    assert statut_en_base(c) == S.RETENUE


# --------------------------------------------------------------------------------------
# CA25 — concours terminé
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_user", ["direction_user", "admin_user"])
def test_concours_termine_aucune_decision(client, request, concours, poste, fixture_user):
    """CA25 — concours « Terminé » : pas de formulaire sur la fiche, POST refusé (direction et
    admin, Q3), rien n'est enregistré."""
    user = request.getfixturevalue(fixture_user)
    c = new_candidature(poste, S.CONVOQUEE)
    Concours.objects.filter(pk=concours.pk).update(statut=Concours.Statut.TERMINE)
    client.force_login(user)
    fiche = client.get(url_fiche(c))
    assert fiche.status_code == 200
    root = parse(fiche)
    assert not root.with_attr("data-decision-form")
    assert root.with_attr("data-decision-indisponible")
    response = poster(client, user, c, S.RETENUE, htmx=True)
    assert response.status_code == 400
    assert_rien_enregistre(c, S.CONVOQUEE)
    with pytest.raises(DecisionError):
        record_decision(
            c, user, S.RETENUE, MOTIF, confirmer_revision=True, confirmer_avertissements=True
        )
    assert_rien_enregistre(c, S.CONVOQUEE)


# --------------------------------------------------------------------------------------
# CA26 à CA28 — interface
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("statut", list(TABLE))
def test_decisions_proposees_selon_le_statut(client, direction_user, poste, statut):
    """CA26 / règle 12 — la liste ne propose que les décisions permises (libellés français)."""
    c = new_candidature(poste, statut)
    client.force_login(direction_user)
    options = options_proposees(client.get(url_fiche(c)))
    assert {v for v, _ in options} == {d.value for d in TABLE[statut]}
    for value, label in options:
        assert label == LIBELLES[value]


def test_eligible_propose_exactement_quatre_decisions(client, direction_user, cand):
    """CA26 — « éligible » : exactement Non éligible, Présélectionnée, Convoquée, Rejetée."""
    client.force_login(direction_user)
    labels = [label for _, label in options_proposees(client.get(url_fiche(cand)))]
    assert sorted(labels) == sorted(["Non éligible", "Présélectionnée", "Convoquée", "Rejetée"])


def test_formulaire_de_la_fiche(client, direction_user, cand):
    """Règle 12 — motif, statut et dernière décision transmis, bouton « Enregistrer la décision »,
    aide sur les données sensibles."""
    client.force_login(direction_user)
    response = client.get(url_fiche(cand))
    root = parse(response)
    form = one_node(root, "data-decision-form")
    assert form.attrs.get("method", "").lower() == "post"
    assert [n for n in form.iter() if n.tag == "textarea" and n.attrs.get("name") == "motif"]
    hidden = form_hidden_values(response)
    assert hidden["statut_attendu"] == S.ELIGIBLE
    assert hidden["derniere_decision_id"] == ""
    assert "Enregistrer la décision" in form.text()
    assert "Indiquez des faits liés au dossier, sans donnée sensible" in form.text()


def one_node(root, attr):
    nodes = root.with_attr(attr)
    assert len(nodes) == 1, f"{attr} : {len(nodes)} éléments"
    return nodes[0]


def test_statut_inattendu_aucune_decision_proposee(client, direction_user, poste):
    """Cas limite — statut importé inattendu : aucune décision proposée, message dédié."""
    c = new_candidature(poste, S.ELIGIBLE)
    Candidature.objects.filter(pk=c.pk).update(statut="statut_importe")
    client.force_login(direction_user)
    response = client.get(url_fiche(c))
    assert response.status_code == 200
    root = parse(response)
    assert "Statut non pris en charge, contactez un administrateur" in root.text()
    selects = [n for n in root.iter() if n.tag == "select" and n.attrs.get("name") == "decision"]
    for sel in selects:
        assert not [o for o in sel.iter() if o.tag == "option" and o.attrs.get("value")]
    r = poster(client, direction_user, c, S.ELIGIBLE)
    assert r.status_code == 400
    assert Decision.objects.filter(candidature=c).count() == 0


def test_htmx_succes_renvoie_fragment_a_jour(client, direction_user, cand):
    """CA27 — HTMX accepté : fragment avec nouveau statut (OOB) et historique à jour."""
    response = poster(client, direction_user, cand, S.CONVOQUEE, htmx=True, confirmer=False)
    assert response.status_code == 200
    html = response.content.decode()
    assert "<html" not in html.lower()
    root = parse(html)
    assert root.with_attr("data-decision-succes")
    statut = root.with_attr("id", "statut-candidature")
    assert len(statut) == 1
    assert "hx-swap-oob" in statut[0].attrs
    assert "Convoquée" in statut[0].text()
    section = root.with_attr("id", "decisions")
    assert len(section) == 1
    assert MOTIF in section[0].text()


def test_htmx_motif_invalide_reaffiche_le_formulaire(client, direction_user, cand):
    """CA27 / règle 12 — HTMX refusé (motif) : formulaire réaffiché avec message et motif saisi."""
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, "Trop bref", htmx=True)
    assert response.status_code == 400
    html = response.content.decode()
    assert "<html" not in html.lower()
    root = parse(html)
    assert root.with_attr("data-decision-erreur")
    assert MESSAGE_MOTIF in root.text()
    form = one_node(root, "data-decision-form")
    textareas = [n for n in form.iter() if n.tag == "textarea" and n.attrs.get("name") == "motif"]
    assert textareas and textareas[0].text() == "Trop bref"


def test_post_classique_redirige_vers_la_fiche(client, direction_user, cand):
    """CA28 — POST sans HX-Request accepté : 302 vers la fiche."""
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, confirmer=False)
    assert response.status_code == 302
    assert response.url == url_fiche(cand)


def test_motif_html_echappe_a_l_affichage(client, direction_user, cand):
    """Cas limite — motif contenant du HTML : stocké tel quel, échappé à l'affichage."""
    motif = "<script>alert('x')</script> motif fictif"
    poster(client, direction_user, cand, S.PRESELECTIONNEE, motif, confirmer=False)
    assert Decision.objects.get(candidature=cand).motif == motif
    fiche = client.get(url_fiche(cand)).content.decode()
    assert "<script>alert('x')</script>" not in fiche
    assert "&lt;script&gt;" in fiche


# --------------------------------------------------------------------------------------
# CA29 — administration et ORM
# --------------------------------------------------------------------------------------


def test_admin_django_lecture_seule_pour_les_decisions(client, admin_user, cand):
    """CA29 — pas d'ajout (403), ni modification, ni suppression depuis l'administration."""
    d = record_decision(cand, admin_user, S.PRESELECTIONNEE, MOTIF)
    model_admin = admin.site._registry[Decision]
    request = RequestFactory().get("/")
    request.user = admin_user
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request, d) is False
    assert model_admin.has_delete_permission(request, d) is False

    client.force_login(admin_user)
    assert client.get(reverse("admin:evaluation_decision_add")).status_code == 403
    assert client.get(reverse("admin:evaluation_decision_delete", args=[d.pk])).status_code == 403
    assert (
        client.post(
            reverse("admin:evaluation_decision_delete", args=[d.pk]), {"post": "yes"}
        ).status_code
        == 403
    )
    client.post(
        reverse("admin:evaluation_decision_change", args=[d.pk]),
        {"candidature": cand.pk, "auteur": admin_user.pk, "decision": S.REJETEE, "motif": "x" * 20},
    )
    d.refresh_from_db()
    assert (d.decision, d.motif) == (S.PRESELECTIONNEE, MOTIF)
    assert Decision.objects.filter(pk=d.pk).exists()


def test_orm_refuse_la_modification_d_une_decision(direction_user, cand):
    """CA29 — via l'ORM, save() sur une décision existante lève une erreur."""
    d = record_decision(cand, direction_user, S.PRESELECTIONNEE, MOTIF)
    d.motif = "Motif réécrit après coup, interdit."
    with pytest.raises(Exception):  # noqa: B017 — la spec n'impose pas le type d'erreur
        d.save()
    d.refresh_from_db()
    assert d.motif == MOTIF


# Relecture conformité KAN-13 — concurrence moteur, refus journalisés (Q6), htmx
# --------------------------------------------------------------------------------------


def test_moteur_avec_instance_perimee_ne_ecrase_pas_une_decision(direction_user, poste):
    """Règle 9 — le moteur reçoit une instance chargée avant la décision humaine (cas de
    ``sync_site --evaluate``) : il relit la ligne verrouillée et ne reclasse pas."""
    c = new_candidature(poste, S.A_VERIFIER, donnees_declarees=PROFIL_BAC2)
    perimee = Candidature.objects.get(pk=c.pk)
    record_decision(c, direction_user, S.ELIGIBLE, "Équivalence de diplôme fictive admise.")
    assert perimee.statut == S.A_VERIFIER
    run_eligibility(perimee)
    assert statut_en_base(c) == S.ELIGIBLE
    assert perimee.statut == S.ELIGIBLE


def test_moteur_reclasse_avec_libelle_sans_nom(poste):
    c = new_candidature(poste, S.RECUE, donnees_declarees=PROFIL_BAC2)
    run_eligibility(c)
    e = JournalAudit.objects.get(action=AuditAction.UPDATE, object_id=str(c.pk))
    assert e.object_repr == f"Candidature #{c.pk}"


def refus():
    return JournalAudit.objects.filter(action=AuditAction.UPDATE, metadata__has_key="refus")


@pytest.mark.parametrize("htmx", [False, True])
def test_refus_de_droits_journalise(client, cand, htmx):
    """Q6 — un 403 sur l'envoi d'une décision laisse une trace sans nom."""
    user = make_user(f"consultant-refus-{int(htmx)}@example.org", Role.CONSULTANT)
    response = poster(client, user, cand, S.PRESELECTIONNEE, htmx=htmx)
    assert response.status_code == 403
    e = refus().get()
    assert e.metadata["refus"] == "droits"
    assert e.actor == user
    assert e.object_repr == f"Candidature #{cand.pk}"


def test_transition_interdite_journalisee_sans_motif(client, direction_user, cand):
    """Q6 — transition interdite envoyée directement : trace, sans recopier le motif."""
    response = poster(client, direction_user, cand, S.RETENUE)
    assert response.status_code == 400
    e = refus().get()
    assert e.metadata["refus"] == "transition_interdite"
    assert e.metadata["decision"] == S.RETENUE
    assert MOTIF not in str(e.metadata)
    assert cand.candidat.nom not in e.object_repr


def test_erreur_de_saisie_non_journalisee(client, direction_user, cand):
    """Q6 — un motif trop court est une erreur de saisie : pas de trace de refus."""
    poster(client, direction_user, cand, S.PRESELECTIONNEE, "court")
    assert not refus().exists()


def test_fiche_reaffichee_sans_js_est_une_consultation(client, direction_user, cand):
    """§5 — la fiche complète renvoyée après un refus sans JavaScript est journalisée."""
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, "court")
    assert response.status_code == 400
    assert JournalAudit.objects.filter(action=AuditAction.VIEW, object_id=str(cand.pk)).exists()


def test_fragment_htmx_n_est_pas_une_consultation(client, direction_user, cand):
    poster(client, direction_user, cand, S.PRESELECTIONNEE, "court", htmx=True)
    assert not JournalAudit.objects.filter(action=AuditAction.VIEW).exists()


def test_chiffre_unicode_dans_derniere_decision_pas_d_erreur_500(client, direction_user, cand):
    response = poster(client, direction_user, cand, S.PRESELECTIONNEE, derniere="²")
    assert response.status_code in (302, 400, 409)


def test_remplacement_sur_erreur_limite_au_formulaire_de_decision(client, direction_user, cand):
    """La configuration htmx globale ne remplace plus la cible sur 400/409 ; seul le
    formulaire de décision (data-decision-form) le fait, via un script statique."""
    client.force_login(direction_user)
    html = client.get(url_fiche(cand)).content.decode()
    assert "responseHandling" not in html
    assert "js/decision.js" in html
    assert "data-decision-form" in html
