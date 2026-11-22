from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class AppelIA(models.Model):
    """Trace de chaque appel à l'API Claude (modèle, prompt, entrée pseudonymisée, sortie, durée)."""

    class Statut(models.TextChoices):
        SUCCES = "succes", "Succès"
        ERREUR = "erreur", "Erreur"

    created_at = models.DateTimeField("date", auto_now_add=True, db_index=True)
    tache = models.CharField("tâche", max_length=100)
    modele = models.CharField("modèle", max_length=100)
    prompt_nom = models.CharField("prompt", max_length=100)
    prompt_version = models.CharField("version du prompt", max_length=20)
    pseudonymise = models.BooleanField("entrée pseudonymisée", default=True)
    entree = models.JSONField("entrée", encoder=DjangoJSONEncoder)
    sortie = models.JSONField("sortie", null=True, blank=True, encoder=DjangoJSONEncoder)
    statut = models.CharField("statut", max_length=10, choices=Statut.choices)
    erreur = models.TextField("erreur", blank=True)
    duree_ms = models.PositiveIntegerField("durée (ms)", default=0)
    tokens_entree = models.PositiveIntegerField(null=True, blank=True)
    tokens_sortie = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "appel IA"
        verbose_name_plural = "appels IA"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.tache} ({self.modele})"
