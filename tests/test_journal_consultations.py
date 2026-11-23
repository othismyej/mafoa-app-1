"""Tests d'acceptation de KAN-14 — Journal des consultations et écran « Journal d'audit ».

Les attentes sont fondées sur la spécification ``docs/specs/KAN-14-journal-consultations.md``.
Toutes les données sont fictives (domaines mafoa.test / exemple.test, IP de documentation
RFC 5737). Aucun appel IA ni accès au stockage de fichiers.
"""

import csv
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.audit import exports
from apps.audit.models import AuditAction, JournalAudit
from apps.audit.services import consultation_repr, log_access_denied, log_consultation
from apps.documents.models import Extraction
from apps.evaluation.models import (
    Anomalie,
    Decision,
    GrilleEvaluation,
    Score,
    Verdict,
    VerificationEligibilite,
)
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    Organisation,
    Piece,
    PosteConcours,
)

from .conftest import make_user
from .test_candidature_detail import parse
from .test_decision_humaine import payload

pytestmark = pytest.mark.django_db

S = Candidature.Statut
IP = "203.0.113.10"
IP2 = "198.51.100.5"
UA = "Navigateur-Fictif/1.0 (KAN-14)"
REQ = {"REMOTE_ADDR": IP, "HTTP_USER_AGENT": UA}
HX = {"HTTP_HX_REQUEST": "true"}
NOMINATIF = ("BENALI", "Yasmine", "ZZ999001", "y.benali@exemple.test")
MOTIF = "Dossier complet et conforme aux critères du poste."
CASA = ZoneInfo("Africa/Casablanca")

# --------------------------------------------------------------------------------------
# Jeu de données fictif
# --------------------------------------------------------------------------------------


@pytest.fixture
def admin1(db):
    return make_user("admin1@mafoa.test", Role.ADMIN)


@pytest.fixture
def dir1(db):
    return make_user("dir1@mafoa.test", Role.DIRECTION)


@pytest.fixture
def dir2(db):
    user = make_user("dir2@mafoa.test", Role.DIRECTION)
    user.is_active = False
    user.save(update_fields=["is_active"])
    return user


@pytest.fixture
def cons1(db):
    return make_user("cons1@mafoa.test", Role.CONSULTANT)


@pytest.fixture
def poste(db):
    org = Organisation.objects.create(nom="Agence Fictive Draa", type=Organisation.Type.PUBLIC)
    concours = Concours.objects.create(organisation=org, intitule="Concours fictif Draa 2026")
    return PosteConcours.objects.create(
        concours=concours, intitule="Ingénieur réseaux", reference="P-14", nombre_postes=2
    )


@pytest.fixture
def candidat(db):
    return Candidat.objects.create(
        nom="BENALI",
        prenom="Yasmine",
        email="y.benali@exemple.test",
        telephone="+212600000914",
        cin="ZZ999001",
    )


_seq = {"n": 0}


def new_candidature(poste, candidat=None, statut=S.ELIGIBLE) -> Candidature:
    if candidat is None:
        _seq["n"] += 1
        candidat = Candidat.objects.create(
            nom=f"Fictif{_seq['n']}", prenom="Test", email=f"kan14-{_seq['n']}@exemple.test"
        )
    return Candidature.objects.create(
        candidat=candidat,
        poste_concours=poste,
        date_candidature=timezone.make_aware(datetime(2026, 3, 15, 10, 30)),
        statut=statut,
    )


@pytest.fixture
def c42(poste, candidat):
    """Le dossier « 42 » des critères d'acceptation (identifiant réel attribué par la base)."""
    return new_candidature(poste, candidat)


@pytest.fixture
def c4(poste):
    return new_candidature(poste)


@pytest.fixture
def c420(poste):
    return new_candidature(poste)


@pytest.fixture
def piece7(c42):
    return Piece.objects.create(
        candidature=c42, type=Piece.Type.DIPLOME, nom_fichier="diplome.pdf", storage_key="x"
    )


def fiche_url(c) -> str:
    return reverse("recruitment:candidature_detail", args=[getattr(c, "pk", c)])


def journal_url(**params) -> str:
    base = reverse("audit:journal_liste")
    if not params:
        return base
    from urllib.parse import urlencode

    return f"{base}?{urlencode(params, doseq=True)}"


def panneau_url(c) -> str:
    return reverse("audit:consultations_dossier", args=[getattr(c, "pk", c)])


def export_url(fmt: str, **params) -> str:
    from urllib.parse import urlencode

    return f"{reverse('audit:journal_export', args=[fmt])}?{urlencode(params, doseq=True)}"


def get(client, user, path, **extra):
    if user is not None:
        client.force_login(user)
    return client.get(path, **{**REQ, **extra})


def views():
    return JournalAudit.objects.filter(action=AuditAction.VIEW)


def denied():
    return JournalAudit.objects.filter(action=AuditAction.ACCESS_DENIED)


def entree(ts=None, **kw) -> JournalAudit:
    """Entrée du journal créée directement, éventuellement à un horodatage donné."""
    kw.setdefault("action", AuditAction.VIEW)
    if kw.get("actor") is not None:
        kw.setdefault("actor_repr", kw["actor"].email)
    obj = kw.pop("obj", None)
    if obj is not None:
        kw["content_type"] = ContentType.objects.get_for_model(obj)
        kw["object_id"] = str(obj.pk)
        kw.setdefault("object_repr", consultation_repr(obj))
    if ts is None:
        return JournalAudit.objects.create(**kw)
    with mock.patch("django.utils.timezone.now", return_value=ts):
        return JournalAudit.objects.create(**kw)


