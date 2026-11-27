import pytest
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret

from apps.accounts.models import Role, User


def make_user(email: str, role: str, *, mfa: bool = True, password: str = "Mot-de-passe-solide-42"):
    user = User.objects.create_user(email=email, password=password, role=role, full_name=email)
    if mfa:
        TOTP.activate(user, generate_totp_secret())
    return user


@pytest.fixture
def admin_user(db):
    return make_user("admin@example.com", Role.ADMIN)


@pytest.fixture
def direction_user(db):
    return make_user("direction@example.com", Role.DIRECTION)


@pytest.fixture
def user_without_mfa(db):
    return make_user("nouveau@example.com", Role.DIRECTION, mfa=False)


@pytest.fixture
def synced(db):
    """Base peuplée par la FixtureSource (sans calcul d'empreintes, plus rapide)."""
    from integrations.site_source.fixture_source import FixtureSource
    from integrations.site_source.sync import sync_from_source

    source = FixtureSource(seed=1234)
    sync_from_source(source, compute_hashes=False)
    return source


@pytest.fixture(autouse=True)
def _aucun_appel_ia_reel(monkeypatch):
    """Garde-fou (KAN-41) : aucun test n'appelle l'API Claude réelle."""

    def interdit(*args, **kwargs):
        raise AssertionError("Appel réseau à l'API Claude interdit dans les tests.")

    monkeypatch.setattr("ai.client.anthropic_transport", interdit)
