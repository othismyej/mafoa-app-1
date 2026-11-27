"""Schémas Pydantic des sorties IA. Toute réponse est validée avant usage."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


# --------------------------------------------------------------------------------------
# Lecture des pièces par type (KAN-41, règle 8)
# --------------------------------------------------------------------------------------
# ``extra="forbid"`` : un champ inattendu (ex. « sexe », « date_naissance ») fait rejeter
# toute la réponse (règles 9 et 10). Les dates restent des chaînes ISO : leur interprétation
# est faite par le code déterministe (``apps.documents.services``).

_DATE = "Date ISO AAAA-MM-JJ, ou null si absente ou illisible."
_EXTRAIT = "Passage exact du document qui justifie ces informations."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FormationLue(_Strict):
    intitule: str = Field(description="Intitulé du diplôme tel qu'écrit.")
    niveau: str | None = Field(None, description="Niveau tel qu'écrit (ex. « Bac+5 », « Master »).")
    specialite: str | None = None
    etablissement: str | None = None
    annee_obtention: int | None = None
    extrait: str = Field("", description=_EXTRAIT)


class ExperienceLue(_Strict):
    employeur: str | None = None
    poste: str | None = Field(None, description="Intitulé du poste occupé.")
    debut: str | None = Field(None, description=_DATE)
    fin: str | None = Field(None, description=_DATE + " Null si le poste est en cours.")
    en_cours: bool = False
    extrait: str = Field("", description=_EXTRAIT)


class LectureCV(_Strict):
    nom_titulaire: str | None = Field(None, description="Nom du titulaire tel qu'écrit (jetons).")
    formations: list[FormationLue] = Field(default_factory=list)
    experiences: list[ExperienceLue] = Field(default_factory=list)
    competences: list[str] = Field(default_factory=list)
    confiance: float = Field(ge=0, le=1, description="Confiance globale dans l'extraction.")
    remarques: list[str] = Field(default_factory=list)


class LectureDiplome(_Strict):
    nom_titulaire: str | None = None
    intitule: str | None = None
    niveau: str | None = None
    specialite: str | None = None
    etablissement: str | None = None
    date_obtention: str | None = Field(None, description=_DATE)
    annee_obtention: int | None = None
    extrait: str = Field("", description=_EXTRAIT)
    confiance: float = Field(ge=0, le=1)
    remarques: list[str] = Field(default_factory=list)


class PeriodeAttestee(_Strict):
    poste: str | None = None
    debut: str | None = Field(None, description=_DATE)
    fin: str | None = Field(None, description=_DATE + " Null si l'emploi est en cours.")
    en_cours: bool = False
    extrait: str = Field("", description=_EXTRAIT)


class LectureAttestation(_Strict):
    nom_titulaire: str | None = None
    employeur: str | None = None
    periodes: list[PeriodeAttestee] = Field(default_factory=list)
    date_attestation: str | None = Field(None, description=_DATE)
    confiance: float = Field(ge=0, le=1)
    remarques: list[str] = Field(default_factory=list)


class SegmentDossier(_Strict):
    """Partie d'un dossier combiné : un document (pages consécutives) et ses données.

    Seul le bloc correspondant à ``type_document`` est renseigné (contrôlé par le code)."""

    type_document: Literal["cv", "diplome", "attestation_travail", "autre"]
    page_debut: int = Field(ge=1, description="Première page du document (1 = première page).")
    page_fin: int = Field(ge=1, description="Dernière page du document.")
    cv: LectureCV | None = None
    diplome: LectureDiplome | None = None
    attestation: LectureAttestation | None = None
    description: str = Field(
        "",
        description="Pour « autre » : nature du document (ex. relevé de notes), sans donnée personnelle.",
    )


class LectureDossier(_Strict):
    """Dossier combiné (champ « Documents » du site) : pages classées par document."""

    segments: list[SegmentDossier] = Field(default_factory=list)
    confiance: float = Field(ge=0, le=1)
    remarques: list[str] = Field(default_factory=list)