def consultation(c, actor, ip=IP, **meta) -> JournalAudit:
    return entree(
        obj=c,
        actor=actor,
        ip_address=ip,
        metadata={"vue": "fiche", "candidature_id": c.pk, "candidat_id": c.candidat_id, **meta},
    )


def rows_ids(response) -> list[int]:
    return [int(n.attrs["data-entree"]) for n in parse(response).with_attr("data-entree")]


def local_dt(*args) -> datetime:
    return datetime(*args, tzinfo=CASA)


# --------------------------------------------------------------------------------------
# A. Règle commune (CA1 à CA10)
# --------------------------------------------------------------------------------------


def test_ca1_ouverture_de_fiche_journalisee(client, dir1, c42):
    response = get(client, dir1, fiche_url(c42))
    assert response.status_code == 200
    entries = list(views())
    assert len(entries) == 1
    e = entries[0]
    assert e.actor == dir1 and e.actor_repr == "dir1@mafoa.test"
    assert e.content_type == ContentType.objects.get_for_model(Candidature)
    assert e.object_id == str(c42.pk)
    assert e.object_repr == f"Candidature #{c42.pk}"
    assert e.ip_address == IP
    assert e.user_agent == UA
    assert e.before is None and e.after is None
    assert e.metadata["vue"] == "fiche"
    assert e.metadata["candidature_id"] == c42.pk
    assert e.metadata["candidat_id"] == c42.candidat_id


def test_ca2_aucune_donnee_nominative_dans_les_consultations(client, dir1, admin1, c42, piece7):
    get(client, dir1, fiche_url(c42))  # CA1
    client.post(  # CA6 : refus sans JavaScript
        reverse("recruitment:decision_enregistrer", args=[c42.pk]),
        payload(c42, S.PRESELECTIONNEE, "Trop"),
        **REQ,
    )
    request = RequestFactory().get("/pieces/x/", **REQ)  # CA8 : appel direct (KAN-12)
    request.user = dir1
    log_consultation(request, piece7, "piece", mode="apercu")
    # Pages d'administration (CA9) : détail du candidat et de la candidature.
    get(client, admin1, reverse("admin:recruitment_candidat_change", args=[c42.candidat_id]))
    get(client, admin1, reverse("admin:recruitment_candidature_change", args=[c42.pk]))
    entries = list(views())
    assert len(entries) >= 5
    for e in entries:
        blob = f"{e.object_repr} {e.metadata}"
        for valeur in NOMINATIF:
            assert valeur not in blob, (valeur, e.metadata)


def test_ca3_pas_de_dedoublonnage(client, dir1, c42):
    get(client, dir1, fiche_url(c42))
    get(client, dir1, fiche_url(c42))
    get(client, dir1, fiche_url(c42), HTTP_HX_HISTORY_RESTORE_REQUEST="true", **HX)
    assert views().filter(metadata__vue="fiche").count() == 3


def test_ca4_ni_redirection_ni_404_ni_405_ne_journalisent(client, dir1, c42):
    response = client.get(fiche_url(c42), **REQ)
    assert response.status_code == 302
    client.force_login(dir1)
    assert client.get(fiche_url(999999), **REQ).status_code == 404
    assert client.post(fiche_url(c42), **REQ).status_code == 405
    assert not views().exists()


def test_ca5_refus_consultant_trace_et_persistant(client, cons1, c42):
    response = get(client, cons1, fiche_url(c42))
    assert response.status_code == 403
    for valeur in NOMINATIF:
        assert valeur not in response.content.decode()
    assert not views().exists()
    entries = list(denied())
    assert len(entries) == 1
    e = entries[0]
    assert e.actor == cons1
    assert e.metadata["chemin"] == f"/candidatures/{c42.pk}/"
    assert e.metadata["candidature_id"] == c42.pk
    assert e.object_repr == f"Candidature #{c42.pk}"
    assert e.ip_address == IP and e.user_agent == UA


def test_ca6_reaffichage_apres_decision_et_htmx(client, dir1, c42):
    client.force_login(dir1)
    url = reverse("recruitment:decision_enregistrer", args=[c42.pk])
    response = client.post(url, payload(c42, S.PRESELECTIONNEE, "Trop"), **REQ)
    assert response.status_code >= 400
    v = list(views())
    assert len(v) == 1
    assert v[0].metadata["vue"] == "fiche_apres_decision"
    assert v[0].metadata["candidature_id"] == c42.pk

    response = client.post(url, payload(c42, S.PRESELECTIONNEE, MOTIF), **{**REQ, **HX})
    assert response.status_code == 200
    assert Decision.objects.filter(candidature=c42).count() == 1
    assert JournalAudit.objects.filter(action=AuditAction.DECISION).count() == 1
    assert views().count() == 1  # aucune nouvelle consultation


def test_ca7_echec_d_ecriture_aucune_donnee_servie(dir1, c42):
    client = Client(raise_request_exception=False)
    client.force_login(dir1)
    with mock.patch("apps.audit.services.log", side_effect=RuntimeError("journal indisponible")):
        response = client.get(fiche_url(c42), **REQ)
    assert response.status_code == 500
    content = response.content.decode()
    assert "BENALI" not in content and "ZZ999001" not in content


