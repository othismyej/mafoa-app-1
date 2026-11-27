from django.conf import settings
from django.db import models

from apps.recruitment.models import Piece


class Extraction(models.Model):
    """Résultat de lecture d'une pièce : texte brut et données structurées (KAN-41).

    Une extraction « en attente » est créée à la mise en file ; la tâche Celery la complète.
    Les extractions précédentes d'une pièce sont conservées (historique des lectures).
    """

    class Methode(models.TextChoices):
        PDF_TEXTE = "pdf_texte", "PDF texte"
        VISION = "vision", "Vision IA (scan/image)"
        MANUELLE = "manuelle", "Saisie manuelle"

    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        TERMINEE = "terminee", "Terminée"
        ECHEC = "echec", "Échec"
        IGNOREE = "ignoree", "Ignorée"

    class Origine(models.TextChoices):
        FICHE = "fiche", "Fiche candidature"
        SYNCHRONISATION = "synchronisation", "Synchronisation du site"

    piece = models.ForeignKey(
        Piece, verbose_name="pièce", on_delete=models.CASCADE, related_name="extractions"
    )
    type_piece = models.CharField("type de pièce lu", max_length=30, blank=True)
    methode = models.CharField("méthode", max_length=20, choices=Methode.choices, blank=True)
    statut = models.CharField(
        "statut", max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE
    )
    #: Motif d'un échec ou d'une lecture ignorée (``apps.documents.lecture.MOTIFS``).
    code = models.CharField("motif", max_length=40, blank=True)
    texte = models.TextField("texte extrait", blank=True)
    donnees = models.JSONField("données structurées", default=dict, blank=True)
    modele = models.CharField("modèle IA", max_length=100, blank=True)
    prompt_nom = models.CharField("prompt", max_length=100, blank=True)
    prompt_version = models.CharField("version du prompt", max_length=20, blank=True)
    empreinte = models.CharField("empreinte du contenu lu", max_length=64, blank=True)
    confiance = models.FloatField("confiance", null=True, blank=True)
    erreur = models.TextField("erreur", blank=True)
    appel_ia = models.ForeignKey(
        "ai.AppelIA", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    forcee = models.BooleanField("relecture forcée", default=False)
    origine = models.CharField(
        "origine", max_length=20, choices=Origine.choices, default=Origine.FICHE
    )
    declenchee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="déclenchée par",
    )
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    terminee_le = models.DateTimeField("terminée le", null=True, blank=True)

    class Meta:
        verbose_name = "extraction"
        verbose_name_plural = "extractions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["piece", "statut", "-created_at"], name="extraction_piece_statut_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"Extraction #{self.pk} — pièce #{self.piece_id} ({self.get_statut_display()})"
