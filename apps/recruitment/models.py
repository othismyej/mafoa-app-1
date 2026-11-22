from django.db import models
from django.db.models import Q

from apps.evaluation.eligibility.levels import NiveauEtudes

from .normalization import identity_key, normalize_email, normalize_name, normalize_phone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        abstract = True


class SourceRefMixin(models.Model):
    """Référence vers l'objet d'origine sur le site (identifiant + dernière synchronisation)."""

    source_id = models.CharField("identifiant site", max_length=100, blank=True, db_index=True)
    source_synced_at = models.DateTimeField("synchronisé le", null=True, blank=True)

    class Meta:
        abstract = True

    @classmethod
    def unique_source_constraint(cls, name: str) -> models.UniqueConstraint:
        return models.UniqueConstraint(fields=["source_id"], condition=~Q(source_id=""), name=name)


class Fonction(models.TextChoices):
    DIRECTEUR = "directeur", "Directeur"
    CHEF_DEPARTEMENT = "chef_departement", "Chef de département"
    CHEF_DIVISION = "chef_division", "Chef de division"
    CHEF_SERVICE = "chef_service", "Chef de service"
    RESPONSABLE = "responsable", "Responsable"
    INGENIEUR = "ingenieur", "Ingénieur"
    ARCHITECTE = "architecte", "Architecte"
    TECHNICIEN = "technicien", "Technicien"
    AGENT = "agent", "Agent"
    OUVRIER = "ouvrier", "Ouvrier"
    INGENIEUR_CADRE = "ingenieur_cadre", "Ingénieur / Cadre"


class ModeTravail(models.TextChoices):
    PRESENTIEL = "presentiel", "Présentiel"
    HYBRIDE = "hybride", "Hybride"
    TELETRAVAIL = "teletravail", "Télétravail"


class Organisation(SourceRefMixin, TimeStampedModel):
    class Type(models.TextChoices):
        PUBLIC = "public", "Entité publique"
        PRIVE = "prive", "Entreprise privée"

    nom = models.CharField("nom", max_length=255)
    type = models.CharField("type", max_length=10, choices=Type.choices)
    ville = models.CharField("ville", max_length=100, blank=True)

    class Meta:
        verbose_name = "organisation"
        verbose_name_plural = "organisations"
        ordering = ["nom"]
        constraints = [SourceRefMixin.unique_source_constraint("uniq_organisation_source")]

    def __str__(self) -> str:
        return self.nom


class Concours(SourceRefMixin, TimeStampedModel):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        OUVERT = "ouvert", "Postulation ouverte"
        CLOTURE = "cloture", "Postulation terminée"
        EN_EVALUATION = "en_evaluation", "En évaluation"
        TERMINE = "termine", "Terminé"

    organisation = models.ForeignKey(
        Organisation, verbose_name="organisation", on_delete=models.PROTECT, related_name="concours"
    )
    intitule = models.CharField("intitulé", max_length=255)
    slug = models.SlugField("slug", max_length=255, blank=True)
    description = models.TextField("description", blank=True)
    date_publication = models.DateField("date de publication", null=True, blank=True)
    date_limite = models.DateField("date limite", null=True, blank=True)
    statut = models.CharField(
        "statut", max_length=20, choices=Statut.choices, default=Statut.OUVERT
    )

    class Meta:
        verbose_name = "concours"
        verbose_name_plural = "concours"
        ordering = ["-date_limite", "intitule"]
        constraints = [SourceRefMixin.unique_source_constraint("uniq_concours_source")]

    def __str__(self) -> str:
        return self.intitule

    @property
    def nombre_postes_total(self) -> int:
        return sum(self.postes.values_list("nombre_postes", flat=True))


class CriteresEligibiliteMixin(models.Model):
    """Critères d'éligibilité structurés, interprétés par le moteur déterministe."""

    niveau_etudes_min = models.PositiveSmallIntegerField(
        "niveau d'études minimum", choices=NiveauEtudes.choices(), null=True, blank=True
    )
    specialites_acceptees = models.JSONField(
        "spécialités acceptées",
        default=list,
        blank=True,
        help_text="Liste de libellés. Vide : toute spécialité acceptée.",
    )
    experience_min_annees = models.DecimalField(
        "expérience minimale (années)", max_digits=4, decimal_places=1, null=True, blank=True
    )
    experience_max_annees = models.DecimalField(
        "expérience maximale (années)", max_digits=4, decimal_places=1, null=True, blank=True
    )
    autres_criteres = models.TextField(
        "autres critères", blank=True, help_text="Critères non automatisés : vérification humaine."
    )

    class Meta:
        abstract = True


