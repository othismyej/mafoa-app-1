"""Calcul déterministe des années d'expérience.

Règles :
- une période sans date de début est ignorée et rend le calcul « incertain » ;
- une période sans date de fin (poste en cours) s'arrête à la date de référence ;
- une période est tronquée à la date de référence ; une période qui commence après est ignorée ;
- une période dont la fin précède le début est une incohérence : ignorée et signalée ;
- les périodes qui se chevauchent ou se touchent sont fusionnées (aucun double comptage) ;
- jours comptés de façon inclusive ; années = jours / 365,25, arrondies au centième.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

DAYS_PER_YEAR = Decimal("365.25")


@dataclass(frozen=True)
class Periode:
    debut: date | None
    fin: date | None = None
    employeur: str = ""
    intitule: str = ""


@dataclass
class ResultatExperience:
    annees: Decimal
    jours: int
    periodes_retenues: list[tuple[date, date]] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    incertain: bool = False
    incoherent: bool = False


def _label(p: Periode) -> str:
    parts = [x for x in (p.intitule, p.employeur) if x]
    return " chez ".join(parts) if parts else "période sans intitulé"


def compute_experience(periodes: list[Periode], date_reference: date) -> ResultatExperience:
    avertissements: list[str] = []
    incertain = False
    incoherent = False
    intervals: list[tuple[date, date]] = []

    for p in periodes:
        if p.debut is None:
            incertain = True
            avertissements.append(f"Date de début manquante : {_label(p)} (non comptée).")
            continue
        fin = p.fin or date_reference
        if p.fin is not None and p.fin < p.debut:
            incoherent = True
            avertissements.append(
                f"Date de fin antérieure au début : {_label(p)} "
                f"({p.debut:%d/%m/%Y} → {p.fin:%d/%m/%Y}), non comptée."
            )
            continue
        if p.debut > date_reference:
            avertissements.append(
                f"Période postérieure à la date de référence : {_label(p)} (non comptée)."
            )
            continue
        if fin > date_reference:
            fin = date_reference
        intervals.append((p.debut, fin))

    merged: list[tuple[date, date]] = []
    for debut, fin in sorted(intervals):
        if merged and debut <= merged[-1][1] + timedelta(days=1):
            if fin > merged[-1][1]:
                merged[-1] = (merged[-1][0], fin)
        else:
            merged.append((debut, fin))

    jours = sum((fin - debut).days + 1 for debut, fin in merged)
    annees = (Decimal(jours) / DAYS_PER_YEAR).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ResultatExperience(
        annees=annees,
        jours=jours,
        periodes_retenues=merged,
        avertissements=avertissements,
        incertain=incertain,
        incoherent=incoherent,
    )
