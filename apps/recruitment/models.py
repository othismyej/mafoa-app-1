from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.evaluation.eligibility.levels import NiveauEtudes

from .normalization import (
    identity_key,
    normalize_cin,
    normalize_email,
    normalize_name,
    normalize_phone,
)


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


def valider_pieces_obligatoires(value) -> None:
    """Pièces obligatoires (KAN-42, règle 14) : liste de types de ``Piece.Type`` hors « autre »."""
    if value is None:
        return
    if not isinstance(value, list):
        raise ValidationError(
            'Liste de types de pièce attendue (ex. ["cv", "diplome"]).', code="invalid"
        )
    permis = set(Piece.TYPES_OBLIGATOIRES)
    refuses = [v for v in value if not isinstance(v, str) or v not in permis]
    if refuses:
        raise ValidationError(
            "Type de pièce non permis : %(v)s. Valeurs possibles : %(p)s.",
            code="invalid",
            params={"v": ", ".join(map(str, refuses)), "p": ", ".join(Piece.TYPES_OBLIGATOIRES)},
        )
    if len(set(value)) != len(value):
        raise ValidationError("Un type de pièce est répété.", code="invalid")


class PiecesObligatoiresMixin(models.Model):
    """Pièces obligatoires d'un concours, d'un poste ou d'une offre (KAN-42, règle 14).

    ``None`` = valeur par défaut (héritée du concours pour un poste, puis paramètre
    ``PIECES_OBLIGATOIRES_DEFAUT``) ; ``[]`` = aucune pièce obligatoire.
    """

    pieces_obligatoires = models.JSONField(
        "pièces obligatoires",
        null=True,
        blank=True,
        default=None,
        validators=[valider_pieces_obligatoires],
        help_text=(
            'Liste de types de pièce, ex. ["cv", "diplome", "attestation_travail"]. '
            "Vide : valeur par défaut. [] : aucune pièce obligatoire."
        ),
    )

    class Meta:
        abstract = True


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


class Concours(SourceRefMixin, PiecesObligatoiresMixin, TimeStampedModel):
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


class PosteConcours(
    SourceRefMixin, CriteresEligibiliteMixin, PiecesObligatoiresMixin, TimeStampedModel
):
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


