from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Invitation, Role, User
from apps.audit.models import AuditAction, JournalAudit

from .conftest import make_user

pytestmark = pytest.mark.django_db
PASSWORD = "Mot-de-passe-solide-42"


def test_inscription_publique_fermee(client):
    response = client.post(
        reverse("account_signup"),
        {"email": "intrus@example.com", "password1": PASSWORD, "password2": PASSWORD},
    )
    assert not User.objects.filter(email="intrus@example.com").exists()
    assert response.status_code == 200
    assert "account/signup_closed" in [t.name for t in response.templates][0]


def test_pages_protegees_redirigent_vers_la_connexion(client):
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_utilisateur_sans_totp_est_force_a_l_activer(client, user_without_mfa):
    response = client.post(
        reverse("account_login"), {"login": user_without_mfa.email, "password": PASSWORD}
    )
    assert response.status_code == 302
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert response.url == reverse("mfa_activate_totp")
    # La page d'activation elle-même reste accessible.
    page = client.get(reverse("mfa_activate_totp"))
    assert page.status_code == 200
    assert "totp" in page.content.decode().lower()


def test_activation_totp_debloque_l_acces(
    client, user_without_mfa, django_capture_on_commit_callbacks
):
    from allauth.mfa.totp.internal.auth import (
        format_hotp_value,
        hotp_value,
        yield_hotp_counters_from_time,
    )

    client.post(reverse("account_login"), {"login": user_without_mfa.email, "password": PASSWORD})
    client.get(reverse("mfa_activate_totp"))
    secret = client.session["mfa.totp.secret"]
    counter = next(yield_hotp_counters_from_time())
    code = format_hotp_value(hotp_value(secret, counter))
    # Le signal d'allauth part en on_commit : on exécute les callbacks dans le test.
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("mfa_activate_totp"), {"code": code})
    assert response.status_code == 302
    assert client.get(reverse("dashboard")).status_code == 200
    assert JournalAudit.objects.filter(action=AuditAction.MFA_ACTIVATED).exists()


def test_utilisateur_avec_totp_accede_au_tableau_de_bord(client, direction_user):
    client.force_login(direction_user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert "Tableau de bord" in response.content.decode()


def test_connexion_par_mot_de_passe_exige_le_code_totp(client, direction_user):
    response = client.post(
        reverse("account_login"), {"login": direction_user.email, "password": PASSWORD}
    )
    assert response.status_code == 302
    assert response.url == reverse("mfa_authenticate")
    # Tant que le code TOTP n'est pas saisi, l'utilisateur n'est pas connecté.
    assert client.get(reverse("dashboard")).status_code == 302
    assert "_auth_user_id" not in client.session


def test_echec_de_connexion_journalise(client, direction_user):
    client.post(reverse("account_login"), {"login": direction_user.email, "password": "faux"})
    entry = JournalAudit.objects.get(action=AuditAction.LOGIN_FAILED)
    assert entry.metadata["identifiant"] == direction_user.email
    assert "faux" not in str(entry.metadata)


def test_role_inactif_refuse(client, direction_user):
    direction_user.role = Role.CONSULTANT
    direction_user.save()
    client.force_login(direction_user)
    assert client.get(reverse("dashboard")).status_code == 403


def test_admin_django_reserve_au_role_admin(client, admin_user, direction_user):
    client.force_login(direction_user)
    assert client.get(reverse("admin:index")).status_code in (302, 403)
    client.force_login(admin_user)
    assert client.get(reverse("admin:index")).status_code == 200


def test_admin_django_exige_aussi_le_totp(client):
    admin_sans_mfa = make_user("admin2@example.com", Role.ADMIN, mfa=False)
    client.force_login(admin_sans_mfa)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 302
    assert response.url == reverse("mfa_activate_totp")


def test_is_staff_derive_du_role(db):
    u = make_user("x@example.com", Role.DIRECTION)
    assert not u.is_staff
    u.role = Role.ADMIN
    u.save()
    assert u.is_staff
    u.role = Role.DIRECTION
    u.is_superuser = True
    u.save()
    assert not u.is_staff and not u.is_superuser


def test_invitation_cree_le_compte_avec_le_role(client, admin_user):
    inv = Invitation.objects.create(
        email="associe@example.com", role=Role.DIRECTION, invited_by=admin_user
    )
    url = reverse("accept_invitation", args=[inv.token])
    assert client.get(url).status_code == 200
    response = client.post(
        url, {"full_name": "Associé Fictif", "password1": PASSWORD, "password2": PASSWORD}
    )
    assert response.status_code == 302
    user = User.objects.get(email="associe@example.com")
    assert user.role == Role.DIRECTION and user.check_password(PASSWORD)
    inv.refresh_from_db()
    assert inv.accepted_user == user and not inv.is_usable
    assert JournalAudit.objects.filter(action=AuditAction.INVITATION_ACCEPTED).exists()
    # Usage unique.
    assert client.get(url).status_code == 404


def test_invitation_expiree_refusee(client, admin_user):
    inv = Invitation.objects.create(
        email="tard@example.com",
        invited_by=admin_user,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    assert client.get(reverse("accept_invitation", args=[inv.token])).status_code == 404


def test_invitation_mot_de_passe_faible_refuse(client, admin_user):
    inv = Invitation.objects.create(email="faible@example.com", invited_by=admin_user)
    response = client.post(
        reverse("accept_invitation", args=[inv.token]),
        {"full_name": "X", "password1": "123", "password2": "123"},
    )
    assert response.status_code == 200
    assert not User.objects.filter(email="faible@example.com").exists()


def test_jeton_invitation_inconnu(client):
    assert client.get(reverse("accept_invitation", args=["inconnu"])).status_code == 404


def test_admin_cree_une_invitation_journalisee(client, admin_user):
    client.force_login(admin_user)
    response = client.post(
        reverse("admin:accounts_invitation_add"),
        {
            "email": "Nouvel.Associe@Example.com",
            "role": Role.DIRECTION,
            "expires_at_0": "2099-01-01",
            "expires_at_1": "00:00:00",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    inv = Invitation.objects.get()
    assert inv.email == "nouvel.associe@example.com" and inv.invited_by == admin_user
    assert JournalAudit.objects.filter(action=AuditAction.INVITATION_CREATED).exists()


def test_admin_ne_peut_pas_creer_de_compte_directement(client, admin_user):
    client.force_login(admin_user)
    assert client.get(reverse("admin:accounts_user_add")).status_code == 403


def test_mfa_facultative_si_desactivee(client, user_without_mfa, settings):
    settings.MFA_REQUIRED = False
    client.force_login(user_without_mfa)
    assert client.get(reverse("dashboard")).status_code == 200


def test_mfa_toujours_forcee_en_production():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "config" / "settings" / "prod.py"
    assigns = {
        t.id: node.value
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert isinstance(assigns["MFA_REQUIRED"], ast.Constant)
    assert assigns["MFA_REQUIRED"].value is True


def test_pages_connectees_non_mises_en_cache(client, direction_user):
    client.force_login(direction_user)
    cache_control = client.get(reverse("dashboard")).headers["Cache-Control"]
    assert "no-store" in cache_control and "private" in cache_control
