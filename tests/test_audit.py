import pytest
from django.db import connection, transaction
from django.db.utils import DatabaseError

from apps.audit.models import AppendOnlyError, AuditAction, JournalAudit
from apps.audit.services import log

pytestmark = pytest.mark.django_db


def test_log_enregistre_acteur_objet_avant_apres(direction_user, rf):
    request = rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_USER_AGENT="pytest")
    request.user = direction_user
    entry = log(
        AuditAction.UPDATE, obj=direction_user, before={"a": 1}, after={"a": 2}, request=request
    )
    assert entry.actor == direction_user
    assert entry.object_id == str(direction_user.pk)
    assert entry.before == {"a": 1} and entry.after == {"a": 2}
    assert entry.ip_address == "10.0.0.5" and entry.user_agent == "pytest"


def test_modification_interdite_par_l_orm(db):
    entry = log(AuditAction.SYNC)
    entry.action = AuditAction.DELETE
    with pytest.raises(AppendOnlyError):
        entry.save()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    with pytest.raises(AppendOnlyError):
        JournalAudit.objects.all().update(action="x")
    with pytest.raises(AppendOnlyError):
        JournalAudit.objects.all().delete()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_journalaudit SET action = 'falsifie'",
        "DELETE FROM audit_journalaudit",
    ],
)
def test_modification_interdite_en_sql_direct(db, sql):
    log(AuditAction.SYNC)
    with (
        pytest.raises(DatabaseError, match="ajout seul"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)
    assert JournalAudit.objects.filter(action=AuditAction.SYNC).count() == 1


def test_connexion_et_deconnexion_journalisees(client, direction_user):
    client.force_login(direction_user)
    client.post("/accounts/logout/")
    actions = list(JournalAudit.objects.values_list("action", flat=True))
    assert AuditAction.LOGIN in actions and AuditAction.LOGOUT in actions


def test_admin_journal_en_lecture_seule(client, admin_user):
    entry = log(AuditAction.SYNC)
    client.force_login(admin_user)
    assert client.get("/admin/audit/journalaudit/").status_code == 200
    assert client.get(f"/admin/audit/journalaudit/{entry.pk}/delete/").status_code == 403
    assert client.get("/admin/audit/journalaudit/add/").status_code == 403


# --- Adresse IP du client (KAN-29) ---------------------------------------------------


@pytest.mark.parametrize(
    ("count", "meta", "attendu"),
    [
        (0, {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "6.6.6.6"}, "10.0.0.1"),
        (1, {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "6.6.6.6, 41.92.0.7"}, "41.92.0.7"),
        (
            2,
            {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "6.6.6.6, 41.92.0.7, 10.0.0.9"},
            "41.92.0.7",
        ),
        (2, {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "41.92.0.7"}, "10.0.0.1"),
        (1, {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "not-an-ip"}, "10.0.0.1"),
        (0, {"REMOTE_ADDR": "pas une ip"}, None),
        (1, {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "2001:db8::1"}, "2001:db8::1"),
    ],
)
def test_client_ip(rf, settings, count, meta, attendu):
    from apps.audit.services import client_ip

    settings.TRUSTED_PROXY_COUNT = count
    request = rf.get("/", **meta)
    assert client_ip(request) == attendu


def test_en_tete_forge_ignore_sans_proxy_de_confiance(direction_user, rf, settings):
    settings.TRUSTED_PROXY_COUNT = 0
    request = rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="1.2.3.4")
    request.user = direction_user
    assert log(AuditAction.VIEW, request=request).ip_address == "10.0.0.5"


def test_valeur_invalide_sans_erreur(direction_user, client, settings):
    settings.TRUSTED_PROXY_COUNT = 1
    client.force_login(direction_user)
    response = client.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4<script>")
    assert response.status_code == 200


def test_echec_de_connexion_journalise_malgre_un_en_tete_invalide(client, direction_user, settings):
    settings.TRUSTED_PROXY_COUNT = 1
    client.post(
        "/accounts/login/",
        {"login": direction_user.email, "password": "faux"},
        HTTP_X_FORWARDED_FOR="not-an-ip",
    )
    entry = JournalAudit.objects.get(action=AuditAction.LOGIN_FAILED)
    assert entry.ip_address == "127.0.0.1"
