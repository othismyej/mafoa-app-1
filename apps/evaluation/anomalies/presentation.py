"""Présentation des anomalies sur la fiche candidature (spec KAN-42, règle 30)."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from ..models import Anomalie
from .regles import TYPES_PIECE
from .resolution import issues_permises

CODE_LIBELLES = {
    ("plusieurs_postes_concours", "autre_compte"): gettext_noop(
        "Autre compte candidat dans le même concours"
    ),
    ("doublon_candidat", "meme_offre"): gettext_noop("Autre compte candidat à la même offre"),
    ("doublon_candidat", "meme_cin"): gettext_noop("Autre compte candidat de même CIN"),
    ("doublon_candidat", "autre_compte"): gettext_noop("Autre compte candidat rapproché"),
    ("doublon_candidat", "telephone_seul"): gettext_noop(
        "Autre compte candidat rapproché par le téléphone seul"
    ),
    ("doublon_candidat", "valeur_partagee"): gettext_noop(
        "Valeur de rapprochement partagée par de nombreux comptes"
    ),
    ("doublon_piece", "autre_candidat"): gettext_noop(
        "Fichier identique à celui d’un autre compte candidat"
    ),
    ("doublon_piece", "meme_dossier"): gettext_noop("Fichier présent deux fois dans le dossier"),
    ("doublon_piece", "valeur_partagee"): gettext_noop(
        "Fichier partagé par de nombreux comptes candidats"
    ),
    ("incoherence_dates", "fin_avant_debut"): gettext_noop("Fin d’expérience avant le début"),
    ("incoherence_dates", "debut_apres_candidature"): gettext_noop(
        "Expérience commençant après la candidature"
    ),
    ("incoherence_dates", "diplome_apres_candidature"): gettext_noop(
        "Diplôme obtenu après la candidature"
    ),
    ("incoherence_dates", "date_illisible"): gettext_noop("Date d’expérience absente ou illisible"),
    ("incoherence_dates", "chevauchement"): gettext_noop("Expériences qui se chevauchent"),
    ("incoherence_dates", "experience_avant_diplome"): gettext_noop(
        "Expérience antérieure au diplôme"
    ),
    ("incoherence_nom", "titulaire_different"): gettext_noop(
        "Titulaire de la pièce différent du candidat"
    ),
    ("ecart_declaration", "niveau"): gettext_noop("Niveau lu inférieur au niveau déclaré"),
    ("ecart_declaration", "specialite"): gettext_noop(
        "Spécialité lue différente de la déclaration"
    ),
    ("ecart_declaration", "annee_obtention"): gettext_noop(
        "Année d’obtention lue différente de la déclaration"
    ),
    ("ecart_declaration", "periode"): gettext_noop("Période attestée différente de la déclaration"),
}
PIECE_MANQUANTE = gettext_noop("Pièce obligatoire manquante — %(piece)s")
CRITERES = {
    "email": gettext_noop("email"),
    "telephone": gettext_noop("téléphone"),
    "identite": gettext_noop("identité"),
    "cin": gettext_noop("CIN"),
}
#: Types dont la candidature liée est affichée (NOM Prénom, cible, lien).
TYPES_LIES = ("doublon_candidat", "plusieurs_postes_concours", "doublon_piece")


@dataclass
class AnomalieVue:
    a: Anomalie
    libelle_code: str = ""
    sources: list[str] = field(default_factory=list)
    extrait: str = ""
    lien: bool = False
    etat_symetrique: str = ""  # "resolue" | "non_resolue" | ""
    issues: list[tuple[str, str]] = field(default_factory=list)


def libelle_code(a: Anomalie) -> str:
    if not a.code:
        return ""
    if a.type == "piece_manquante":
        return _(PIECE_MANQUANTE) % {"piece": _(TYPES_PIECE.get(a.code, a.code))}
    libelle = CODE_LIBELLES.get((a.type, a.code))
    return _(libelle) if libelle else ""


def _sources(a: Anomalie) -> tuple[list[str], str]:
    details = a.details or {}
    source = details.get("source")
    sources, extrait = [], ""
    if source == "donnees_declarees":
        sources.append(_("Données déclarées"))
    elif source == "compte_rapproche" and details.get("criteres"):
        sources.append(
            _("Compte rapproché par : %(c)s")
            % {"c": ", ".join(_(CRITERES.get(c, c)) for c in details["criteres"])}
        )
    elif source == "extraction":
        le = parse_datetime(details.get("extrait_le") or "")
        sources.append(
            _("Extraction du %(d)s, modèle %(m)s, prompt %(p)s, confiance %(c)s")
            % {
                "d": le.strftime("%d/%m/%Y") if le else "—",
                "m": details.get("modele") or "—",
                "p": details.get("prompt_version") or "—",
                "c": f"{details.get('confiance', 0):.2f}".replace(".", ","),
            }
        )
        extrait = details.get("extrait") or ""
    if a.piece_id and a.piece is not None:
        sources.append(
            _("Pièce : %(t)s (%(f)s)") % {"t": a.piece.get_type_display(), "f": a.piece.nom_fichier}
        )
    return sources, extrait


def presenter(candidature, anomalies: list[Anomalie]) -> list[AnomalieVue]:
    """Vues des anomalies, avec l'état de l'anomalie correspondante sur la candidature liée
    (règle 27) : une requête au plus, sans autre détail sur la candidature liée."""
    liees = {a.candidature_liee_id for a in anomalies if a.candidature_liee_id}
    etats: dict[tuple[int, str], bool] = {}
    if liees:
        for cid, type_, resolue in Anomalie.objects.filter(
            candidature_id__in=liees, candidature_liee_id=candidature.pk
        ).values_list("candidature_id", "type", "resolue"):
            # Non résolue si au moins une anomalie correspondante l'est.
            etats[(cid, type_)] = etats.get((cid, type_), True) and resolue
    vues = []
    for a in anomalies:
        sources, extrait = _sources(a)
        etat = ""
        if a.candidature_liee_id and (a.candidature_liee_id, a.type) in etats:
            etat = "resolue" if etats[(a.candidature_liee_id, a.type)] else "non_resolue"
        vues.append(
            AnomalieVue(
                a=a,
                libelle_code=libelle_code(a),
                sources=sources,
                extrait=extrait,
                lien=a.type in TYPES_LIES and a.code not in ("valeur_partagee", "meme_dossier"),
                etat_symetrique=etat,
                issues=[(i, _(Anomalie.Issue(i).label)) for i in issues_permises(a)],
            )
        )
    return vues
