from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models.fields.json import KT


class AuditAction(models.TextChoices):
    LOGIN = "login", "Connexion"
    LOGIN_FAILED = "login_failed", "Échec de connexion"
    LOGOUT = "logout", "Déconnexion"
    MFA_ACTIVATED = "mfa_activated", "Double authentification activée"
    MFA_DEACTIVATED = "mfa_deactivated", "Double authentification désactivée"
    INVITATION_CREATED = "invitation_created", "Invitation créée"
    INVITATION_ACCEPTED = "invitation_accepted", "Invitation acceptée"
    VIEW = "view", "Consultation"
    CREATE = "create", "Création"
    UPDATE = "update", "Modification"
    DELETE = "delete", "Suppression"
    DECISION = "decision", "Décision"
    SYNC = "sync", "Synchronisation site"
    EXPORT = "export", "Export"
    AI_CALL = "ai_call", "Appel IA"
    ACCESS_DENIED = "access_denied", "Accès refusé"
    LOT = "lot", "Action en lot"


class AppendOnlyError(Exception):
    """Levée lors d'une tentative de modification ou suppression d'une entrée d'audit."""


class JournalAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Le journal d'audit est en ajout seul.")

    def delete(self):
        raise AppendOnlyError("Le journal d'audit est en ajout seul.")


class JournalAudit(models.Model):
    """Entrée du journal d'audit.

    En ajout seul, protégé à trois niveaux : ``save``/``delete`` du modèle, admin en
    lecture seule et trigger PostgreSQL qui refuse tout UPDATE ou DELETE
    (migration 0002).
    """

    timestamp = models.DateTimeField("horodatage", auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="acteur",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    actor_repr = models.CharField("acteur (libellé)", max_length=255, blank=True)
    action = models.CharField("action", max_length=40, choices=AuditAction.choices, db_index=True)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    object_id = models.CharField("identifiant objet", max_length=64, blank=True, db_index=True)
    object_repr = models.CharField("objet", max_length=255, blank=True)
    before = models.JSONField("avant", null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField("après", null=True, blank=True, encoder=DjangoJSONEncoder)
    ip_address = models.GenericIPAddressField("adresse IP", null=True, blank=True)
    user_agent = models.CharField("navigateur", max_length=255, blank=True)
    metadata = models.JSONField("métadonnées", default=dict, blank=True, encoder=DjangoJSONEncoder)

    objects = JournalAuditQuerySet.as_manager()

    class Meta:
        verbose_name = "entrée du journal d'audit"
        verbose_name_plural = "journal d'audit"
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            # Filtre « dossier » du journal et panneau des consultations (KAN-14, règle 18).
            models.Index(KT("metadata__candidature_id"), name="audit_meta_candidature_idx"),
            # Filtre « acteur » trié par date (KAN-14, règle 18).
            models.Index(fields=["actor", "timestamp"], name="audit_actor_ts_idx"),
            # Reconstitution d'un lot et contrôle de rejeu (KAN-15, règles 19 et 29).
            models.Index(KT("metadata__lot_id"), name="audit_meta_lot_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.actor_repr} {self.action} {self.object_repr}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AppendOnlyError("Le journal d'audit est en ajout seul.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Le journal d'audit est en ajout seul.")
