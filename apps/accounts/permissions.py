"""Vérification des rôles côté serveur, à utiliser sur chaque vue métier."""

from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

from .models import Role

DIRECTION_ROLES = (Role.ADMIN, Role.DIRECTION)
ADMIN_ROLES = (Role.ADMIN,)


def role_required(*roles: str):
    """Décorateur de vue : 403 si l'utilisateur n'a pas l'un des rôles demandés."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated or not request.user.has_role(*roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class RoleRequiredMixin(AccessMixin):
    allowed_roles: tuple[str, ...] = DIRECTION_ROLES

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.has_role(*self.allowed_roles):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