class OffreEmploi(
    SourceRefMixin, CriteresEligibiliteMixin, PiecesObligatoiresMixin, TimeStampedModel
):
    class Statut(models.TextChoices):
        OUVERTE = "ouverte", "Ouverte"
        FERMEE = "fermee", "Fermée"
        POURVUE = "pourvue", "Pourvue"

    organisation = models.ForeignKey(
        Organisation, verbose_name="organisation", on_delete=models.PROTECT, related_name="offres"
    )
    entreprise_confidentielle = models.BooleanField(
        "entreprise confidentielle",
        default=False,
        help_text=(
            "Affichée « Entreprise confidentielle » : le nom de l’organisation n’est jamais montré."
        ),
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
    # KAN-42, Q3 bis révisée : la CIN est une clé de rapprochement (jamais affichée ni
    # journalisée par la détection).
    cin_normalise = models.CharField(max_length=20, blank=True, db_index=True, editable=False)

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
        self.cin_normalise = normalize_cin(self.cin)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {
                "email_normalise",
                "telephone_normalise",
                "nom_normalise",
                "cle_identite",
                "cin_normalise",
            }
        super().save(*args, **kwargs)


class CandidatureQuerySet(models.QuerySet):
    def bulk_create(self, objs, *args, **kwargs):
        # Même commodité que ``Candidature.save()`` (règle 3) : jamais « spontanée » par défaut.
        objs = list(objs)
        for obj in objs:
            obj.renseigner_type()
        return super().bulk_create(objs, *args, **kwargs)


class Candidature(SourceRefMixin, TimeStampedModel):
    class Statut(models.TextChoices):
        RECUE = "recue", _("Reçue")
        EN_ANALYSE = "en_analyse", _("En analyse")
        ELIGIBLE = "eligible", _("Éligible")
        NON_ELIGIBLE = "non_eligible", _("Non éligible")
        A_VERIFIER = "a_verifier", _("À vérifier")
        PRESELECTIONNEE = "preselectionnee", _("Présélectionnée")
        CONVOQUEE = "convoquee", _("Convoquée")
        RETENUE = "retenue", _("Retenue")
        REJETEE = "rejetee", _("Rejetée")
        # Candidatures spontanées uniquement (KAN-39, règle 4).
        VIVIER = "vivier", _("Conservée au vivier")

    class Type(models.TextChoices):
        """Nature de la candidature (KAN-39, règle 1) : explicite, jamais déduite « spontanée »."""

        CONCOURS = "concours", _("Concours")
        OFFRE = "offre", _("Offre d’emploi")
        SPONTANEE = "spontanee", _("Candidature spontanée")

    #: Statuts permis pour une candidature spontanée (règle 5) ; les autres types n'ont jamais
    #: le statut « vivier ».
    STATUTS_SPONTANEE = ("recue", "en_analyse", "vivier", "rejetee")

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
    # Renseigné à la création, jamais modifié ensuite (règle 14) ; ``save()`` ne déduit que
    # « concours » ou « offre », jamais « spontanée » (règle 3).
    type = models.CharField("type", max_length=10, choices=Type.choices, db_index=True)
    date_candidature = models.DateTimeField("date de candidature")
    statut = models.CharField("statut", max_length=20, choices=Statut.choices, default=Statut.RECUE)
    donnees_declarees = models.JSONField(
        "données déclarées",
        default=dict,
        blank=True,
        help_text="Formation et expériences saisies par le candidat sur le site.",
    )
    # Dernière exécution complète du détecteur d'anomalies pour ce dossier (KAN-42, règle 20).
    anomalies_verifiees_le = models.DateTimeField(
        "anomalies vérifiées le", null=True, blank=True, editable=False
    )

    objects = CandidatureQuerySet.as_manager()

    class Meta:
        verbose_name = "candidature"
        verbose_name_plural = "candidatures"
        ordering = ["-date_candidature"]
        constraints = [
            SourceRefMixin.unique_source_constraint("uniq_candidature_source"),
            # Remplace « candidature_poste_xor_offre » (KAN-39, règle 2).
            models.CheckConstraint(
                condition=(
                    Q(type="concours", poste_concours__isnull=False, offre__isnull=True)
                    | Q(type="offre", offre__isnull=False, poste_concours__isnull=True)
                    | Q(
                        type="spontanee",
                        poste_concours__isnull=True,
                        offre__isnull=True,
                        concours__isnull=True,
                    )
                ),
                name="candidature_type_cible",
                violation_error_message=(
                    "Type et cible incohérents : un poste pour un concours, une offre pour une "
                    "offre d'emploi, ni l'un ni l'autre pour une candidature spontanée."
                ),
            ),
            models.CheckConstraint(
                condition=(
                    Q(type="spontanee", statut__in=["recue", "en_analyse", "vivier", "rejetee"])
                    | (~Q(type="spontanee") & ~Q(statut="vivier"))
                ),
                name="candidature_statut_selon_type",
                violation_error_message="Statut non permis pour ce type de candidature.",
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
        indexes = [
            models.Index(fields=["type", "date_candidature"], name="candidature_type_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.candidat} → {self.cible_libelle}"

    def save(self, *args, **kwargs):
        self.renseigner_type()
        self.concours_id = self.poste_concours.concours_id if self.poste_concours_id else None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            # Ex. update_or_create : le concours dérivé doit suivre le poste.
            kwargs["update_fields"] = set(update_fields) | {"concours"}
        super().save(*args, **kwargs)

    def renseigner_type(self) -> None:
        """Commodité (règle 3) : type vide → « concours » si poste, « offre » si offre ; jamais
        « spontanée ». Sans cible ni type, la contrainte de base refuse l'enregistrement."""
        if not self.type:
            if self.poste_concours_id:
                self.type = self.Type.CONCOURS
            elif self.offre_id:
                self.type = self.Type.OFFRE

    @property
    def cible(self):
        return self.poste_concours or self.offre

    @property
    def est_spontanee(self) -> bool:
        return self.type == self.Type.SPONTANEE

    @property
    def cible_libelle(self) -> str:
        """Cible en texte : poste, offre ou « Candidature spontanée » (règle 9)."""
        cible = self.cible
        if cible is None and self.est_spontanee:
            return str(self.Type.SPONTANEE.label)
        return str(cible)

    @classmethod
    def statut_choices_avec_cible(cls) -> list[tuple[str, str]]:
        """Statuts possibles d'une candidature de concours ou d'offre (sans « vivier »)."""
        return [(v, label) for v, label in cls.Statut.choices if v != cls.Statut.VIVIER]

    @classmethod
    def statut_choices_spontanee(cls) -> list[tuple[str, str]]:
        return [(v, cls.Statut(v).label) for v in cls.STATUTS_SPONTANEE]


class DetailsSpontanee(TimeStampedModel):
    """Souhaits d'une candidature spontanée (KAN-39, règle 7), un-à-un avec la candidature.

    Structure à confirmer avec le code source du site (QS2). Aucun champ sur le sexe, l'âge,
    la situation familiale ou la photo.
    """

    MESSAGE_MAX = 5000

    candidature = models.OneToOneField(
        Candidature,
        verbose_name="candidature",
        on_delete=models.CASCADE,
        related_name="details_spontanee",
    )
    fonction_souhaitee = models.CharField(
        "fonction souhaitée", max_length=30, choices=Fonction.choices, blank=True
    )
    fonction_souhaitee_brute = models.CharField(
        "fonction souhaitée (libellé du site)",
        max_length=150,
        blank=True,
        help_text="Libellé d'origine quand il ne correspond à aucune fonction connue.",
    )
    secteur_souhaite = models.CharField("secteur souhaité", max_length=150, blank=True)
    lieu_souhaite = models.CharField(
        "lieu de travail souhaité",
        max_length=100,
        blank=True,
        help_text="Préférence de mobilité déclarée, distincte de la ville de résidence.",
    )
    disponibilite = models.CharField("disponibilité", max_length=100, blank=True)
    message = models.TextField("message du candidat", blank=True)
    message_tronque = models.BooleanField("message tronqué", default=False)
    consentement_vivier = models.BooleanField(
        "consentement au vivier", null=True, blank=True, help_text="Vide : non renseigné."
    )
    consentement_le = models.DateTimeField("consentement le", null=True, blank=True)
    mise_a_jour_source_le = models.DateTimeField(
        "dernière mise à jour à la source",
        null=True,
        blank=True,
        help_text="Point de départ de la conservation si le site la fournit (QS3).",
    )

    class Meta:
        verbose_name = "souhaits d'une candidature spontanée"
        verbose_name_plural = "souhaits des candidatures spontanées"
        indexes = [
            models.Index(fields=["fonction_souhaitee"], name="spontanee_fonction_idx"),
            models.Index(fields=["secteur_souhaite"], name="spontanee_secteur_idx"),
            models.Index(fields=["lieu_souhaite"], name="spontanee_lieu_idx"),
        ]

    def __str__(self) -> str:
        return f"Souhaits — Candidature #{self.candidature_id}"

    def save(self, *args, **kwargs):
        if self.candidature.type != Candidature.Type.SPONTANEE:
            raise ValueError("Les souhaits ne concernent que les candidatures spontanées.")
        super().save(*args, **kwargs)


class Piece(SourceRefMixin, TimeStampedModel):
    """Pièce jointe. Le fichier reste dans Spaces : seule la référence est stockée."""

    class Type(models.TextChoices):
        CV = "cv", "CV"
        DIPLOME = "diplome", "Diplôme"
        ATTESTATION_TRAVAIL = "attestation_travail", "Attestation de travail"
        LETTRE = "lettre", "Lettre de motivation"
        AUTRE = "autre", "Autre"

    #: Types pouvant être exigés (KAN-42, règle 14) : « autre » ne remplace jamais une pièce.
    TYPES_OBLIGATOIRES = ("cv", "diplome", "attestation_travail", "lettre")

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
