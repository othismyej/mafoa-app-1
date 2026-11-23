"""Mise en forme des données d'une fiche candidature (spec KAN-11).

Les données déclarées viennent du site et peuvent être incomplètes ou mal formées : rien ici ne
doit faire échouer l'affichage. Aucun calcul métier (l'expérience retenue est celle du moteur).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.utils.translation import gettext as _

SOURCE_LABELS = {"donnees_declarees": "Données déclarées", "extraction": "Pièces extraites"}


@dataclass
class DateAffichee:
    texte: str
    probleme: str = ""  # "", "manquante" ou "illisible"


def format_date(value) -> DateAffichee:
    if value in (None, ""):
        return DateAffichee(_("Date manquante"), "manquante")
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        return DateAffichee(str(value), "illisible")
    return DateAffichee(d.strftime("%d/%m/%Y"))


def _text(entry: dict, key: str) -> str:
    value = entry.get(key)
    return str(value) if value not in (None, "") else ""


def formations(donnees: dict) -> list[dict]:
    raw = donnees.get("formation") if isinstance(donnees, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        {
            "intitule": _text(f, "intitule"),
            "niveau": _text(f, "niveau"),
            "specialite": _text(f, "specialite"),
            "etablissement": _text(f, "etablissement"),
            "annee_obtention": _text(f, "annee_obtention"),
        }
        for f in raw
        if isinstance(f, dict)
    ]


def experiences(donnees: dict) -> list[dict] | None:
    """``None`` si l'information n'a pas été fournie, ``[]`` si aucune expérience déclarée."""
    if not isinstance(donnees, dict) or "experiences" not in donnees:
        return None
    raw = donnees.get("experiences")
    if not isinstance(raw, list):
        return []
    result = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        debut = format_date(e.get("debut"))
        fin_raw = e.get("fin")
        if fin_raw in (None, "") and not debut.probleme:
            fin = DateAffichee(_("en cours"), "en_cours")
        else:
            fin = format_date(fin_raw)
        result.append(
            {
                "employeur": _text(e, "employeur"),
                "poste": _text(e, "poste"),
                "debut": debut,
                "fin": fin,
            }
        )
    return result


def source_labels(sources) -> list[str]:
    if not isinstance(sources, list):
        return []
    labels = []
    for s in sources:
        key = s.get("type") if isinstance(s, dict) else s
        labels.append(SOURCE_LABELS.get(str(key), str(key)))
    return labels