class PosteConcours(SourceRefMixin, CriteresEligibiliteMixin, TimeStampedModel):
    concours = models.ForeignKey(
        Concours, verbose_name="concours", on_delete=models.CASCADE, related_name="postes"
    )
    intitule = models.CharField("intitulé", max_length=255)
    reference = models.CharField("référence", max_length=50, blank=True)
    slug = models.SlugField("slug", max_length=255, blank=True)
    secteur = models.CharField("secteur", max_length=150, blank=True)
    fonction = models.CharField("fonction", max_length=30, choices=Fonction.choices, blank=True)
    nombre_postes = models.PositiveIntegerField("nombre de postes", default=1)
    mode_travail = models.CharField(
        "mode de travail",
        max_length=20,
        choices=ModeTravail.choices,
        default=ModeTravail.PRESENTIEL,
    )
    ville = models.CharField("ville", max_length=100, blank=True)
    description = models.TextField("description", blank=True)
    missions = models.TextField("missions", blank=True)
    date_publication = models.DateField("date de publication", null=True, blank=True)
    date_limite = models.DateField("date limite", null=True, blank=True)

    class Meta:
        verbose_name = "poste de concours"
        verbose_name_plural = "postes de concours"
        ordering = ["concours", "reference", "intitule"]
        constraints = [SourceRefMixin.unique_source_constraint("uniq_posteconcours_source")]

    def __str__(self) -> str:
        return f"{self.reference} — {self.intitule}" if self.reference else self.intitule

    @property
    def date_reference(self):
        """Date à laquelle l'expérience est calculée : date limite du poste, sinon du concours."""
        return self.date_limite or self.concours.date_limite


class OffreEmploi(SourceRefMixin, CriteresEligibiliteMixin, TimeStampedModel):
    class Statut(models.TextChoices):
        OUVERTE = "ouverte", "Ouverte"
        FERMEE = "fermee", "Fermée"
        POURVUE = "pourvue", "Pourvue"

    organisation = models.ForeignKey(
        Organisation, verbose_name="organisation", on_delete=models.PROTECT, related_name="offres"
    )
    entreprise_confidentielle = models.BooleanField(
        "entreprise confidentielle", default=False, help_text="Affichée « Confidential Company »."
    )
    intitule = models.CharField("intitulé", max_length=255)
    slug = models.SlugField("slug", max_length=255, blank=True)
    ville = models.CharField("ville", max_length=100, blank=True)
    type_contrat = models.CharField("type de contrat", max_length=30, blank=True)
    mode_travail = models.CharField(
        "mode de travail",
        max_length=20,
        choices=ModeTravail.choices,
        default=ModeTravail.PRESENTIEL,
    )
    secteur = models.CharField("secteur", max_length=150, blank=True)
    fonction = models.CharField("fonction", max_length=30, choices=Fonction.choices, blank=True)
    nombre_postes = models.PositiveIntegerField("nombre de postes", default=1)
    salaire = models.CharField("salaire", max_length=100, blank=True)
    fiche_de_poste = models.TextField("fiche de poste", blank=True)
    competences = models.JSONField("compétences attendues", default=list, blank=True)
    date_publication = models.DateField("date de publication", null=True, blank=True)
    date_limite = models.DateField("date limite", null=True, blank=True)
    statut = models.CharField(
        "statut", max_length=20, choices=Statut.choices, default=Statut.OUVERTE
    )

    class Meta:
        verbose_name = "offre d'emploi"
        verbose_name_plural = "offres d'emploi"
        ordering = ["-date_publication", "intitule"]
        constraints = [SourceRefMixin.unique_source_constraint("uniq_offreemploi_source")]

    def __str__(self) -> str:
        return self.intitule

    @property
    def date_reference(self):
        return self.date_limite


class Candidat(SourceRefMixin, TimeStampedModel):
    """Identité et contacts d'un candidat.

    ``sexe`` et ``region`` ne doivent JAMAIS servir de critère d'évaluation. Ils sont
    conservés uniquement pour le contrôle des écarts (biais) prévu en vague 2.
    """

    class Sexe(models.TextChoices):
        FEMME = "F", "Femme"
        HOMME = "M", "Homme"

    nom = models.CharField("nom", max_length=150)
    prenom = models.CharField("prénom", max_length=150)
    email = models.EmailField("email", blank=True)
    telephone = models.CharField("téléphone", max_length=30, blank=True)
    date_naissance = models.DateField("date de naissance", null=True, blank=True)
    cin = models.CharField("CIN", max_length=20, blank=True)
    ville = models.CharField("ville", max_length=100, blank=True)
    region = models.CharField("région (contrôle des biais uniquement)", max_length=100, blank=True)
    sexe = models.CharField(
        "sexe (contrôle des biais uniquement)", max_length=1, choices=Sexe.choices, blank=True
    )

    # Clés de rapprochement pour la détection des doublons (calculées).
    email_normalise = models.CharField(max_length=255, blank=True, db_index=True, editable=False)
    telephone_normalise = models.CharField(max_length=30, blank=True, db_index=True, editable=False)
    nom_normalise = models.CharField(max_length=300, blank=True, db_index=True, editable=False)
    cle_identite = models.CharField(max_length=320, blank=True, db_index=True, editable=False)

    class Meta:
        verbose_name = "candidat"
        verbose_name_plural = "candidats"
        ordering = ["nom", "prenom"]
        constraints = [SourceRefMixin.unique_source_constraint("uniq_candidat_source")]

    def __str__(self) -> str:
        return f"{self.nom.upper()} {self.prenom}"

    def save(self, *args, **kwargs):
        self.email_normalise = normalize_email(self.email)
        self.telephone_normalise = normalize_phone(self.telephone)
        self.nom_normalise = normalize_name(self.nom, self.prenom)
        self.cle_identite = identity_key(self.nom, self.prenom, self.date_naissance)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {
                "email_normalise",
                "telephone_normalise",
                "nom_normalise",
                "cle_identite",
            }
        super().save(*args, **kwargs)


