from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.urls import reverse

from apps.audit.models import AuditAction
from apps.audit.services import log, snapshot

from .models import ACTIVE_ROLES, Invitation, User

ROLE_CHOICES = [(r.value, r.label) for r in ACTIVE_ROLES]


class UserChangeForm(forms.ModelForm):
    role = forms.ChoiceField(label="Rôle", choices=ROLE_CHOICES)

    class Meta:
        model = User
        fields = ("email", "full_name", "role", "is_active")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Gestion des comptes. Pas de création directe : passer par une invitation."""

    form = UserChangeForm
    list_display = ("email", "full_name", "role", "is_active", "last_login")
    list_filter = ("role", "is_active")
    search_fields = ("email", "full_name")
    ordering = ("email",)
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "full_name")}),
        ("Accès", {"fields": ("role", "is_active")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    filter_horizontal = ()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # On désactive un compte, on ne le supprime pas (traçabilité).
        return False

    def save_model(self, request, obj, form, change):
        before = snapshot(User.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        log(
            AuditAction.UPDATE if change else AuditAction.CREATE,
            obj=obj,
            before=_strip_secret(before),
            after=_strip_secret(snapshot(obj)),
            request=request,
        )


def _strip_secret(data):
    if data:
        data.pop("password", None)
    return data


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "role", "invited_by", "created_at", "expires_at", "accepted_at")
    list_filter = ("role",)
    search_fields = ("email",)
    fields = ("email", "role", "expires_at", "invitation_link", "accepted_at")
    readonly_fields = ("invitation_link", "accepted_at")

    @admin.display(description="Lien d'invitation")
    def invitation_link(self, obj):
        if not obj.pk:
            return "Généré à l'enregistrement."
        if not obj.is_usable:
            return "Invitation utilisée ou expirée."
        return reverse("accept_invitation", args=[obj.token])

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.accepted_at is None

    def save_model(self, request, obj, form, change):
        if not change:
            obj.invited_by = request.user
            obj.email = obj.email.lower()
        super().save_model(request, obj, form, change)
        if not change:
            log(
                AuditAction.INVITATION_CREATED,
                obj=obj,
                after={"email": obj.email, "role": obj.role},
                request=request,
            )
            link = request.build_absolute_uri(reverse("accept_invitation", args=[obj.token]))
            messages.info(request, f"Lien à transmettre à {obj.email} : {link}")
