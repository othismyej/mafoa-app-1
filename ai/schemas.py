"""Schémas Pydantic des sorties IA. Toute réponse est validée avant usage."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DiplomeExtrait(BaseModel):
    intitule: str
    niveau: str | None = Field(None, description="Ex. « Bac+5 », tel qu'écrit ou déduit.")
    specialite: str | None = None
    etablissement: str | None = None
    annee_obtention: int | None = None


class ExperienceExtraite(BaseModel):
    employeur: str | None = None
    poste: str | None = None
    debut: str | None = Field(None, description="Date ISO AAAA-MM-JJ si connue.")
    fin: str | None = Field(None, description="Date ISO, ou null si poste en cours.")
    extrait: str = Field("", description="Passage du document justifiant ces dates.")


class ExtractionCV(BaseModel):
    """Données structurées extraites d'un CV, d'un diplôme ou d'une attestation.

    Les dates et intitulés sont extraits par l'IA ; les calculs (années d'expérience,
    comparaison des niveaux) restent faits par le code déterministe.
    """

    nom_complet: str | None = None
    diplomes: list[DiplomeExtrait] = Field(default_factory=list)
    experiences: list[ExperienceExtraite] = Field(default_factory=list)
    competences: list[str] = Field(default_factory=list)
    confiance: float = Field(ge=0, le=1)
    remarques: list[str] = Field(default_factory=list)