class Candidature(SourceRefMixin, TimeStampedModel):
    class Statut(models.TextChoices):
        RECUE = "recue", "Reçue"
        EN_ANALYSE = "en_analyse", "En analyse"
        ELIGIBLE = "eligible", "Éligible"
        NON_ELIGIBLE = "non_eligible", "Non éligible"
        A_VERIFIER = "a_verifier", "À vérifier"
        PRESELECTIONNEE = "preselectionnee", "Présélectionnée"
        CONVOQUEE = "convoquee", "Convoquée"
        RETENUE = "retenue", "Retenue"
        REJETEE = "rejetee", "Rejetée"

    candidat = models.ForeignKey(
        Candidat, verbose_name="candidat", on_delete=models.PROTECT, related_name="candidatures"
    )
    poste_concours = models.ForeignKey(
        PosteConcours,
        verbose_name="poste de concours",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="candidatures",
    )
    offre = models.ForeignKey(
        OffreEmploi,
        verbose_name="offre d'emploi",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="candidatures",
    )
    # Dénormalisé depuis poste_concours pour garantir « un seul poste par concours ».
    concours = models.ForeignKey(
        Concours,
        verbose_name="concours",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="candidatures",
        editable=False,
    )
    date_candidature = models.DateTimeField("date de candidature")
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.RECUE)
    donnees_declarees = models.JSONField(
        "données déclarées",
        default=dict,
        blank=True,
        help_text="Formation et expériences saisies par le candidat sur le site.",
    )

    class Meta:
        verbose_name = "candidature"
        verbose_name_plural = "candidatures"
        ordering = ["-date_candidature"]
        constraints = [
            SourceRefMixin.unique_source_constraint("uniq_candidature_source"),
            models.CheckConstraint(
                condition=(Q(poste_concours__isnull=False) & Q(offre__isnull=True))
                | (Q(poste_concours__isnull=True) & Q(offre__isnull=False)),
                name="candidature_poste_xor_offre",
                violation_error_message="Une candidature vise soit un poste de concours, soit une offre.",
            ),
            models.UniqueConstraint(
                fields=["candidat", "concours"],
                condition=Q(concours__isnull=False),
                name="uniq_candidature_un_poste_par_concours",
                violation_error_message="Un candidat ne peut postuler qu'à un seul poste par concours.",
            ),
            models.UniqueConstraint(
                fields=["candidat", "offre"],
                condition=Q(offre__isnull=False),
                name="uniq_candidature_par_offre",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.candidat} → {self.cible}"

    def save(self, *args, **kwargs):
        self.concours_id = self.poste_concours.concours_id if self.poste_concours_id else None
        super().save(*args, **kwargs)

    @property
    def cible(self):
        return self.poste_concours or self.offre


class Piece(SourceRefMixin, TimeStampedModel):
    """Pièce jointe. Le fichier reste dans Spaces : seule la référence est stockée."""

    class Type(models.TextChoices):
        CV = "cv", "CV"
        DIPLOME = "diplome", "Diplôme"
        ATTESTATION_TRAVAIL = "attestation_travail", "Attestation de travail"
        LETTRE = "lettre", "Lettre de motivation"
        AUTRE = "autre", "Autre"

    candidature = models.ForeignKey(
        Candidature, verbose_name="candidature", on_delete=models.CASCADE, related_name="pieces"
    )
    type = models.CharField("type", max_length=30, choices=Type.choices)
    nom_fichier = models.CharField("nom du fichier", max_length=255)
    storage_key = models.CharField(
        "référence du fichier", max_length=500, help_text="Clé Spaces (ou fixture://…)."
    )
    content_type = models.CharField("type MIME", max_length=100, blank=True)
    taille = models.PositiveIntegerField("taille (octets)", null=True, blank=True)
    sha256 = models.CharField("empreinte SHA-256", max_length=64, blank=True, db_index=True)
    recue_le = models.DateTimeField("reçue le", null=True, blank=True)

    class Meta:
        verbose_name = "pièce"
        verbose_name_plural = "pièces"
        ordering = ["candidature", "type"]
        constraints = [SourceRefMixin.unique_source_constraint("uniq_piece_source")]

    def __str__(self) -> str:
        return f"{self.get_type_display()} — {self.nom_fichier}"