def test_ca8_consultation_de_piece_par_appel_direct(dir1, c42, piece7):
    """CA8, première moitié : la règle commune pour une pièce (la vue est livrée par KAN-12)."""
    request = RequestFactory().get(f"/pieces/{piece7.pk}/", **REQ)
    request.user = dir1
    e = log_consultation(request, piece7, "piece", mode="apercu")
    assert e.action == AuditAction.VIEW
    assert e.content_type == ContentType.objects.get_for_model(Piece)
    assert e.object_id == str(piece7.pk)
    assert e.object_repr == f"Pièce #{piece7.pk} (Candidature #{c42.pk})"
    meta = dict(e.metadata)
    assert meta.pop("candidat_id", c42.candidat_id) == c42.candidat_id  # facultatif (connu)
    assert meta == {
        "vue": "piece",
        "candidature_id": c42.pk,
        "piece_id": piece7.pk,
        "mode": "apercu",
    }
    assert e.ip_address == IP and e.actor == dir1


@pytest.mark.skip(reason="dépend de KAN-12 (vue qui sert une pièce, requêtes Range)")
def test_ca8_requete_range_de_la_meme_ouverture_non_journalisee():
    pass


def test_log_consultation_refuse_les_appels_invalides(dir1, c42, piece7):
    request = RequestFactory().get("/", **REQ)
    request.user = dir1
    with pytest.raises(ValueError):
        log_consultation(request, c42, "inconnue")
    with pytest.raises(ValueError):
        log_consultation(request, piece7, "piece")  # mode manquant
    with pytest.raises(ValueError):
        log_consultation(request, None, "admin_liste")  # object_repr manquant
    assert not views().exists()


def test_fragment_htmx_porte_nom_et_indicateur(dir1, c42):
    request = RequestFactory().get("/", **REQ)
    request.user = dir1
    e = log_consultation(request, c42, "fragment", fragment="historique_verifications")
    assert e.metadata["htmx"] is True
    assert e.metadata["fragment"] == "historique_verifications"
    assert e.metadata["candidature_id"] == c42.pk


def _objets_admin(c42, piece7, admin1):
    extraction = Extraction.objects.create(piece=piece7, methode=Extraction.Methode.PDF_TEXTE)
    verification = VerificationEligibilite.objects.create(
        candidature=c42, resultat=Verdict.A_VERIFIER, details={}, moteur_version="1.0.0"
    )
    grille = GrilleEvaluation.objects.create(nom="Grille fictive")
    score = Score.objects.create(
        candidature=c42, grille=grille, note_globale=12, origine=Score.Origine.values[0]
    )
    anomalie = Anomalie.objects.create(
        candidature=c42,
        type=Anomalie.Type.PIECE_MANQUANTE,
        gravite=Anomalie.Gravite.INFO,
        description="Pièce fictive manquante.",
    )
    decision = Decision.objects.create(
        candidature=c42, auteur=admin1, decision=S.PRESELECTIONNEE, motif=MOTIF
    )
    return {
        "candidat": (c42.candidat, f"Candidat #{c42.candidat_id}", None),
        "candidature": (c42, f"Candidature #{c42.pk}", c42.pk),
        "piece": (piece7, f"Pièce #{piece7.pk} (Candidature #{c42.pk})", c42.pk),
        "extraction": (extraction, f"Extraction #{extraction.pk}", c42.pk),
        "verificationeligibilite": (
            verification,
            f"Vérification d'éligibilité #{verification.pk}",
            c42.pk,
        ),
        "score": (score, f"Score #{score.pk}", c42.pk),
        "anomalie": (anomalie, f"Anomalie #{anomalie.pk}", c42.pk),
        "decision": (decision, f"Décision #{decision.pk}", c42.pk),
    }


@pytest.mark.parametrize(
    "model",
    [
        "candidat",
        "candidature",
        "piece",
        "extraction",
        "verificationeligibilite",
        "score",
        "anomalie",
        "decision",
    ],
)
def test_ca9_detail_administration_journalise(client, admin1, c42, piece7, model):
    obj, repr_attendu, candidature_id = _objets_admin(c42, piece7, admin1)[model]
    url = reverse(f"admin:{obj._meta.app_label}_{model}_change", args=[obj.pk])
    response = get(client, admin1, url)
    assert response.status_code == 200
    entries = list(views().filter(metadata__vue="admin_detail"))
    assert len(entries) == 1
    e = entries[0]
    assert e.content_type == ContentType.objects.get_for_model(obj)
    assert e.object_id == str(obj.pk)
    assert e.object_repr == repr_attendu
    assert e.metadata.get("candidature_id") == candidature_id
    for valeur in NOMINATIF:
        assert valeur not in f"{e.object_repr} {e.metadata}"


def test_ca9_historique_administration_journalise(client, admin1, c42):
    url = reverse("admin:recruitment_candidature_history", args=[c42.pk])
    assert get(client, admin1, url).status_code == 200
    e = views().get(metadata__vue="admin_detail")
    assert e.metadata["page_admin"] == "historique"
    assert e.metadata["candidature_id"] == c42.pk


def test_ca10_liste_administration_journalisee(client, admin1, c42):
    url = reverse("admin:recruitment_candidat_changelist") + "?q=benali"
    assert get(client, admin1, url).status_code == 200
    e = views().get(metadata__vue="admin_liste")
    assert e.content_type is None and e.object_id == ""
    assert e.object_repr == "Administration : candidats"
    assert e.metadata["recherche"] == "benali"
    assert e.metadata["nb_resultats"] == 1
    assert e.metadata["page"] == 1


def test_admin_post_ne_cree_pas_de_consultation(client, admin1, c42):
    client.force_login(admin1)
    url = reverse("admin:recruitment_candidature_change", args=[c42.pk])
    client.post(url, {}, **REQ)
    assert not views().filter(metadata__vue="admin_detail").exists()


