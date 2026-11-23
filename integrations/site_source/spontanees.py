"""Candidatures spontanées (KAN-39) : correspondance entre le contrat ``SiteSource`` et le modèle.

Tout ce qui dépend de la structure réelle du site pour les candidatures spontanées est isolé
ici et dans ``dto.py`` (questions QS1 à QS7 de la spécification, **à confirmer avec le code
source du site**) : résolution du type, contrôle de cohérence d'un DTO, correspondance des
fonctions souhaitées, valeurs des souhaits.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.evaluation.eligibility.levels import normalize_text
from apps.recruitment.models import Candidature, DetailsSpontanee, Fonction

from .dto import CandidatureDTO, SouhaitsDTO

TYPES = tuple(Candidature.Type.values)  # concours, offre, spontanee
CONCOURS = Candidature.Type.CONCOURS
OFFRE = Candidature.Type.OFFRE
SPONTANEE = Candidature.Type.SPONTANEE

# Motifs de refus d'un DTO (règle 12), journalisés avec le seul ``source_id``.
REFUS_TYPE_INCONNU = "type_inconnu"
REFUS_SANS_TYPE_NI_CIBLE = "sans_type_ni_cible"
REFUS_SPONTANEE_AVEC_CIBLE = "spontanee_avec_cible"
REFUS_CONCOURS_SANS_POSTE = "concours_sans_poste"
REFUS_OFFRE_SANS_OFFRE = "offre_sans_offre"
REFUS_CIBLES_MULTIPLES = "cibles_multiples"


@dataclass(frozen=True)
class TypeResolu:
    type: str | None
    refus: str = ""


def resoudre_type(dto: CandidatureDTO) -> TypeResolu:
    """Type explicite du DTO, ou déduit d'une cible (compatibilité), jamais « spontanée » par
    défaut (règles 3 et 11). Un DTO incohérent est refusé (règle 12)."""
    poste, offre = bool(dto.poste_source_id), bool(dto.offre_source_id)
    type_ = dto.type
    if type_ is None or type_ == "":
        if poste and offre:
            return TypeResolu(None, REFUS_CIBLES_MULTIPLES)
        if poste:
            return TypeResolu(CONCOURS)
        if offre:
            return TypeResolu(OFFRE)
        return TypeResolu(None, REFUS_SANS_TYPE_NI_CIBLE)
    if type_ not in TYPES:
        return TypeResolu(None, REFUS_TYPE_INCONNU)
    if type_ == SPONTANEE and (poste or offre):
        return TypeResolu(None, REFUS_SPONTANEE_AVEC_CIBLE)
    if type_ == CONCOURS and not poste:
        return TypeResolu(None, REFUS_CONCOURS_SANS_POSTE)
    if type_ == OFFRE and not offre:
        return TypeResolu(None, REFUS_OFFRE_SANS_OFFRE)
    if poste and offre:
        return TypeResolu(None, REFUS_CIBLES_MULTIPLES)
    return TypeResolu(str(type_))


def _cle(texte: str) -> str:
    return normalize_text(texte or "").replace(" ", "").replace("/", "").replace("_", "")


#: Code ou libellé du site (normalisé) → valeur de ``Fonction``. À compléter avec les libellés
#: réels du site (QS2).
_FONCTIONS = {}
for _valeur, _libelle in Fonction.choices:
    _FONCTIONS[_cle(_valeur)] = _valeur
    _FONCTIONS[_cle(str(_libelle))] = _valeur


def fonction_depuis_site(libelle: str) -> tuple[str, str]:
    """``(valeur de Fonction, libellé brut)`` : valeur vide et libellé conservé si inconnue
    (règle 15)."""
    libelle = (libelle or "").strip()
    if not libelle:
        return "", ""
    valeur = _FONCTIONS.get(_cle(libelle))
    if valeur:
        return valeur, ""
    return "", libelle[:150]


def valeurs_souhaits(souhaits: SouhaitsDTO | None, date_mise_a_jour=None) -> dict:
    """Champs de ``DetailsSpontanee`` à partir du DTO ; absent = tous vides (règle 15)."""
    s = souhaits or SouhaitsDTO()
    fonction, brute = fonction_depuis_site(s.fonction_souhaitee)
    message = s.message or ""
    tronque = len(message) > DetailsSpontanee.MESSAGE_MAX
    return {
        "fonction_souhaitee": fonction,
        "fonction_souhaitee_brute": brute,
        "secteur_souhaite": (s.secteur_souhaite or "").strip()[:150],
        "lieu_souhaite": (s.lieu_souhaite or "").strip()[:100],
        "disponibilite": (s.disponibilite or "").strip()[:100],
        "message": message[: DetailsSpontanee.MESSAGE_MAX],
        "message_tronque": tronque,
        "consentement_vivier": s.consentement_vivier,
        "consentement_le": s.consentement_le,
        "mise_a_jour_source_le": date_mise_a_jour,
    }
