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
class CandidatureDTO:
    source_id: str
    candidat_source_id: str
    date_candidature: datetime
    donnees_declarees: dict
    poste_source_id: str | None = None
    offre_source_id: str | None = None


@dataclass(frozen=True)
class PieceDTO:
    source_id: str
    candidature_source_id: str
    type: str  # cv | diplome | attestation_travail | lettre | autre
    nom_fichier: str
    storage_key: str
    content_type: str
    recue_le: datetime | None = None