def test_admin_du_journal_journalise_comme_l_ecran(client, admin1):
    e = entree(action=AuditAction.LOGIN, actor=admin1)
    get(client, admin1, reverse("admin:audit_journalaudit_changelist"))
    get(client, admin1, reverse("admin:audit_journalaudit_change", args=[e.pk]))
    assert views().filter(metadata__vue="journal_liste").count() == 1
    assert views().filter(metadata__vue="journal_detail").count() == 1


# --------------------------------------------------------------------------------------
# B. Écran du journal : accès (CA11 à CA14)
# --------------------------------------------------------------------------------------


def test_ca11_liste_par_defaut(client, admin1, dir1, c42):
    ancienne = entree(ts=timezone.now() - timezone.timedelta(days=40), actor=dir1)
    for _ in range(60):
        consultation(c42, dir1)
    response = get(client, admin1, journal_url())
    assert response.status_code == 200
    ids = rows_ids(response)
    assert len(ids) == 50
    assert ids == sorted(ids, reverse=True)
    assert ancienne.pk not in ids
    root = parse(response)
    texte = root.text()
    assert "Consultation" in texte  # libellé français de l'action
    assert f"Candidature #{c42.pk}" in texte
    assert IP in texte
    assert "Détail" in texte
    assert root.with_attr("data-pagination")


@pytest.mark.parametrize("who", ["dir1", "cons1"])
@pytest.mark.parametrize("cible", ["liste", "detail", "export", "panneau"])
def test_ca12_journal_interdit_hors_admin(client, request, admin1, c42, who, cible):
    user = request.getfixturevalue(who)
    e = consultation(c42, admin1)
    url = {
        "liste": journal_url(),
        "detail": reverse("audit:journal_detail", args=[e.pk]),
        "export": export_url("csv"),
        "panneau": panneau_url(c42),
    }[cible]
    response = get(client, user, url)
    assert response.status_code == 403
    assert b"data-entree" not in response.content
    assert b"data-consultation" not in response.content
    assert "attachment" not in response.get("Content-Disposition", "")
    refus = list(denied().filter(actor=user))
    assert len(refus) == 1
    assert refus[0].metadata["chemin"] == url.split("?")[0]
    if cible == "panneau":
        assert refus[0].metadata["candidature_id"] == c42.pk
    assert not views().filter(actor=user).exists()


def test_ca13_non_connecte_redirige(client):
    url = journal_url()
    response = client.get(url)
    assert response.status_code == 302
    assert "next=" in response["Location"]
    assert not denied().exists()


def test_ca14_lien_de_navigation_admin_seulement(client, admin1, dir1, c42):
    response = get(client, admin1, reverse("dashboard"))
    links = parse(response).with_attr("data-nav-journal")
    assert len(links) == 1 and links[0].attrs["href"] == journal_url()
    client.logout()
    for url in (reverse("dashboard"), fiche_url(c42)):
        response = get(client, dir1, url)
        assert response.status_code == 200
        assert b"data-nav-journal" not in response.content
        assert f'href="{journal_url()}'.encode() not in response.content


# --------------------------------------------------------------------------------------
# Écran du journal : filtres et liste (CA15 à CA26)
# --------------------------------------------------------------------------------------


def test_ca15_periode_bornes_incluses(client, admin1):
    e1 = entree(ts=local_dt(2026, 11, 1, 10, 0), action=AuditAction.SYNC)
    e2 = entree(ts=local_dt(2026, 11, 15, 0, 0), action=AuditAction.SYNC)
    e3 = entree(ts=local_dt(2026, 11, 20, 23, 59, 59), action=AuditAction.SYNC)
    e4 = entree(ts=local_dt(2026, 11, 21, 0, 0), action=AuditAction.SYNC)
    ids = rows_ids(get(client, admin1, journal_url(du="2026-11-15", au="2026-11-20")))
    assert e2.pk in ids and e3.pk in ids
    assert e1.pk not in ids and e4.pk not in ids


def test_ca16_filtre_acteur(client, admin1, dir1, dir2):
    a = entree(actor=dir1, action=AuditAction.SYNC)
    b = entree(actor=dir2, action=AuditAction.SYNC)
    c = entree(actor_repr="système", action=AuditAction.SYNC)
    response = get(client, admin1, journal_url(acteur=dir2.pk))
    assert rows_ids(response) == [b.pk]
    acteur = parse(response).with_attr("data-col", "acteur")[0].text()
    assert "dir2@mafoa.test" in acteur and "(désactivé)" in acteur
    ids = rows_ids(get(client, admin1, journal_url(acteur="aucun")))
    assert c.pk in ids and a.pk not in ids and b.pk not in ids
    assert all(e.actor_id is None for e in JournalAudit.objects.filter(pk__in=ids))


def test_ca17_filtre_actions_multiples(client, admin1, dir1, c42):
    v = consultation(c42, dir1)
    d = entree(action=AuditAction.DECISION, actor=dir1, obj=c42)
    lo = entree(action=AuditAction.LOGIN, actor=dir1)
    ids = rows_ids(get(client, admin1, journal_url(action=["view", "decision"])))
    assert v.pk in ids and d.pk in ids and lo.pk not in ids


def test_ca18_filtre_type_objet(client, admin1, dir1, c42, poste):
    v = consultation(c42, dir1)
    vc = entree(actor=dir1, obj=poste.concours, object_repr="Concours fictif")
    ids = rows_ids(get(client, admin1, journal_url(type="candidature")))
    assert v.pk in ids and vc.pk not in ids
    ct = ContentType.objects.get_for_model(Candidature)
    assert all(e.content_type_id == ct.pk for e in JournalAudit.objects.filter(pk__in=ids))


