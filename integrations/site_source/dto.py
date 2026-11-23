"""Objets neutres échangés entre une source (site) et l'application.

Le reste de l'application ne dépend que de ces DTO, jamais de la manière dont le site
est lu (fixtures, base en lecture seule, API REST).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class OrganisationDTO:
    source_id: str
    nom: str
    type: str  # "public" | "prive"
    ville: str = ""


@dataclass(frozen=True)
class ConcoursDTO:
    source_id: str
    organisation_source_id: str
    intitule: str
    slug: str
    description: str
    date_publication: date | None
    date_limite: date | None
    statut: str


@dataclass(frozen=True)
class PosteConcoursDTO:
    source_id: str
    concours_source_id: str
    reference: str
    intitule: str
    slug: str
    secteur: str
    fonction: str
    nombre_postes: int
    mode_travail: str
    ville: str
    description: str
    missions: str
    date_publication: date | None
    date_limite: date | None
    niveau_etudes_min: int | None
    specialites_acceptees: list[str] = field(default_factory=list)
    experience_min_annees: Decimal | None = None
    experience_max_annees: Decimal | None = None
    autres_criteres: str = ""


@dataclass(frozen=True)
class OffreEmploiDTO:
    source_id: str
    organisation_source_id: str
    intitule: str
    slug: str
    entreprise_confidentielle: bool
    ville: str
    type_contrat: str
    mode_travail: str
    secteur: str
    fonction: str
    nombre_postes: int
    salaire: str
    fiche_de_poste: str
    competences: list[str]
    date_publication: date | None
    date_limite: date | None
    statut: str
    niveau_etudes_min: int | None
    specialites_acceptees: list[str] = field(default_factory=list)
    experience_min_annees: Decimal | None = None
    experience_max_annees: Decimal | None = None


@dataclass(frozen=True)
class CandidatDTO:
    source_id: str
    nom: str
    prenom: str
    email: str
    telephone: str
    date_naissance: date | None
    cin: str
    ville: str
    region: str = ""
    sexe: str = ""


@dataclass(frozen=True)
class SouhaitsDTO:
    """Souhaits d'une candidature spontanée (KAN-39, règle 11), tous facultatifs.

    **À confirmer avec le code source du site (QS2, QS4)** : champs réellement disponibles,
    libellés des fonctions, forme du consentement. ``fonction_souhaitee`` est le libellé ou le
    code du site ; la correspondance avec ``Fonction`` est faite par ``spontanees.py``.
    Aucun champ sur le sexe, l'âge, la situation familiale ou la photo n'est prévu ni importé.
    """

    fonction_souhaitee: str = ""
    secteur_souhaite: str = ""
    lieu_souhaite: str = ""
    disponibilite: str = ""
    message: str = ""
    consentement_vivier: bool | None = None
    consentement_le: datetime | None = None


@dataclass(frozen=True)
class CandidatureDTO:
    """Candidature lue sur le site.

    ``type`` (KAN-39) : ``"concours"``, ``"offre"`` ou ``"spontanee"``, **fourni par la source**.
    Compatibilité : absent (``None``), il est déduit uniquement d'un ``poste_source_id``
    (concours) ou d'un ``offre_source_id`` (offre) ; un DTO sans type ni cible est refusé, jamais
    classé « spontanée » par défaut. ``souhaits`` : candidatures spontanées seulement.
    ``date_mise_a_jour`` : dernière mise à jour du dossier à la source, si le site la fournit
    (point de départ de la conservation, QS3).

    **Contrat à confirmer avec le code source du site (QS1 à QS3)** : table des candidatures
    spontanées, espace des identifiants (``source_id`` préfixé ``SP-`` si la table est
    distincte), un dossier mis à jour ou plusieurs dépôts.
    """

    source_id: str
    candidat_source_id: str
    date_candidature: datetime
    donnees_declarees: dict
    poste_source_id: str | None = None
    offre_source_id: str | None = None
    type: str | None = None
    souhaits: SouhaitsDTO | None = None
    date_mise_a_jour: datetime | None = None


@dataclass(frozen=True)
class PieceDTO:
    source_id: str
    candidature_source_id: str
    type: str  # cv | diplome | attestation_travail | lettre | autre
    nom_fichier: str
    storage_key: str
    content_type: str
    recue_le: datetime | None = None
