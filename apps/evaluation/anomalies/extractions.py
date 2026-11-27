"""Lecture des données extraites des pièces par l'IA (KAN-41) — point de branchement unique.

Spec KAN-42, règle 13 : le détecteur d'anomalies compare des données **déjà extraites** ; il
n'appelle jamais l'IA. Tant que KAN-41 n'est pas fusionnée, l'interface renvoie une liste vide :
les contrôles « pièce ↔ candidat » et « déclaré ↔ extrait » ne produisent rien et sont comptés
« non exécutés » au rapport (règle 23, CA30).

Pour brancher KAN-41 : implémenter :func:`extractions_par_candidature` (dernière extraction de
chaque pièce, en lot : une ou deux requêtes quel que soit le nombre de candidatures), passer
:data:`KAN41_DISPONIBLE` à ``True`` et appeler :func:`signaler_extraction_terminee` à la fin de
chaque extraction validée. Le reste du détecteur ne dépend que des types ci-dessous.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: ``True`` une fois l'API de lecture de KAN-41 branchée ci-dessous.
KAN41_DISPONIBLE = False


@dataclass(frozen=True)
class ChampExtrait:
    """Valeur lue sur une pièce, avec la confiance du modèle (0 à 1) et l'extrait cité."""

    valeur: Any
    confiance: float
    extrait: str = ""


@dataclass(frozen=True)
class DiplomeExtrait:
    intitule: ChampExtrait | None = None
    niveau: ChampExtrait | None = None
    specialite: ChampExtrait | None = None
    annee_obtention: ChampExtrait | None = None


@dataclass(frozen=True)
class PieceExtraite:
    """Dernière extraction d'une pièce.

    ``periodes`` (attestations de travail) : un ``ChampExtrait`` par période, dont la valeur est
    un dictionnaire ``{"employeur", "poste", "debut", "fin"}`` (dates ISO, ``fin`` vide = en
    cours) ; la confiance et l'extrait portent sur la période entière.
    """

    piece_id: int
    type_piece: str
    extraction_id: int
    modele: str = ""
    prompt_version: str = ""
    extrait_le: datetime | None = None
    titulaire: ChampExtrait | None = None
    diplome: DiplomeExtrait | None = None
    periodes: tuple[ChampExtrait, ...] = field(default_factory=tuple)


def extractions_par_candidature(candidature_ids: Iterable[int]) -> dict[int, list[PieceExtraite]]:
    """Dernière extraction de chaque pièce, par candidature (absente = aucune extraction).

    KAN-41 non fusionnée : aucune donnée extraite.
    """
    return {}


def extractions_candidature(candidature) -> list[PieceExtraite]:
    """Interface de la spec (règle 13) pour une candidature."""
    return extractions_par_candidature([candidature.pk]).get(candidature.pk, [])


def signaler_extraction_terminee(piece) -> None:
    """Déclencheur « après extraction » (règle 22) : à appeler par KAN-41 à la fin d'une
    extraction validée. La détection de la candidature de la pièce est mise en file à la
    validation de la transaction (contrôles des extractions et pièces en double)."""
    from .declencheurs import planifier_detection

    planifier_detection([piece.candidature_id], declencheur="extraction")