def _entrees_dossiers(dir1, c42, c4, c420, piece7):
    fiche = consultation(c42, dir1)
    piece = entree(
        obj=piece7,
        actor=dir1,
        metadata={
            "vue": "piece",
            "candidature_id": c42.pk,
            "piece_id": piece7.pk,
            "mode": "apercu",
        },
    )
    decision = entree(action=AuditAction.DECISION, actor=dir1, obj=c42, metadata={})
    refus = entree(
        action=AuditAction.UPDATE,
        actor=dir1,
        metadata={"refus": "droits", "candidature_id": c42.pk},
    )
    autres = [consultation(c4, dir1), consultation(c420, dir1)]
    return [fiche, piece, decision, refus], autres


def test_ca19_filtre_dossier(client, admin1, dir1, c42, c4, c420, piece7):
    attendues, autres = _entrees_dossiers(dir1, c42, c4, c420, piece7)
    ids = rows_ids(get(client, admin1, journal_url(dossier=c42.pk)))
    assert sorted(ids) == sorted(e.pk for e in attendues)


def test_ca20_filtre_ip(client, admin1, dir1, c42):
    a = consultation(c42, dir1, ip=IP)
    b = consultation(c42, dir1, ip=IP2)
    ids = rows_ids(get(client, admin1, journal_url(ip=IP)))
    assert a.pk in ids and b.pk not in ids


def test_ca21_filtres_combines_htmx_et_page_complete(client, admin1, dir1, c42, c4):
    ok = consultation(c42, dir1, ip=IP)
    consultation(c42, dir1, ip=IP2)
    consultation(c4, dir1, ip=IP)
    entree(action=AuditAction.LOGIN, actor=dir1, ip_address=IP)
    params = {"acteur": dir1.pk, "action": "view", "ip": IP, "dossier": c42.pk}
    complete = get(client, admin1, journal_url(**params))
    fragment = get(client, admin1, journal_url(**params), **HX)
    assert rows_ids(complete) == rows_ids(fragment) == [ok.pk]
    assert b"<html" in complete.content and b"<html" not in fragment.content
    form = parse(complete).with_attr("id", "filtres-journal")[0]
    assert form.attrs["hx-push-url"] == "true" and form.attrs["method"] == "get"
    # Les liens d'export et de pagination portent tous les filtres.
    export = [
        n
        for n in parse(complete).iter()
        if n.tag == "a" and "/export/csv/" in n.attrs.get("href", "")
    ]
    href = export[0].attrs["href"]
    for valeur in (f"acteur={dir1.pk}", "action=view", f"ip={IP}", f"dossier={c42.pk}"):
        assert valeur in href


def test_ca22_parametres_invalides(client, admin1, dir1, c42):
    e = consultation(c42, dir1)
    response = get(
        client, admin1, journal_url(du="2026-13-45", dossier="abc", action="pirate", acteur=999999)
    )
    assert response.status_code == 200
    messages = parse(response).with_attr("data-filtres-messages")[0].text()
    for fragment in ("2026-13-45", "abc", "pirate", "acteur"):
        assert fragment in messages
    assert e.pk in rows_ids(response)  # filtres invalides ignorés

    for params in (
        {"du": "2026-11-20", "au": "2026-11-01"},
        {"du": "2025-01-01", "au": "2026-11-20"},
    ):
        response = get(client, admin1, journal_url(**params))
        assert response.status_code == 200
        assert parse(response).with_attr("data-filtres-messages")
        assert rows_ids(response) == []


def test_ca23_aucun_resultat(client, admin1):
    response = get(client, admin1, journal_url(ip="192.0.2.99"))
    assert "Aucune entrée pour ces critères." in parse(response).text()


def test_ca24_consultation_du_journal_journalisee_et_masquee(client, admin1, dir1, c42):
    get(client, admin1, journal_url(dossier=c42.pk))
    e = views().get(metadata__vue="journal_liste")
    assert e.actor == admin1
    assert e.metadata["filtres"]["dossier"] == c42.pk
    assert e.metadata["page"] == 1 and "nb_resultats" in e.metadata
    assert e.pk not in rows_ids(get(client, admin1, journal_url()))
    assert e.pk in rows_ids(get(client, admin1, journal_url(journal="1")))


def test_ca25_valeurs_echappees(client, admin1):
    xss = "<script>alert(1)</script>"
    e = entree(action=AuditAction.SYNC, object_repr=xss, metadata={"note": xss})
    for url in (journal_url(), reverse("audit:journal_detail", args=[e.pk])):
        response = get(client, admin1, url)
        assert xss.encode() not in response.content
        assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.content


def test_ca26_lien_vers_la_fiche_et_objet_disparu(client, admin1, dir1, c42, piece7):
    sur_fiche = consultation(c42, dir1)
    sur_piece = entree(obj=piece7, actor=dir1, metadata={"vue": "piece", "mode": "apercu"})
    disparu = entree(
        actor=dir1,
        content_type=ContentType.objects.get_for_model(Candidature),
        object_id="987654",
        object_repr="Candidature #987654",
    )
    response = get(client, admin1, journal_url())
    assert response.status_code == 200
    root = parse(response)

    def objet(e):
        return [
            n
            for n in root.with_attr("data-entree", str(e.pk))[0].iter()
            if n.attrs.get("data-col") == "objet"
        ][0]

    for e in (sur_fiche, sur_piece):
        liens = [n for n in objet(e).iter() if n.tag == "a"]
        assert liens and liens[0].attrs["href"] == fiche_url(c42)
    assert not [n for n in objet(disparu).iter() if n.tag == "a"]
    assert "Candidature #987654" in objet(disparu).text()


