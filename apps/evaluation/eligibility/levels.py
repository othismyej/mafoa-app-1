"""Niveaux d'études : énumération ordonnée et normalisation des libellés.

Code pur, sans dépendance Django, partagé par les modèles et le moteur d'éligibilité.
"""

from __future__ import annotations

import re
import unicodedata
from enum import IntEnum


class NiveauEtudes(IntEnum):
    """Valeur = nombre d'années après le baccalauréat (comparaison directe)."""

    BAC = 0
    BAC_2 = 2
    BAC_3 = 3
    BAC_4 = 4
    BAC_5 = 5
    DOCTORAT = 8

    @property
    def label(self) -> str:
        return NIVEAU_LABELS[self]

    @classmethod
    def choices(cls) -> list[tuple[int, str]]:
        return [(n.value, n.label) for n in cls]


NIVEAU_LABELS = {
    NiveauEtudes.BAC: "Bac",
    NiveauEtudes.BAC_2: "Bac+2",
    NiveauEtudes.BAC_3: "Bac+3",
    NiveauEtudes.BAC_4: "Bac+4",
    NiveauEtudes.BAC_5: "Bac+5",
    NiveauEtudes.DOCTORAT: "Doctorat (Bac+8)",
}


def normalize_text(value: str) -> str:
    """Minuscules, sans accents, ponctuation réduite à des espaces."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z0-9+]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


# Motifs testés dans l'ordre : le premier qui correspond l'emporte.
# Les libellés ambigus (ex. « licence » seule = Bac+3) sont explicites ici et testés.
_BAC_PLUS = re.compile(r"\bbac ?\+ ?(\d)\b")
_PATTERNS: list[tuple[re.Pattern[str], NiveauEtudes]] = [
    (re.compile(r"\b(doctorat|phd|docteur|these)\b"), NiveauEtudes.DOCTORAT),
    (
        re.compile(
            r"\b(master|mastere|msc|mba|ingenieur|ingenieur d etat|diplome d ingenieur"
            r"|dess|dea|ecole de commerce|grande ecole|architecte|pharmacie)\b"
        ),
        NiveauEtudes.BAC_5,
    ),
    (re.compile(r"\b(maitrise|m1)\b"), NiveauEtudes.BAC_4),
    (re.compile(r"\b(licence|bachelor|lp|licence professionnelle)\b"), NiveauEtudes.BAC_3),
    (re.compile(r"\b(dut|bts|deug|deust|technicien specialise|dts|ts)\b"), NiveauEtudes.BAC_2),
    (re.compile(r"\b(baccalaureat|bac)\b"), NiveauEtudes.BAC),
]


def parse_niveau(label: str | None) -> NiveauEtudes | None:
    """Convertit un libellé libre (« Bac+5 », « Master », « Ingénieur d'État »…) en niveau.

    Renvoie ``None`` si le libellé n'est pas reconnu : l'appelant doit alors
    traiter le cas comme « à vérifier », jamais comme non éligible.
    """
    text = normalize_text(label or "")
    if not text:
        return None
    m = _BAC_PLUS.search(text)
    if m:
        years = int(m.group(1))
        if years >= 8:
            return NiveauEtudes.DOCTORAT
        eligible = [n for n in NiveauEtudes if n.value <= years]
        return max(eligible)
    for pattern, niveau in _PATTERNS:
        if pattern.search(text):
            return niveau
    return None
