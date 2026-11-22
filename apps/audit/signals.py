"""Journalisation automatique des connexions et de la double authentification."""

from allauth.mfa.signals import authenticator_added, authenticator_removed
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditAction
from .services import log


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log(AuditAction.LOGIN, actor=user, obj=user, request=request)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log(AuditAction.LOGOUT, actor=user, obj=user, request=request)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    # On ne journalise jamais le mot de passe : uniquement l'identifiant saisi.
    identifier = str(credentials.get("email") or credentials.get("username") or "")[:255]
    log(
        AuditAction.LOGIN_FAILED,
        request=request,
        actor_repr="anonyme",
        metadata={"identifiant": identifier},
    )


@receiver(authenticator_added)
def on_mfa_added(sender, request, user, authenticator, **kwargs):
    log(
        AuditAction.MFA_ACTIVATED,
        actor=user,
        obj=user,
        request=request,
        metadata={"type": authenticator.type},
    )


@receiver(authenticator_removed)
def on_mfa_removed(sender, request, user, authenticator, **kwargs):
    log(
        AuditAction.MFA_DEACTIVATED,
        actor=user,
        obj=user,
        request=request,
        metadata={"type": authenticator.type},
    )