# --------------------------------------------------------------------------------------
# Détail, non-modification (CA27 à CA29)
# --------------------------------------------------------------------------------------


def test_ca27_detail_avant_apres(client, admin1, dir1, c42):
    e = entree(
        action=AuditAction.DECISION,
        actor=dir1,
        obj=c42,
        ip_address=IP,
        user_agent=UA,
        before={"id": c42.pk, "statut": "eligible"},
        after={"id": c42.pk, "statut": "preselectionnee"},
        metadata={"decision_id": 5, "candidature_id": c42.pk},
    )
    response = get(client, admin1, reverse("audit:journal_detail", args=[e.pk]))
    assert response.status_code == 200
    root = parse(response)
    texte = root.text()
    for valeur in ("dir1@mafoa.test", "Décision", f"Candidature #{c42.pk}", IP, UA, "decision_id"):
        assert valeur in texte
    statut = root.with_attr("data-cle", "statut")[0]
    assert statut.with_attr("data-avant")[0].text() == "eligible"
    assert statut.with_attr("data-apres")[0].text() == "preselectionnee"
    assert statut.with_attr("data-modifie") and "modifié" in statut.text()
    assert not root.with_attr("data-cle", "id")[0].with_attr("data-modifie")
    v = views().get(metadata__vue="journal_detail")
    assert v.content_type == ContentType.objects.get_for_model(JournalAudit)
    assert v.object_id == str(e.pk)


def test_ca28_detail_inexistant(client, admin1):
    assert get(client, admin1, reverse("audit:journal_detail", args=[999999])).status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_ca29_aucune_modification_possible(client, admin1, dir1, c42, method):
    e = consultation(c42, dir1)
    avant = list(JournalAudit.objects.values_list("id", "object_repr", "metadata"))
    client.force_login(admin1)
    for url in (journal_url(), reverse("audit:journal_detail", args=[e.pk]), export_url("csv")):
        assert getattr(client, method)(url).status_code == 405
    apres = list(
        JournalAudit.objects.filter(pk__in=[a[0] for a in avant]).values_list(
            "id", "object_repr", "metadata"
        )
    )
    assert apres == avant


def test_ca29_aucun_formulaire_de_modification(client, admin1, dir1, c42):
    e = consultation(c42, dir1)
    for url in (journal_url(), reverse("audit:journal_detail", args=[e.pk])):
        root = parse(get(client, admin1, url))
        forms = [n for n in root.iter() if n.tag == "form"]
        # Seul le formulaire de filtres (GET) et la déconnexion de l'en-tête sont présents.
        for form in forms:
            if form.attrs.get("method", "get").lower() == "post":
                assert "logout" in form.attrs.get("action", "")
        texte = root.text()
        assert "Supprimer" not in texte and "Modifier" not in texte


def test_ca29_head_autorise(client, admin1):
    client.force_login(admin1)
    assert client.head(journal_url()).status_code == 200


# --------------------------------------------------------------------------------------
# Panneau « Consultations de ce dossier » (CA30 à CA32)
# --------------------------------------------------------------------------------------


def test_ca30_panneau_des_consultations(client, admin1, dir1, c42, piece7):
    for _ in range(13):
        consultation(c42, dir1)
    for _ in range(12):
        entree(
            obj=piece7,
            actor=admin1,
            ip_address=IP2,
            metadata={
                "vue": "piece",
                "candidature_id": c42.pk,
                "piece_id": piece7.pk,
                "mode": "apercu",
            },
        )
    response = get(client, admin1, panneau_url(c42), **HX)
    assert response.status_code == 200
    root = parse(response)
    assert (
        "25 consultations par 2 personnes" in root.with_attr("data-consultations-total")[0].text()
    )
    items = root.with_attr("data-consultation")
    assert len(items) == 20
    premier = items[0].text()
    assert "admin1@mafoa.test" in premier and IP2 in premier
    assert f"Pièce #{piece7.pk} (aperçu)" in premier
    assert "Fiche" in items[-1].text()
    lien = root.with_attr("data-lien-journal")[0].attrs["href"]
    assert f"dossier={c42.pk}" in lien
    e = views().get(metadata__vue="journal_dossier")
    assert e.metadata["candidature_id"] == c42.pk and e.actor == admin1
    # La consultation du panneau n'est pas comptée.
    response = get(client, admin1, panneau_url(c42), **HX)
    assert "25 consultations" in parse(response).text()


def test_ca31_panneau_absent_et_interdit_pour_la_direction(client, admin1, dir1, c42):
    response = get(client, dir1, fiche_url(c42))
    assert b"data-voir-consultations" not in response.content
    assert get(client, dir1, panneau_url(c42)).status_code == 403
    assert denied().filter(actor=dir1, metadata__candidature_id=c42.pk).count() == 1
    client.logout()
    response = get(client, admin1, fiche_url(c42))
    bouton = parse(response).with_attr("data-voir-consultations")
    assert len(bouton) == 1
    assert bouton[0].attrs["hx-get"] == panneau_url(c42)
    # Chargement à la demande uniquement : pas de déclenchement au survol ni au chargement.
    assert "hx-trigger" not in bouton[0].attrs


def test_ca32_premiere_consultation(client, admin1, c42):
    get(client, admin1, fiche_url(c42))
    response = get(client, admin1, panneau_url(c42), **HX)
    assert "1 consultation par 1 personne" in parse(response).text()


def test_panneau_dossier_inexistant(client, admin1):
    assert get(client, admin1, panneau_url(999999)).status_code == 404


# --------------------------------------------------------------------------------------
# Export (CA33 à CA36)
# --------------------------------------------------------------------------------------

