"""Rapprochement déterministe des spécialités.

Une correspondance est reconnue si, après normalisation (casse, accents, ponctuation) :
1. les libellés sont identiques ;
2. ils appartiennent au même groupe d'équivalences ci-dessous ;
3. tous les mots significatifs de la spécialité attendue figurent dans celle du candidat
   (ex. « Génie civil » ⊂ « Génie civil et BTP »).

Sans correspondance, le moteur renvoie « à vérifier » et jamais « non éligible » :
le rapprochement de spécialités reste une appréciation humaine.
"""

from __future__ import annotations

from .levels import normalize_text

# Groupes d'équivalences. À enrichir avec les équipes Mafoa : toute modification doit être testée.
EQUIVALENCES: list[set[str]] = [
    {
        "informatique",
        "genie informatique",
        "computer science",
        "systemes d information",
        "ingenierie logicielle",
        "developpement informatique",
    },
    {"genie civil", "btp", "batiment et travaux publics", "travaux publics"},
    {"genie electrique", "electrotechnique", "electricite", "genie electrique et energetique"},
    {"hydraulique", "genie de l eau", "eau et assainissement", "eau et environnement"},
    {"finance", "finance comptabilite", "comptabilite", "audit et controle de gestion"},
    {"gestion", "management", "gestion des entreprises", "sciences de gestion"},
    {"ressources humaines", "grh", "gestion des ressources humaines"},
    {"droit", "sciences juridiques", "droit public", "droit des affaires"},
    {"genie mecanique", "mecanique", "genie industriel et mecanique"},
]

STOPWORDS = {"de", "des", "du", "la", "le", "les", "l", "d", "et", "en", "option", "specialite"}

_INDEX: dict[str, int] = {
    normalize_text(label): i for i, group in enumerate(EQUIVALENCES) for label in group
}


def _tokens(text: str) -> set[str]:
    return {t for t in text.split() if t not in STOPWORDS}


def match_specialite(candidat: str, acceptees: list[str]) -> tuple[str | None, str]:
    """Renvoie (spécialité acceptée correspondante ou None, explication)."""
    cand = normalize_text(candidat)
    if not cand:
        return None, "spécialité non renseignée"
    cand_tokens = _tokens(cand)
    for accepted in acceptees:
        acc = normalize_text(accepted)
        if not acc:
            continue
        if cand == acc:
            return accepted, "correspondance exacte"
        if cand in _INDEX and _INDEX.get(acc) == _INDEX[cand]:
            return accepted, "équivalence reconnue"
        acc_tokens = _tokens(acc)
        if acc_tokens and acc_tokens <= cand_tokens:
            return accepted, "spécialité attendue incluse dans l'intitulé"
    return None, "aucune correspondance automatique"
