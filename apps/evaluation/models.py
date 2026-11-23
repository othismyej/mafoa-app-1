from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.recruitment.models import Candidature, Concours, OffreEmploi, Piece, PosteConcours


class Verdict(models.TextChoices):
    ELIGIBLE = "eligible", "Éligible"
    NON_ELIGIBLE = "non_eligible", "Non éligible"
    A_VERIFIER = "a_verifier", "À vérifier"


class GrilleEvaluation(models.Model):
    """Grille de notation configurable, rattachée à un concours, un poste ou une offre."""

    nom = models.CharField("nom", max_length=255)
    concours = models.ForeignKey(
        Concours, null=True, blank=True, on_delete=models.CASCADE, related_name="grilles"
    )
    poste_concours = models.ForeignKey(
        PosteConcours, null=True, blank=True, on_delete=models.CASCADE, related_name="grilles"
    )
    offre = models.ForeignKey(
        OffreEmploi, null=True, blank=True, on_delete=models.CASCADE, related_name="grilles"
    )
    version = models.PositiveIntegerField("version", default=1)
    active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        verbose_name = "grille d'évaluation"
        verbose_name_plural = "grilles d'évaluation"
        ordering = ["nom", "-version"]

    def __str__(self) -> str:
        return f"{self.nom} (v{self.version})"


class Critere(models.Model):
    grille = models.ForeignKey(
        GrilleEvaluation, verbose_name="grille", on_delete=models.CASCADE, related_name="criteres"
    )
    libelle = models.CharField("libellé", max_length=255)
    description = models.TextField("description", blank=True)
    ponderation = models.DecimalField("pondération", max_digits=5, decimal_places=2, default=1)
    bareme_max = models.DecimalField("note maximale", max_digits=5, decimal_places=2, default=20)
    consignes_ia = models.TextField("consignes pour l'IA", blank=True)
    ordre = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        verbose_name = "critère"
        verbose_name_plural = "critères"
        ordering = ["grille", "ordre", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(ponderation__gte=0), name="critere_ponderation_pos"),
            models.CheckConstraint(condition=Q(bareme_max__gt=0), name="critere_bareme_pos"),
        ]

    def __str__(self) -> str:
        return self.libelle


class VerificationEligibilite(models.Model):
    """Résultat du moteur d'éligibilité (déterministe), avec le détail critère par critère."""

    candidature = models.ForeignKey(
        Candidature, on_delete=models.CASCADE, related_name="verifications_eligibilite"
    )
    resultat = models.CharField("résultat", max_length=20, choices=Verdict.choices)
    details = models.JSONField(
        "détail par critère",
        default=list,
        help_text="Liste de {critere, verdict, raison, attendu, constate}.",
    )
    justification = models.TextField("justification", blank=True)
    sources = models.JSONField("sources", default=list, blank=True)
    moteur_version = models.CharField("version du moteur", max_length=20)
    created_at = models.DateTimeField("date", auto_now_add=True)

    class Meta:
        verbose_name = "vérification d'éligibilité"
        verbose_name_plural = "vérifications d'éligibilité"
        ordering = ["-created_at"]
        indexes = [
            # Dernière vérification d'une candidature (listes et fiches concours).
            models.Index(
                fields=["candidature", "-created_at", "-id"], name="verif_cand_dernier_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.candidature} : {self.get_resultat_display()}"


class Score(models.Model):
    class Origine(models.TextChoices):
        IA = "ia", "Proposition IA"
        HUMAIN = "humain", "Saisie humaine"

    candidature = models.ForeignKey(Candidature, on_delete=models.CASCADE, related_name="scores")
    grille = models.ForeignKey(GrilleEvaluation, on_delete=models.PROTECT, related_name="scores")
    note_globale = models.DecimalField("note globale", max_digits=6, decimal_places=2)
    origine = models.CharField("origine", max_length=10, choices=Origine.choices)
    modele = models.CharField("modèle IA", max_length=100, blank=True)
    prompt_version = models.CharField("version du prompt", max_length=20, blank=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField("date", auto_now_add=True)

    class Meta:
        verbose_name = "score"
        verbose_name_plural = "scores"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.candidature} : {self.note_globale}"


class ScoreCritere(models.Model):
    score = models.ForeignKey(Score, on_delete=models.CASCADE, related_name="notes")
    critere = models.ForeignKey(Critere, on_delete=models.PROTECT, related_name="+")
    note = models.DecimalField("note", max_digits=5, decimal_places=2)
    justification = models.TextField("justification")
    extrait = models.TextField("extrait cité du dossier", blank=True)
    piece = models.ForeignKey(
        Piece, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "note par critère"
        verbose_name_plural = "notes par critère"
        constraints = [
            models.UniqueConstraint(fields=["score", "critere"], name="uniq_score_critere"),
        ]

    def __str__(self) -> str:
        return f"{self.critere} : {self.note}"


class Anomalie(models.Model):
    class Type(models.TextChoices):
        PIECE_MANQUANTE = "piece_manquante", "Pièce manquante"
        DOUBLON_CANDIDAT = "doublon_candidat", "Doublon de candidat"
        DOUBLON_PIECE = "doublon_piece", "Pièce en double"
        INCOHERENCE_DATES = "incoherence_dates", "Incohérence de dates"
        INCOHERENCE_NOM = "incoherence_nom", "Incohérence de nom"
        EMPREINTE_DIFFERENTE = "empreinte_differente", "Empreinte de pièce différente"
        AUTRE = "autre", "Autre"

    class Gravite(models.TextChoices):
        INFO = "info", "Information"
        MOYENNE = "moyenne", "Moyenne"
        HAUTE = "haute", "Haute"

    candidature = models.ForeignKey(Candidature, on_delete=models.CASCADE, related_name="anomalies")
    piece = models.ForeignKey(
        Piece, null=True, blank=True, on_delete=models.SET_NULL, related_name="anomalies"
    )
    candidature_liee = models.ForeignKey(
        Candidature,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Pour un doublon : l'autre candidature concernée.",
    )
    type = models.CharField("type", max_length=30, choices=Type.choices)
    gravite = models.CharField("gravité", max_length=10, choices=Gravite.choices)
    description = models.TextField("description")
    resolue = models.BooleanField("résolue", default=False)
    resolue_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    resolue_le = models.DateTimeField("résolue le", null=True, blank=True)
    created_at = models.DateTimeField("détectée le", auto_now_add=True)

    class Meta:
        verbose_name = "anomalie"
        verbose_name_plural = "anomalies"
        ordering = ["resolue", "-created_at"]
        indexes = [
            models.Index(fields=["candidature", "resolue"], name="anomalie_cand_resolue_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_type_display()} — {self.candidature}"


class Decision(models.Model):
    """Décision sur une candidature. Toujours prise par un humain, motif obligatoire.

    Une décision n'est jamais modifiée : une nouvelle décision remplace la précédente.
    """

    candidature = models.ForeignKey(Candidature, on_delete=models.PROTECT, related_name="decisions")
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="auteur",
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    decision = models.CharField("décision", max_length=20, choices=Candidature.Statut.choices)
    motif = models.TextField("motif")
    created_at = models.DateTimeField("date", auto_now_add=True)

    class Meta:
        verbose_name = "décision"
        verbose_name_plural = "décisions"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(motif__regex=r"^\s*$"), name="decision_motif_obligatoire"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.candidature} : {self.get_decision_display()} ({self.auteur})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Une décision ne se modifie pas : enregistrez une nouvelle décision.")
        if not (self.motif or "").strip():
            raise ValueError("Le motif de la décision est obligatoire.")
        super().save(*args, **kwargs)
