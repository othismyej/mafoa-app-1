"""Lecture des extractions de pièces (spec KAN-41, règles 14, 20, 26 et 30).

API stable, sans effet de bord ni appel IA, utilisée par le moteur d'éligibilité et par les
stories KAN-42 (anomalies) et KAN-43 (adéquation CV / offre) :

- :func:`extractions_de` — extractions courantes d'une candidature, une par pièce lue ;
- :func:`profil_extrait` — profil (diplômes, périodes) construit à partir des pièces lues ;
- :func:`ecarts_declare_pieces` — écarts entre le déclaré et les pièces ;
- :func:`schema_pour` / :func:`donnees_validees` — relecture typée des données d'une extraction.

Toute l'interprétation (dates, niveaux, années) est déterministe : l'IA n'a fourni que des
chaînes extraites et validées par schéma.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Prefetch
from django.utils import timezone
from pydantic import BaseModel, ValidationError

from ai.schemas import LectureAttestation, LectureCV, LectureDiplome
from apps.evaluation.eligibility.engine import Diplome, ProfilCandidat
from apps.evaluation.eligibility.experience import Periode, compute_experience
from apps.evaluation.eligibility.levels import NiveauEtudes
from apps.evaluation.eligibility.specialites import match_specialite
from apps.recruitment.models import Candidature, Piece

from .models import Extraction

#: Types de pièces lus par l'IA (règle 1) et schéma de sortie de chacun (règle 8).
SCHEMAS: dict[str, type[BaseModel]] = {
    Piece.Type.CV: LectureCV,
    Piece.Type.DIPLOME: LectureDiplome,
    Piece.Type.ATTESTATION_TRAVAIL: LectureAttestation,
}
TYPES_LUS = tuple(SCHEMAS)
#: Pièces qui alimentent chaque critère du moteur (règle 21).
TYPES_NIVEAU = (Piece.Type.DIPLOME, Piece.Type.CV)
TYPES_EXPERIENCE = (Piece.Type.ATTESTATION_TRAVAIL, Piece.Type.CV)
#: Écart d'expérience affiché à partir de cette différence (règle 26).
ECART_EXPERIENCE_ANNEES = Decimal("0.5")


def schema_pour(piece_type: str) -> type[BaseModel]:
    return SCHEMAS[piece_type]


def donnees_validees(extraction: Extraction) -> BaseModel | None:
    """Données d'une extraction relues avec le schéma de son type ; ``None`` si illisibles."""
    schema = SCHEMAS.get(extraction.type_piece or extraction.piece.type)
    if schema is None or extraction.statut != Extraction.Statut.TERMINEE:
        return None
    try:
        return schema.model_validate(extraction.donnees)
    except ValidationError:
        return None


def est_courante(extraction: Extraction, piece: Piece) -> bool:
    """Extraction terminée dont l'empreinte correspond au contenu enregistré (règle 14)."""
    if extraction.statut != Extraction.Statut.TERMINEE:
        return False
    attendue = (piece.sha256 or "").strip().lower()
    return not attendue or extraction.empreinte == attendue


def courante(piece: Piece, extractions: list[Extraction] | None = None) -> Extraction | None:
    """Extraction courante d'une pièce (``extractions`` : déjà chargées, plus récentes d'abord)."""
    if extractions is None:
        extractions = list(
            piece.extractions.filter(statut=Extraction.Statut.TERMINEE).order_by(
                "-created_at", "-id"
            )
        )
    return next((e for e in extractions if est_courante(e, piece)), None)


def _pieces_lues(candidature: Candidature) -> list[Piece]:
    terminees = Extraction.objects.filter(statut=Extraction.Statut.TERMINEE).order_by(
        "-created_at", "-id"
    )
    return list(
        Piece.objects.filter(candidature_id=candidature.pk, type__in=TYPES_LUS)
        .order_by("type", "id")
        .prefetch_related(Prefetch("extractions", queryset=terminees, to_attr="_terminees"))
    )


def extractions_de(candidature: Candidature) -> list[Extraction]:
    """Extractions courantes de la candidature, une par pièce lue, ordre (type, pièce)."""
    result = []
    for piece in _pieces_lues(candidature):
        e = courante(piece, piece._terminees)
        if e is not None:
            e.piece = piece
            result.append(e)
    return result


def to_date(value) -> date | None:
    """Date ISO ``AAAA-MM-JJ`` ; toute autre forme est traitée comme manquante."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _periode(debut, fin, en_cours: bool, employeur: str, poste: str) -> Periode:
    d_fin = to_date(fin)
    return Periode(
        debut=to_date(debut),
        fin=d_fin,
        employeur=employeur or "",
        intitule=poste or "",
        # Ni date de fin ni « en cours » : la période ne peut pas être comptée (KAN-41).
        fin_inconnue=d_fin is None and not en_cours,
    )


@dataclass
class ProfilExtrait:
    """Profil construit à partir des pièces lues (règle 20).

    ``pieces_niveau`` / ``pieces_experience`` : pièces qui alimentent les critères de niveau et
    spécialité, et d'expérience ; vides si aucune pièce lue ne les alimente (critère évalué sur
    le seul déclaré). ``confiance_*`` : confiance minimale des pièces utilisées.
    """

    extractions: list[Extraction] = field(default_factory=list)
    diplomes: list[Diplome] = field(default_factory=list)
    periodes: list[Periode] = field(default_factory=list)
    pieces_niveau: list[int] = field(default_factory=list)
    pieces_experience: list[int] = field(default_factory=list)
    confiance_niveau: float | None = None
    confiance_experience: float | None = None
    noms_titulaires: dict[int, str] = field(default_factory=dict)

    @property
    def vide(self) -> bool:
        return not (self.pieces_niveau or self.pieces_experience)

    @property
    def profil(self) -> ProfilCandidat:
        """Profil pour le moteur ; ``experiences=None`` si aucune pièce n'en fournit."""
        return ProfilCandidat(
            diplomes=list(self.diplomes),
            experiences=list(self.periodes) if self.pieces_experience else None,
        )


