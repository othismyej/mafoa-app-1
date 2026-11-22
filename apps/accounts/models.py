import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = "admin", "Administrateur (direction générale)"
    DIRECTION = "direction", "Direction (associés et directeurs)"
    # Vague 2 : déclarés pour le modèle de données, non attribuables pour l'instant.
    CONSULTANT = "consultant", "Consultant (vague 2)"
    CLIENT = "client", "Client (vague 2)"


ACTIVE_ROLES = (Role.ADMIN, Role.DIRECTION)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("L'email est obligatoire.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra["role"] = Role.ADMIN
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Utilisateur interne Mafoa. Identifiant : email. Aucune inscription publique."""

    email = models.EmailField("email", unique=True)
    full_name = models.CharField("nom complet", max_length=150, blank=True)
    role = models.CharField("rôle", max_length=20, choices=Role.choices, default=Role.DIRECTION)
    is_active = models.BooleanField("actif", default=True)
    is_staff = models.BooleanField(
        "accès à l'administration",
        default=False,
        help_text="Calculé automatiquement : réservé au rôle administrateur.",
    )
    is_superuser = models.BooleanField(
        "tous les droits",
        default=False,
        help_text="Calculé automatiquement : réservé au rôle administrateur.",
    )
    date_joined = models.DateTimeField("date de création", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"
        ordering = ["email"]

    def __str__(self) -> str:
        return self.full_name or self.email

    def save(self, *args, **kwargs):
        # Les droits d'administration découlent du rôle, jamais d'un réglage manuel.
        self.is_staff = self.is_superuser = self.role == Role.ADMIN
        super().save(*args, **kwargs)

    @property
    def is_admin(self) -> bool:
        return self.is_active and self.role == Role.ADMIN

    @property
    def is_direction(self) -> bool:
        return self.is_active and self.role in ACTIVE_ROLES

    def has_role(self, *roles: str) -> bool:
        return self.is_active and self.role in roles


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _default_expiry():
    return timezone.now() + timedelta(hours=72)


class Invitation(models.Model):
    """Invitation à usage unique créée par un administrateur."""

    email = models.EmailField("email")
    role = models.CharField(
        "rôle",
        max_length=20,
        choices=[(r.value, r.label) for r in ACTIVE_ROLES],
        default=Role.DIRECTION,
    )
    token = models.CharField(max_length=64, unique=True, default=_new_token, editable=False)
    invited_by = models.ForeignKey(
        User,
        verbose_name="invité par",
        on_delete=models.PROTECT,
        related_name="invitations_sent",
    )
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    expires_at = models.DateTimeField("expire le", default=_default_expiry)
    accepted_at = models.DateTimeField("acceptée le", null=True, blank=True)
    accepted_user = models.OneToOneField(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invitation",
        editable=False,
    )

    class Meta:
        verbose_name = "invitation"
        verbose_name_plural = "invitations"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Invitation {self.email} ({self.get_role_display()})"

    @property
    def is_usable(self) -> bool:
        return self.accepted_at is None and self.expires_at > timezone.now()