FILENAME = re.compile(r'attachment; filename="journal-audit-\d{8}-\d{4}\.(csv|xlsx)"')


def _csv_rows(response) -> list[list[str]]:
    content = b"".join(response.streaming_content).decode("utf-8")
    assert content.startswith("﻿")
    return list(csv.reader(io.StringIO(content[1:]), delimiter=";"))


def test_ca33_export_csv(client, admin1, dir1, c42, c4, c420, piece7):
    attendues, _ = _entrees_dossiers(dir1, c42, c4, c420, piece7)
    response = get(client, admin1, export_url("csv", dossier=c42.pk))
    assert response.status_code == 200
    assert FILENAME.fullmatch(response["Content-Disposition"]).group(1) == "csv"
    assert response["Content-Type"].startswith("text/csv")
    rows = _csv_rows(response)
    assert rows[0] == exports.HEADERS
    assert not any(h.lower() in ("avant", "après") for h in rows[0])
    assert len(rows) == 1 + len(attendues)
    objets = sorted(r[5] for r in rows[1:])
    assert objets == sorted(e.object_repr for e in attendues)
    assert all(len(r) == len(exports.HEADERS) for r in rows)
    e = JournalAudit.objects.get(action=AuditAction.EXPORT)
    assert e.actor == admin1
    assert e.metadata["vue"] == "journal_export"
    assert e.metadata["format"] == "csv"
    assert e.metadata["filtres"]["dossier"] == c42.pk
    assert e.metadata["nb_lignes"] == len(attendues)


def test_ca34_export_excel(client, admin1, dir1, c42, c4, c420, piece7):
    from openpyxl import load_workbook

    attendues, _ = _entrees_dossiers(dir1, c42, c4, c420, piece7)
    response = get(client, admin1, export_url("xlsx", dossier=c42.pk))
    assert response.status_code == 200
    assert FILENAME.fullmatch(response["Content-Disposition"]).group(1) == "xlsx"
    sheet = load_workbook(io.BytesIO(response.content), read_only=True).active
    rows = [list(r) for r in sheet.iter_rows(values_only=True)]
    assert rows[0] == exports.HEADERS
    assert sorted(r[5] or "" for r in rows[1:]) == sorted(e.object_repr for e in attendues)
    e = JournalAudit.objects.get(action=AuditAction.EXPORT)
    assert e.metadata["format"] == "xlsx" and e.metadata["nb_lignes"] == len(attendues)


@pytest.mark.parametrize("prefixe", ["=", "+", "-", "@"])
def test_ca35_neutralisation_des_formules(client, admin1, prefixe):
    from openpyxl import load_workbook

    e = entree(
        action=AuditAction.SYNC,
        actor_repr=f"{prefixe}HYPERLINK(1)",
        metadata={"x": f"{prefixe}1+1"},
    )
    rows = _csv_rows(get(client, admin1, export_url("csv", action="sync")))
    ligne = [r for r in rows[1:] if r[1].endswith("HYPERLINK(1)")][0]
    assert ligne[1] == f"'{prefixe}HYPERLINK(1)"
    # Seul le début de cellule est neutralisé : la valeur journalisée reste intacte dans le JSON.
    assert f'"x":"{prefixe}1+1"' in ligne[8]
    response = get(client, admin1, export_url("xlsx", action="sync"))
    sheet = load_workbook(io.BytesIO(response.content), read_only=True).active
    cellules = [r for r in sheet.iter_rows(values_only=True)][1:]
    assert any(r[1] == f"'{prefixe}HYPERLINK(1)" for r in cellules)
    assert e.pk


def test_neutralize_tabulation_et_retour_chariot():
    assert exports.neutralize("\tx") == "'\tx"
    assert exports.neutralize("\rx") == "'\rx"
    assert exports.neutralize("Candidature #42") == "Candidature #42"


def test_ca36_plafond_d_export(client, admin1, dir1, c42, c4, c420, piece7, settings):
    _entrees_dossiers(dir1, c42, c4, c420, piece7)
    settings.AUDIT_EXPORT_MAX_ROWS = 3
    client.force_login(admin1)
    response = client.get(export_url("csv", dossier=c42.pk), follow=True, **REQ)
    assert "attachment" not in response.get("Content-Disposition", "")
    assert "restreignez les filtres" in parse(response).text()
    assert not JournalAudit.objects.filter(action=AuditAction.EXPORT).exists()


def test_export_vide_avec_en_tetes_seuls(client, admin1):
    rows = _csv_rows(get(client, admin1, export_url("csv", ip="192.0.2.99")))
    assert rows == [exports.HEADERS]
    assert JournalAudit.objects.get(action=AuditAction.EXPORT).metadata["nb_lignes"] == 0


def test_export_format_inconnu(client, admin1):
    assert get(client, admin1, export_url("pdf")).status_code == 404


# --------------------------------------------------------------------------------------
# Accès refusés (règle 8)
# --------------------------------------------------------------------------------------


def test_log_access_denied_sans_nom(cons1, c42):
    request = RequestFactory().get(f"/candidatures/{c42.pk}/?q=x", **REQ)
    request.user = cons1
    e = log_access_denied(request, candidature_id=c42.pk)
    assert e.action == AuditAction.ACCESS_DENIED
    assert e.metadata == {"chemin": f"/candidatures/{c42.pk}/", "candidature_id": c42.pk}
    assert e.object_repr == f"Candidature #{c42.pk}"
    assert e.get_action_display() == "Accès refusé"


# --------------------------------------------------------------------------------------
# Performance, index, interface (CA37 à CA40)
# --------------------------------------------------------------------------------------


