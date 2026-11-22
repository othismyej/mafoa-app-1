"""Contrôles d'accès globaux.

1. ``LoginRequiredMiddleware`` : toute page exige une session, sauf les pages publiques
   strictement nécessaires (connexion, réinitialisation du mot de passe, invitation).
2. ``MFARequiredMiddleware`` : un utilisateur connecté sans TOTP actif est redirigé vers
   l'activation du TOTP. Aucune autre page n'est accessible tant qu'il n'est pas configuré.
"""

from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse

PUBLIC_PREFIXES = ("/accounts/", "/invitation/", "/i18n/", "/admin/login/")


def _static_prefix() -> str:
    return "/" + settings.STATIC_URL.lstrip("/")


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if (
            not request.user.is_authenticated
            and not path.startswith(PUBLIC_PREFIXES)
            and not path.startswith(_static_prefix())
        ):
            return redirect_to_login(request.get_full_path(), reverse(settings.LOGIN_URL))
        return self.get_response(request)


class MFARequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _allowed_paths(self) -> tuple[str, ...]:
        return (
            reverse("mfa_activate_totp"),
            reverse("mfa_index"),
            reverse("mfa_reauthenticate"),
            reverse("account_reauthenticate"),
            reverse("account_logout"),
            "/i18n/",
            _static_prefix(),
        )

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and not request.path_info.startswith(self._allowed_paths())
            and not is_mfa_enabled(user, types=["totp"])
        ):
            return redirect("mfa_activate_totp")
        return self.get_response(request)
