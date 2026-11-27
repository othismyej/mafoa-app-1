"""Mise en forme de la section « Lecture des pièces (IA) » de la fiche (spec KAN-41, D)."""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from ai.client import ia_configuree
from ai.schemas import LectureAttestation, LectureCV, LectureDiplome
from apps.recruitment.presenters import DateAffichee, format_date

from .lecture import EtatLecture, etats_lecture
from .services import donnees_validees, ecarts_declare_pieces, profil_depuis_extractions

ETATS = {
    "non_lue": _("Non lue"),
    "en_cours": _("Lecture en cours"),
    "lue": _("Lue"),
    "obsolete": _("Obsolète (pièce modifiée depuis la lecture)"),
    "echec": _("Échec"),
    "ignoree": _("Ignorée"),
}
CONFIANCE_ELEVEE = 0.85
NIVEAUX_CONFIANCE = {
    "elevee": _("Confiance élevée"),
    "moyenne": _("Confiance moyenne"),
    "faible": _("Confiance faible"),
}


def niveau_confiance(value: float | None) -> str:
    if value is None:
        return "faible"
    if value >= CONFIANCE_ELEVEE:
        return "elevee"
    return "moyenne" if value >= settings.AI_CONFIANCE_MIN else "faible"


def _fin(fin, en_cours: bool) -> DateAffichee:
    if en_cours and not fin:
        return DateAffichee(str(_("en cours")), "en_cours")
    return format_date(fin)


def _formation(f) -> dict:
    return {
        "intitule": f.intitule or "",
        "niveau": f.niveau or "",
        "specialite": f.specialite or "",
        "etablissement": f.etablissement or "",
        "annee": f.annee_obtention or "",
        "extrait": f.extrait,
    }


def _periode(poste, employeur, debut, fin, en_cours, extrait) -> dict:
    return {
        "poste": poste or "",
        "employeur": employeur or "",
        "debut": format_date(debut),
        "fin": _fin(fin, en_cours),
        "extrait": extrait,
    }


def donnees_affichees(extraction) -> dict:
    """Formations, expériences et remarques d'une extraction, robustes aux données mal formées."""
    data = donnees_validees(extraction)
    if data is None:
        return {"lisibles": False}
    out = {"lisibles": True, "formations": [], "experiences": [], "remarques": data.remarques}
    if isinstance(data, LectureCV):
        out["formations"] = [_formation(f) for f in data.formations]
        out["experiences"] = [
            _periode(x.poste, x.employeur, x.debut, x.fin, x.en_cours, x.extrait)
            for x in data.experiences
        ]
        out["competences"] = data.competences
    elif isinstance(data, LectureDiplome):
        out["formations"] = [
            {
                "intitule": data.intitule or "",
                "niveau": data.niveau or "",
                "specialite": data.specialite or "",
                "etablissement": data.etablissement or "",
                "annee": data.annee_obtention or (data.date_obtention or "")[:4],
                "extrait": data.extrait,
            }
        ]
    elif isinstance(data, LectureAttestation):
        out["experiences"] = [
            _periode(x.poste, data.employeur, x.debut, x.fin, x.en_cours, x.extrait)
            for x in data.periodes
        ]
    return out


def _ligne(e: EtatLecture, avec_donnees: bool) -> dict:
    courante = e.courante
    ligne = {
        "piece": e.piece,
        "etat": e.etat,
        "etat_label": ETATS[e.etat],
        "courante": courante,
        "derniere": e.derniere,
        "motif": e.motif,
    }
    if courante is not None:
        niveau = niveau_confiance(courante.confiance)
        ligne["confiance_niveau"] = niveau
        ligne["confiance_label"] = NIVEAUX_CONFIANCE[niveau]
        if avec_donnees:
            ligne["donnees"] = donnees_affichees(courante)
    return ligne


def lecture_context(candidature, pieces, *, avec_donnees: bool = True) -> dict:
    """Contexte de la section. ``avec_donnees=False`` : statuts seulement (fragment d'état,
    sans donnée personnelle, règle 28)."""
    etats = etats_lecture(pieces)
    lignes = [_ligne(e, avec_donnees) for e in etats]
    ecarts = []
    if avec_donnees:
        courantes = [e.courante for e in etats if e.courante is not None]
        if courantes:
            ecarts = ecarts_declare_pieces(candidature, profil_depuis_extractions(courantes))
    return {
        "lecture_lignes": lignes,
        "lecture_en_cours": any(e.etat == "en_cours" for e in etats),
        "lecture_a_lire": any(e.etat in ("non_lue", "echec", "obsolete") for e in etats),
        "lecture_lues": any(e.courante is not None for e in etats),
        "lecture_ecarts": ecarts,
        "ia_configuree": ia_configuree(),
    }