def _nb_requetes(client, url, **extra) -> int:
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url, **{**REQ, **extra})
    assert response.status_code == 200
    return len(ctx.captured_queries)


def _bulk(n, c42, dir1, admin1):
    ct = ContentType.objects.get_for_model(Candidature)
    JournalAudit.objects.bulk_create(
        JournalAudit(
            actor=dir1 if i % 2 else admin1,
            actor_repr="x",
            action=AuditAction.VIEW,
            content_type=ct,
            object_id=str(c42.pk),
            object_repr=f"Candidature #{c42.pk}",
            ip_address=IP,
            metadata={"vue": "fiche", "candidature_id": c42.pk},
        )
        for i in range(n)
    )


def test_ca37_nombre_de_requetes_constant(client, admin1, dir1, c42):
    _bulk(5, c42, dir1, admin1)
    detail = JournalAudit.objects.order_by("id").first()
    client.force_login(admin1)
    urls = [
        (journal_url(), {}),
        (panneau_url(c42), HX),
        (reverse("audit:journal_detail", args=[detail.pk]), {}),
    ]
    for url, extra in urls:  # préchauffage (cache des types de contenu, session)
        _nb_requetes(client, url, **extra)
    petit = [_nb_requetes(client, url, **extra) for url, extra in urls]
    _bulk(4995, c42, dir1, admin1)
    grand = [_nb_requetes(client, url, **extra) for url, extra in urls]
    assert grand == petit


def test_ca38_index_presents():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'audit_journalaudit'"
        )
        index = dict(cursor.fetchall())
    assert "candidature_id" in index["audit_meta_candidature_idx"]
    assert "->>" in index["audit_meta_candidature_idx"]
    assert re.search(r"\(actor_id, \"?timestamp\"?\)", index["audit_actor_ts_idx"])


def test_ca38_migration_sur_table_peuplee():
    executor = MigrationExecutor(connection)
    executor.migrate([("audit", "0002_append_only_trigger")])
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO audit_journalaudit (timestamp, actor_repr, action, object_id, "
            "object_repr, user_agent, metadata) VALUES (now(), 'système', 'view', '', "
            "'Candidature #1', '', '{\"candidature_id\": 1}') RETURNING id"
        )
        (entry_id,) = cursor.fetchone()
        # Contrôles de clés étrangères différés : à vérifier avant le CREATE INDEX.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.migrate([("audit", "0003_kan14_access_denied_index")])
    e = JournalAudit.objects.get(pk=entry_id)
    assert e.object_repr == "Candidature #1" and e.metadata == {"candidature_id": 1}


@pytest.mark.parametrize(
    "ts",
    [
        # Exemple de la spec. Avec tzdata 2026e, le Maroc est revenu à UTC+0 le 20/09/2026 :
        # l'heure attendue est calculée par la base des fuseaux, pas figée.
        datetime(2026, 11, 20, 23, 30, tzinfo=UTC),
        # Période à UTC+1 : prouve que la conversion a bien lieu (changement de jour).
        datetime(2026, 7, 20, 23, 30, tzinfo=UTC),
    ],
)
def test_ca39_fuseau_de_casablanca(client, admin1, ts):
    e = entree(ts=ts, action=AuditAction.SYNC)
    local = ts.astimezone(CASA)
    attendu = local.strftime("%d/%m/%Y %H:%M:%S")
    if ts.month == 7:
        assert attendu == "21/07/2026 00:30:00"
    du = au = local.date().isoformat()
    response = get(client, admin1, journal_url(du=du, au=au))
    ligne = parse(response).with_attr("data-entree", str(e.pk))[0]
    assert ligne.with_attr("data-col", "horodatage")[0].text() == attendu


def test_ca40_arabe_rtl_et_proprietes_logiques(client, admin1, dir1, c42):
    e = consultation(c42, dir1)
    client.force_login(admin1)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    for url in (journal_url(), reverse("audit:journal_detail", args=[e.pk])):
        response = client.get(url, **REQ)
        assert response.status_code == 200
        html = [n for n in parse(response).iter() if n.tag == "html"][0]
        assert html.attrs["dir"] == "rtl"
    interdit = re.compile(r"\b(ml|mr|pl|pr|left|right)-|\btext-(left|right)\b")
    gabarits = Path(settings.BASE_DIR, "templates", "audit")
    for path in gabarits.glob("*.html"):
        assert not interdit.search(path.read_text(encoding="utf-8")), path.name


def test_export_excel_supporte_les_caracteres_de_controle(client, admin1):
    """Relecture conformité : un User-Agent forgé (\\x01) ne rend pas l'export Excel impossible."""
    from openpyxl import load_workbook

    entree(action=AuditAction.SYNC, user_agent="Navigateur\x01Fictif")
    response = get(client, admin1, export_url("xlsx", action="sync"))
    assert response.status_code == 200
    sheet = load_workbook(io.BytesIO(response.content), read_only=True).active
    assert any(r[7] == "Navigateur\ufffdFictif" for r in sheet.iter_rows(values_only=True))


def test_export_sans_terme_de_recherche(client, admin1):
    """Minimisation : un terme de recherche (nom, CIN) ne sort pas dans le fichier exporté."""
    entree(action=AuditAction.SYNC, metadata={"recherche": "BENALI", "page": 1})
    rows = _csv_rows(get(client, admin1, export_url("csv", action="sync")))
    assert all("BENALI" not in ";".join(r) for r in rows)
    assert any('"page":1' in r[8] for r in rows[1:])


def test_neutralize_espaces_de_tete():
    assert exports.neutralize("  =1+1") == "'  =1+1"