def _min(a: float | None, b: float | None) -> float | None:
    values = [v for v in (a, b) if v is not None]
    return min(values) if values else None


def profil_depuis_extractions(extractions: list[Extraction]) -> ProfilExtrait:
    out = ProfilExtrait(extractions=list(extractions))
    for e in extractions:
        data = donnees_validees(e)
        if data is None:
            continue
        piece_type = e.type_piece or e.piece.type
        if getattr(data, "nom_titulaire", None):
            out.noms_titulaires[e.piece_id] = data.nom_titulaire
        if piece_type in TYPES_NIVEAU:
            out.pieces_niveau.append(e.piece_id)
            out.confiance_niveau = _min(out.confiance_niveau, e.confiance)
        if piece_type in TYPES_EXPERIENCE:
            out.pieces_experience.append(e.piece_id)
            out.confiance_experience = _min(out.confiance_experience, e.confiance)
        if isinstance(data, LectureDiplome):
            if data.intitule or data.niveau:
                out.diplomes.append(
                    Diplome(
                        intitule=data.intitule or "",
                        niveau=data.niveau,
                        specialite=data.specialite or "",
                        annee_obtention=data.annee_obtention,
                    )
                )
        elif isinstance(data, LectureCV):
            out.diplomes.extend(
                Diplome(
                    intitule=f.intitule or "",
                    niveau=f.niveau,
                    specialite=f.specialite or "",
                    annee_obtention=f.annee_obtention,
                )
                for f in data.formations
            )
            out.periodes.extend(
                _periode(x.debut, x.fin, x.en_cours, x.employeur, x.poste) for x in data.experiences
            )
        elif isinstance(data, LectureAttestation):
            out.periodes.extend(
                _periode(x.debut, x.fin, x.en_cours, data.employeur, x.poste) for x in data.periodes
            )
    return out


def profil_extrait(candidature: Candidature) -> ProfilExtrait:
    """Profil extrait des pièces courantes de la candidature (aucun appel IA)."""
    return profil_depuis_extractions(extractions_de(candidature))


# --------------------------------------------------------------------------------------
# Écarts déclaré ↔ pièces (règle 26)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ecart:
    critere: str  # "niveau_etudes" | "specialite" | "experience"
    libelle: str
    declare: str
    pieces: str
    pieces_ids: tuple[int, ...]


def _meilleur_niveau(diplomes: list[Diplome]) -> NiveauEtudes | None:
    niveaux = [n for n in (d.niveau_resolu() for d in diplomes) if n is not None]
    return max(niveaux) if niveaux else None


def _fmt_annees(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",") + " an(s)"


def ecarts(declare: ProfilCandidat, extrait: ProfilExtrait, date_reference: date) -> list[Ecart]:
    out: list[Ecart] = []
    if extrait.pieces_niveau:
        ids = tuple(extrait.pieces_niveau)
        n_decl, n_pieces = _meilleur_niveau(declare.diplomes), _meilleur_niveau(extrait.diplomes)
        if n_decl is not None and n_pieces is not None and n_decl != n_pieces:
            out.append(Ecart("niveau_etudes", "Niveau d'études", n_decl.label, n_pieces.label, ids))
        spec_decl = [d.specialite for d in declare.diplomes if d.specialite]
        spec_pieces = [d.specialite for d in extrait.diplomes if d.specialite]
        if (
            spec_decl
            and spec_pieces
            and not any(match_specialite(p, spec_decl)[0] for p in spec_pieces)
        ):
            out.append(
                Ecart(
                    "specialite", "Spécialité", " ; ".join(spec_decl), " ; ".join(spec_pieces), ids
                )
            )
    if extrait.pieces_experience and declare.experiences is not None:
        a_decl = compute_experience(declare.experiences, date_reference).annees
        a_pieces = compute_experience(extrait.periodes, date_reference).annees
        if abs(a_decl - a_pieces) >= ECART_EXPERIENCE_ANNEES:
            out.append(
                Ecart(
                    "experience",
                    "Expérience",
                    _fmt_annees(a_decl),
                    _fmt_annees(a_pieces),
                    tuple(extrait.pieces_experience),
                )
            )
    return out


def ecarts_declare_pieces(
    candidature: Candidature, extrait: ProfilExtrait | None = None
) -> list[Ecart]:
    """Écarts entre les données déclarées et les pièces lues (informatif, aucune anomalie)."""
    from apps.evaluation.services import profil_from_declared

    extrait = extrait if extrait is not None else profil_extrait(candidature)
    if extrait.vide:
        return []
    cible = candidature.cible
    reference = (getattr(cible, "date_reference", None) if cible else None) or timezone.localdate()
    return ecarts(profil_from_declared(candidature.donnees_declarees or {}), extrait, reference)
