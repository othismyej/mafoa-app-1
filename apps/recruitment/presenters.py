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
    from .models import Piece

    labels = []
    for s in sources:
        key = s.get("type") if isinstance(s, dict) else s
        if key == "extraction" and isinstance(s, dict) and s.get("piece_id"):
            # Pièce lue par l'IA (KAN-41, règle 29) : « Pièce n° 12 (Diplôme) ».
            type_label = dict(Piece.Type.choices).get(str(s.get("piece_type", "")), "")
            label = _("Pièce n° %(n)s") % {"n": s["piece_id"]}
            labels.append(f"{label} ({type_label})" if type_label else label)
            continue
        labels.append(SOURCE_LABELS.get(str(key), str(key)))
    return labels


CRITERE_KEYS = ("critere", "libelle", "verdict", "attendu", "constate", "raison")


def criteres(details) -> list[dict]:
    """Lignes du tableau des critères, robustes à des ``details`` mal formés."""
    if not isinstance(details, list):
        return []
    return [{k: _text(d, k) for k in CRITERE_KEYS} for d in details if isinstance(d, dict)]


# Statut de la candidature ramené à un verdict d'éligibilité (bandeau d'écart, règle 10).
STATUT_VERS_ELIGIBILITE = {
    "eligible": "eligible",
    "preselectionnee": "eligible",
    "convoquee": "eligible",
    "retenue": "eligible",
    "non_eligible": "non_eligible",
}


def ecart_verdict_decision(
    statut: str,
    verdict: str | None,
    a_une_decision: bool,
    derniere_decision: str | None = None,
) -> bool:
    """Le moteur contredit-il la décision humaine en vigueur ?

    On compare le verdict à l'éligibilité impliquée par le statut courant et par la dernière
    décision humaine (règle 10 de KAN-11).
    """
    if not (a_une_decision and verdict):
        return False
    attendus = {
        STATUT_VERS_ELIGIBILITE.get(statut),
        STATUT_VERS_ELIGIBILITE.get(derniere_decision or ""),
    } - {None}
    return any(verdict != a for a in attendus)
