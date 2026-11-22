from django.db import models

from apps.recruitment.models import Piece


class Extraction(models.Model):
    """Résultat de lecture d'une pièce : texte brut et données structurées."""

    class Methode(models.TextChoices):
        PDF_TEXTE = "pdf_texte", "PDF texte"
        VISION = "vision", "Vision IA (scan/image)"
        MANUELLE = "manuelle", "Saisie manuelle"

    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        TERMINEE = "terminee", "Terminée"
        ECHEC = "echec", "Échec"

    piece = models.ForeignKey(
        Piece, verbose_name="pièce", on_delete=models.CASCADE, related_name="extractions"
    )
    methode = models.CharField("méthode", max_length=20, choices=Methode.choices)
    statut = models.CharField(
        "statut", max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE
    )
    texte = models.TextField("texte extrait", blank=True)
    donnees = models.JSONField("données structurées", default=dict, blank=True)
    modele = models.CharField("modèle IA", max_length=100, blank=True)
    prompt_version = models.CharField("version du prompt", max_length=20, blank=True)
    confiance = models.FloatField("confiance", null=True, blank=True)
    erreur = models.TextField("erreur", blank=True)
    appel_ia = models.ForeignKey(
        "ai.AppelIA", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        verbose_name = "extraction"
        verbose_name_plural = "extractions"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Extraction {self.piece} ({self.get_methode_display()})"
