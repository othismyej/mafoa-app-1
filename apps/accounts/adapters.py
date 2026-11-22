import time

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.internal.flows.login import get_authentication_records
from allauth.core import context
from allauth.mfa.utils import is_mfa_enabled

FIRST_SETUP_WINDOW_SECONDS = 300


class AccountAdapter(DefaultAccountAdapter):
    """Aucune inscription publique : les comptes sont créés uniquement par invitation."""

    def is_open_for_signup(self, request) -> bool:
        return False

    def is_reauthentication_required(self, *, app: str, **kwargs) -> bool:
        # Première configuration du TOTP juste après la connexion : pas de seconde saisie
        # du mot de passe. Toute modification ultérieure de la MFA reste protégée.
        request = getattr(context, "request", None)
        user = getattr(request, "user", None)
        if (
            app == "allauth.mfa"
            and user is not None
            and user.is_authenticated
            and not is_mfa_enabled(user)
        ):
            records = get_authentication_records(request, user)
            if records and time.time() - records[-1]["at"] < FIRST_SETUP_WINDOW_SECONDS:
                return False
        return super().is_reauthentication_required(app=app, **kwargs)
