"""Contrôles d'accès globaux.

1. ``LoginRequiredMiddleware`` : toute page exige une session, sauf les pages publiques
   strictement nécessaires (connexion, réinitialisation du mot de passe, invitation).
2. ``MFARequiredMiddleware`` : un utilisateur connecté sans TOTP actif est redirigé vers
   l'activation du TOTP. Aucune autre page n'est accessible tant qu'il n'est pas configuré.
   Contrôlé par ``settings.MFA_REQUIRED`` (toujours actif en production).
"""

from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.cache import add_never_cache_headers, patch_cache_control

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
            settings.MFA_REQUIRED
            and user.is_authenticated
            and not request.path_info.startswith(self._allowed_paths())
            and not is_mfa_enabled(user, types=["totp"])
        ):
            return redirect("mfa_activate_totp")
        return self.get_response(request)


class NoStoreForAuthenticatedMiddleware:
    """Interdit la mise en cache des pages servies à un utilisateur connecté.

    Les pages contiennent des données personnelles de candidats : après déconnexion, le bouton
    Précédent ne doit pas pouvoir les réafficher depuis un cache du navigateur.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            add_never_cache_headers(response)
            patch_cache_control(response, private=True)
        return response
