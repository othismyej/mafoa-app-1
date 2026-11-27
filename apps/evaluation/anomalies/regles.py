"""Contrôles déterministes d'un dossier (spec KAN-42, sections D et E). Aucune requête SQL.

Chaque contrôle renvoie des :class:`Constat` : type, code, gravité, description en français,
détails structurés et clé d'idempotence. **Minimisation (règle 4)** : ni description, ni
détails, ni clé ne contiennent de nom, prénom, email, téléphone, CIN ou date de naissance ;
seuls des dates, années, niveaux, spécialités du dossier lui-même et des identifiants internes
y figurent. **Critères interdits (règle 5)** : aucun contrôle sur le sexe, la région, la ville,
la photo, la situation familiale ou l'âge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.recruitment.models import Candidature, Piece
from apps.recruitment.normalization import normalize_name

from ..eligibility.experience import Periode, compute_experience
from ..eligibility.levels import NIVEAU_LABELS, NiveauEtudes, normalize_text, parse_niveau
from ..eligibility.specialites import match_specialite
from ..services import _parse_date
from .extractions import ChampExtrait, PieceExtraite

HAUTE, MOYENNE, INFO = "haute", "moyenne", "info"

#: Familles de clés (préfixes) gérées par le détecteur.
FAMILLES_DOUBLONS = ("plusieurs_postes_concours:", "doublon_candidat:", "doublon_piece:")
FAMILLES_TOUTES = (
    *FAMILLES_DOUBLONS,
    "piece_manquante:",
    "incoherence_dates:",
    "incoherence_nom:",
    "ecart_declaration:",
)
#: Préfixes toujours exécutés lors d'un examen complet (hors extractions et expérience
#: antérieure au diplôme, dont l'exécution dépend des données, règle 21).
PREFIXES_TOUJOURS = (
    *FAMILLES_DOUBLONS,
    "piece_manquante:",
    "incoherence_dates:fin_avant_debut:",
    "incoherence_dates:debut_apres_candidature:",
    "incoherence_dates:diplome_apres_candidature:",
    "incoherence_dates:date_illisible:",
    "incoherence_dates:chevauchement:",
)
CLE_EXPERIENCE_AVANT_DIPLOME = "incoherence_dates:experience_avant_diplome"

TYPES_PIECE = dict(Piece.Type.choices)
CRITERE_LIBELLES = {
    "email": "email",
    "telephone": "téléphone",
    "identite": "identité",
    "cin": "CIN",
}


@dataclass
class Constat:
    cle: str
    type: str
    code: str
    gravite: str
    description: str
    details: dict = field(default_factory=dict)
    piece_id: int | None = None
    candidature_liee_id: int | None = None


@dataclass
class Couverture:
    """Clés dont la cause a été **évaluée** pour une candidature (règle 21).

    Une anomalie existante non constatée n'est marquée « cause disparue » que si sa clé est
    couverte : contrôle exécuté, ou pièce visée disparue de l'application.
    """

    familles: tuple[str, ...]
    prefixes: set[str] = field(default_factory=set)

    def couvre(self, cle: str, piece_id: int | None) -> bool:
        if not cle.startswith(self.familles):
            return False
        if ":piece=" in cle and piece_id is None:
            return True  # pièce supprimée (SET_NULL) : cause non constatée
        return cle.startswith(tuple(self.prefixes))


def fr_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def fr_nombre(d: Decimal) -> str:
    texte = f"{d.normalize():f}" if d == d.to_integral() else f"{d:f}"
    return texte.replace(".", ",")


def libelle_piece(type_piece: str) -> str:
    return TYPES_PIECE.get(type_piece, TYPES_PIECE["autre"])


def libelle_niveau(niveau: NiveauEtudes | None) -> str:
    return NIVEAU_LABELS[niveau] if niveau is not None else "non reconnu"


def empreinte_courte(data: dict, champs: tuple[str, ...]) -> str:
    """Empreinte stable d'une entrée déclarée (clé non nominative, règle 17)."""
    brut = json.dumps({k: data.get(k) for k in champs}, sort_keys=True, default=str)
    return hashlib.sha256(brut.encode()).hexdigest()[:12]


def date_candidature(c: Candidature) -> date:
    return timezone.localdate(c.date_candidature)


def _annee(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    texte = str(value or "").strip()
    return int(texte) if texte.isdigit() and len(texte) == 4 else None


def _vide(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


# --------------------------------------------------------------------------------------
# Données déclarées, lues sans jamais lever d'erreur (cas limites : données mal formées)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperienceDeclaree:
    numero: int
    brut: dict
    debut: date | None
    fin: date | None
    empreinte: str


@dataclass(frozen=True)
class DiplomeDeclare:
    numero: int
    brut: dict
    niveau: NiveauEtudes | None
    annee: int | None
    specialite: str
    empreinte: str


def experiences_declarees(donnees) -> list[ExperienceDeclaree]:
    raw = donnees.get("experiences") if isinstance(donnees, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        ExperienceDeclaree(
            numero=i,
            brut=e,
            debut=_parse_date(e.get("debut")),
            fin=_parse_date(e.get("fin")),
            empreinte=empreinte_courte(e, ("debut", "fin", "employeur", "poste")),
        )
        for i, e in enumerate(raw, 1)
        if isinstance(e, dict)
    ]


def diplomes_declares(donnees) -> list[DiplomeDeclare]:
    raw = donnees.get("formation") if isinstance(donnees, dict) else None
    if not isinstance(raw, list):
        return []
    result = []
    for i, f in enumerate(raw, 1):
        if not isinstance(f, dict):
            continue
        niveau = parse_niveau(str(f.get("niveau") or "")) or parse_niveau(
            str(f.get("intitule") or "")
        )
        result.append(
            DiplomeDeclare(
                numero=i,
                brut=f,
                niveau=niveau,
                annee=_annee(f.get("annee_obtention")),
                specialite=str(f.get("specialite") or ""),
                empreinte=empreinte_courte(
                    f, ("intitule", "niveau", "specialite", "etablissement", "annee_obtention")
                ),
            )
        )
    return result


def _periodes(experiences: list[ExperienceDeclaree]) -> list[Periode]:
    return [Periode(debut=e.debut, fin=e.fin) for e in experiences]


# --------------------------------------------------------------------------------------
# E. Pièces obligatoires (règles 14 à 16)
# --------------------------------------------------------------------------------------


def pieces_obligatoires(c: Candidature) -> tuple[list[str], str]:
    """Résolution poste → concours → offre → paramètre par défaut ; renvoie (liste, origine)."""
    poste = c.poste_concours
    if poste is not None:
        if poste.pieces_obligatoires is not None:
            return list(poste.pieces_obligatoires), "poste"
        if poste.concours.pieces_obligatoires is not None:
            return list(poste.concours.pieces_obligatoires), "concours"
    elif c.offre is not None and c.offre.pieces_obligatoires is not None:
        return list(c.offre.pieces_obligatoires), "offre"
    return list(settings.PIECES_OBLIGATOIRES_DEFAUT.get(c.type, [])), "defaut"


def controle_pieces_obligatoires(c: Candidature, pieces: list[Piece]) -> list[Constat]:
    exigees, origine = pieces_obligatoires(c)
    constats = []
    for type_piece in exigees:
        du_type = [p for p in pieces if p.type == type_piece]
        if any(p.taille is None or p.taille > 0 for p in du_type):
            continue
        vide = du_type[0] if du_type else None
        libelle = libelle_piece(type_piece)
        constats.append(
            Constat(
                cle=f"piece_manquante:{type_piece}",
                type="piece_manquante",
                code=type_piece,
                gravite=HAUTE,
                description=(
                    f"Pièce obligatoire reçue vide : {libelle}."
                    if vide
                    else f"Pièce obligatoire manquante : {libelle}."
                ),
                details={
                    "source": "pieces",
                    "type_piece": type_piece,
                    "configuration": origine,
                    "recue_vide": vide is not None,
                },
                piece_id=vide.pk if vide else None,
            )
        )
    return constats


# --------------------------------------------------------------------------------------
# D. Dates déclarées (règle 11)
# --------------------------------------------------------------------------------------


def controle_dates(c: Candidature) -> list[Constat]:
    donnees = c.donnees_declarees or {}
    le = date_candidature(c)
    constats: list[Constat] = []
    experiences = experiences_declarees(donnees)
    for e in experiences:
        base = {"source": "donnees_declarees", "experience": e.numero}
        debut_brut, fin_brut = e.brut.get("debut"), e.brut.get("fin")
        problemes = []
        if _vide(debut_brut):
            problemes.append(("debut", "absente"))
        elif e.debut is None:
            problemes.append(("debut", "illisible"))
        if not _vide(fin_brut) and e.fin is None:
            problemes.append(("fin", "illisible"))
        if problemes:
            texte = " ; ".join(
                f"date de {'début' if champ == 'debut' else 'fin'} {etat}"
                for champ, etat in problemes
            )
            constats.append(
                Constat(
                    cle=f"incoherence_dates:date_illisible:exp={e.empreinte}",
                    type="incoherence_dates",
                    code="date_illisible",
                    gravite=INFO,
                    description=f"Expérience déclarée n° {e.numero} : {texte}.",
                    details={**base, "champs": [{"champ": ch, "etat": et} for ch, et in problemes]},
                )
            )
        if e.debut and e.fin and e.fin < e.debut:
            constats.append(
                Constat(
                    cle=f"incoherence_dates:fin_avant_debut:exp={e.empreinte}",
                    type="incoherence_dates",
                    code="fin_avant_debut",
                    gravite=MOYENNE,
                    description=(
                        f"Expérience déclarée n° {e.numero} dont la fin précède le début : "
                        f"{fr_date(e.debut)} → {fr_date(e.fin)}."
                    ),
                    details={**base, "debut": e.debut.isoformat(), "fin": e.fin.isoformat()},
                )
            )
        if e.debut and e.debut > le:
            constats.append(
                Constat(
                    cle=f"incoherence_dates:debut_apres_candidature:exp={e.empreinte}",
                    type="incoherence_dates",
                    code="debut_apres_candidature",
                    gravite=MOYENNE,
                    description=(
                        f"Expérience déclarée n° {e.numero} commençant le {fr_date(e.debut)}, "
                        f"après la date de candidature ({fr_date(le)})."
                    ),
                    details={
                        **base,
                        "debut": e.debut.isoformat(),
                        "date_candidature": le.isoformat(),
                    },
                )
            )

    # Chevauchements : dates lisibles et cohérentes, fin vide = date de candidature.
    seuil = settings.CHEVAUCHEMENT_SEUIL_JOURS
    intervalles = [
        (e, e.debut, e.fin or le)
        for e in experiences
        if e.debut is not None
        and (e.fin is not None or _vide(e.brut.get("fin")))
        and (e.fin is None or e.fin >= e.debut)
    ]
    for i, (e1, d1, f1) in enumerate(intervalles):
        for e2, d2, f2 in intervalles[i + 1 :]:
            jours = (min(f1, f2) - max(d1, d2)).days + 1
            if jours < seuil or e1.empreinte == e2.empreinte:
                continue
            a, b = sorted((e1.empreinte, e2.empreinte))
            constats.append(
                Constat(
                    cle=f"incoherence_dates:chevauchement:exp={a}+{b}",
                    type="incoherence_dates",
                    code="chevauchement",
                    gravite=INFO,
                    description=(
                        f"Les expériences déclarées n° {e1.numero} et n° {e2.numero} se "
                        f"chevauchent sur {jours} jours (seuil : {seuil} jours)."
                    ),
                    details={
                        "source": "donnees_declarees",
                        "experiences": [e1.numero, e2.numero],
                        "jours": jours,
                        "seuil_jours": seuil,
                    },
                )
            )

    for d in diplomes_declares(donnees):
        if d.annee is not None and d.annee > le.year:
            constats.append(
                Constat(
                    cle=f"incoherence_dates:diplome_apres_candidature:form={d.empreinte}",
                    type="incoherence_dates",
                    code="diplome_apres_candidature",
                    gravite=MOYENNE,
                    description=(
                        f"Diplôme déclaré n° {d.numero} obtenu en {d.annee}, après l'année de "
                        f"candidature ({le.year})."
                    ),
                    details={
                        "source": "donnees_declarees",
                        "formation": d.numero,
                        "annee_obtention": d.annee,
                        "annee_candidature": le.year,
                    },
                )
            )
    return constats


# --------------------------------------------------------------------------------------
# D. Expérience antérieure au diplôme (règle 12)
# --------------------------------------------------------------------------------------


def _minimum(cible) -> NiveauEtudes | None:
    return NiveauEtudes(cible.niveau_etudes_min) if cible.niveau_etudes_min is not None else None


def controle_experience_avant_diplome(c: Candidature) -> tuple[list[Constat], bool]:
    """Renvoie (constats, exécuté). Non exécuté (règle 21) : date de référence absente ou
    aucun diplôme de référence avec une année lisible."""
    cible = c.cible
    if c.type == Candidature.Type.SPONTANEE or cible is None:
        return [], True
    minimum = cible.experience_min_annees
    if minimum is None:
        return [], True  # contrôle sans objet : une ancienne anomalie n'est plus fondée
    date_ref = cible.date_reference
    if date_ref is None:
        return [], False
    donnees = c.donnees_declarees or {}
    niveau_min = _minimum(cible)
    candidats = [d for d in diplomes_declares(donnees) if d.annee and d.niveau is not None]
    if niveau_min is not None:
        candidats = [d for d in candidats if d.niveau >= niveau_min]
    if not candidats:
        return [], False
    ref = sorted(candidats, key=lambda d: (-d.niveau, d.annee))[0]
    date_diplome = date(ref.annee, 1, 1)
    experiences = experiences_declarees(donnees)
    periodes = _periodes(experiences)
    total = compute_experience(periodes, date_ref).annees
    apres = []
    for p in periodes:
        if p.debut is None or (p.fin is not None and p.fin < p.debut):
            apres.append(p)  # ignorée de la même façon par le calcul
        elif p.fin is not None and p.fin < date_diplome:
            continue
        else:
            apres.append(Periode(debut=max(p.debut, date_diplome), fin=p.fin))
    total_apres = compute_experience(apres, date_ref).annees
    minimum = Decimal(minimum)
    if not (total >= minimum > total_apres):
        return [], True
    return [
        Constat(
            cle=CLE_EXPERIENCE_AVANT_DIPLOME,
            type="incoherence_dates",
            code="experience_avant_diplome",
            gravite=MOYENNE,
            description=(
                f"Sans l'expérience antérieure au diplôme de référence ({libelle_niveau(ref.niveau)}"
                f", obtenu en {ref.annee}, retenu au {fr_date(date_diplome)}), l'expérience "
                f"déclarée passe de {fr_nombre(total)} à {fr_nombre(total_apres)} an(s), sous le "
                f"minimum exigé de {fr_nombre(minimum)} an(s) (date de référence : "
                f"{fr_date(date_ref)}). Le verdict d'éligibilité n'est pas modifié."
            ),
            details={
                "source": "donnees_declarees",
                "total_declare": str(total),
                "total_apres_diplome": str(total_apres),
                "minimum": str(minimum),
                "date_reference": date_ref.isoformat(),
                "annee_diplome": ref.annee,
                "niveau_diplome": libelle_niveau(ref.niveau),
                "date_diplome_retenue": date_diplome.isoformat(),
            },
        )
    ], True


# --------------------------------------------------------------------------------------
# D. Pièce ↔ candidat et déclaré ↔ extrait (règle 13)
# --------------------------------------------------------------------------------------


def _fiable(champ: ChampExtrait | None) -> bool:
    if champ is None or _vide(champ.valeur):
        return False
    try:
        return float(champ.confiance) >= settings.EXTRACTION_CONFIANCE_MIN
    except (TypeError, ValueError):
        return False


def _source_extraction(px: PieceExtraite, champ: ChampExtrait, *, valeur=True) -> dict:
    details = {
        "source": "extraction",
        "extraction_id": px.extraction_id,
        "modele": px.modele,
        "prompt_version": px.prompt_version,
        "extrait_le": px.extrait_le.isoformat() if px.extrait_le else None,
        "confiance": float(champ.confiance),
    }
    if valeur:
        # Jamais pour le nom lu (minimisation, règle 13) : ni la valeur, ni l'extrait cité.
        details["extrait"] = champ.extrait
    return details


def _diplome_compare(
    lu: NiveauEtudes | None, declares: list[DiplomeDeclare]
) -> DiplomeDeclare | None:
    avec_niveau = [d for d in declares if d.niveau is not None]
    if lu is not None:
        meme = [d for d in avec_niveau if d.niveau == lu]
        if meme:
            return meme[0]
    if avec_niveau:
        return max(avec_niveau, key=lambda d: d.niveau)
    return declares[0] if declares else None


def controle_extractions(
    c: Candidature, pieces: list[Piece], extractions: list[PieceExtraite]
) -> tuple[list[Constat], set[str]]:
    """Renvoie (constats, préfixes de clés évalués)."""
    constats: list[Constat] = []
    couverts: set[str] = set()
    par_id = {p.pk: p for p in pieces}
    donnees = c.donnees_declarees or {}
    declares = diplomes_declares(donnees)
    cible = c.cible
    niveau_min = _minimum(cible) if cible is not None else None
    for px in extractions:
        piece = par_id.get(px.piece_id)
        if piece is None:
            continue  # pièce absente de l'application : rien à comparer
        p_libelle = libelle_piece(piece.type)

        # Titulaire ↔ candidat.
        if _fiable(px.titulaire):
            cle = f"incoherence_nom:titulaire_different:piece={piece.pk}"
            couverts.add(cle)
            lus = set(normalize_name(str(px.titulaire.valeur), "").split())
            attendus = set((c.candidat.nom_normalise or "").split())
            if lus and attendus and not (lus <= attendus or attendus <= lus):
                communs = len(lus & attendus)
                gravite = (
                    HAUTE
                    if not communs and piece.type in ("diplome", "attestation_travail")
                    else MOYENNE
                )
                constats.append(
                    Constat(
                        cle=cle,
                        type="incoherence_nom",
                        code="titulaire_different",
                        gravite=gravite,
                        description=(
                            f"Le nom du titulaire lu sur la pièce ({p_libelle}) ne correspond "
                            "pas au nom du candidat ("
                            + ("aucun mot commun" if not communs else "mots en partie communs")
                            + ")."
                        ),
                        details={
                            **_source_extraction(px, px.titulaire, valeur=False),
                            "mots_communs": communs,
                        },
                        piece_id=piece.pk,
                    )
                )

        # Diplôme ↔ formation déclarée.
        dip = px.diplome
        if piece.type == "diplome" and dip is not None and declares:
            lu = parse_niveau(str(dip.niveau.valeur)) if _fiable(dip.niveau) else None
            ref = _diplome_compare(lu, declares)
            if lu is not None and ref is not None and ref.niveau is not None:
                cle = f"ecart_declaration:niveau:piece={piece.pk}"
                couverts.add(cle)
                if lu < ref.niveau:
                    haute = niveau_min is not None and lu < niveau_min <= ref.niveau
                    texte = (
                        f"Niveau lu sur le diplôme ({libelle_niveau(lu)}) inférieur au niveau "
                        f"déclaré ({libelle_niveau(ref.niveau)})."
                    )
                    if haute:
                        texte += (
                            f" Le niveau lu est inférieur au minimum exigé "
                            f"({libelle_niveau(niveau_min)})."
                        )
                    constats.append(
                        Constat(
                            cle=cle,
                            type="ecart_declaration",
                            code="niveau",
                            gravite=HAUTE if haute else MOYENNE,
                            description=texte,
                            details={
                                **_source_extraction(px, dip.niveau),
                                "valeur_lue": libelle_niveau(lu),
                                "valeur_declaree": libelle_niveau(ref.niveau),
                                "minimum": libelle_niveau(niveau_min) if niveau_min else None,
                            },
                            piece_id=piece.pk,
                        )
                    )
            if ref is not None and _fiable(dip.specialite) and ref.specialite.strip():
                cle = f"ecart_declaration:specialite:piece={piece.pk}"
                couverts.add(cle)
                lue = str(dip.specialite.valeur)
                trouve, _raison = match_specialite(lue, [ref.specialite])
                if trouve is None:
                    constats.append(
                        Constat(
                            cle=cle,
                            type="ecart_declaration",
                            code="specialite",
                            gravite=MOYENNE,
                            description=(
                                f"Spécialité lue sur le diplôme (« {lue} ») sans correspondance "
                                f"avec la spécialité déclarée (« {ref.specialite} »)."
                            ),
                            details={
                                **_source_extraction(px, dip.specialite),
                                "valeur_lue": lue,
                                "valeur_declaree": ref.specialite,
                            },
                            piece_id=piece.pk,
                        )
                    )
            annee_lue = _annee(dip.annee_obtention.valeur) if _fiable(dip.annee_obtention) else None
            if ref is not None and annee_lue is not None and ref.annee is not None:
                cle = f"ecart_declaration:annee_obtention:piece={piece.pk}"
                couverts.add(cle)
                if annee_lue != ref.annee:
                    constats.append(
                        Constat(
                            cle=cle,
                            type="ecart_declaration",
                            code="annee_obtention",
                            gravite=INFO,
                            description=(
                                f"Année d'obtention lue sur le diplôme ({annee_lue}) différente "
                                f"de l'année déclarée ({ref.annee})."
                            ),
                            details={
                                **_source_extraction(px, dip.annee_obtention),
                                "valeur_lue": annee_lue,
                                "valeur_declaree": ref.annee,
                            },
                            piece_id=piece.pk,
                        )
                    )

        # Attestation ↔ expériences déclarées.
        periodes = [p for p in px.periodes if _fiable(p) and isinstance(p.valeur, dict)]
        if piece.type == "attestation_travail" and periodes:
            couverts.add(f"ecart_declaration:periode:piece={piece.pk}:")
            constats.extend(_controle_periodes(c, piece, px, periodes))
    return constats, couverts


def _meme_employeur(a: str, b: str) -> bool:
    a, b = normalize_text(a), normalize_text(b)
    return bool(a and b and (a == b or a in b or b in a))


def _controle_periodes(c, piece, px, periodes) -> list[Constat]:
    le = date_candidature(c)
    experiences = experiences_declarees(c.donnees_declarees or {})
    cible = c.cible
    minimum = getattr(cible, "experience_min_annees", None) if cible is not None else None
    date_ref = cible.date_reference if cible is not None else None
    constats = []
    for e in experiences:
        if e.debut is None:
            continue
        champ = next(
            (
                p
                for p in periodes
                if _meme_employeur(
                    str(e.brut.get("employeur") or ""), str(p.valeur.get("employeur") or "")
                )
            ),
            None,
        )
        if champ is None:
            continue  # pas d'attestation rapprochée : relève des pièces obligatoires
        a_debut = _parse_date(champ.valeur.get("debut"))
        a_fin = _parse_date(champ.valeur.get("fin"))
        if a_debut is None:
            continue
        ecart_debut = abs((a_debut - e.debut).days)
        ecart_fin = abs(((a_fin or le) - (e.fin or le)).days)
        if ecart_debut <= 31 and ecart_fin <= 31:
            continue
        haute = False
        if minimum is not None and date_ref is not None:
            declarees = _periodes(experiences)
            attestees = [
                Periode(debut=a_debut, fin=a_fin)
                if x.numero == e.numero
                else Periode(x.debut, x.fin)
                for x in experiences
            ]
            minimum_d = Decimal(minimum)
            haute = (
                compute_experience(declarees, date_ref).annees
                >= minimum_d
                > compute_experience(attestees, date_ref).annees
            )

        def periode(d, f):
            return f"{fr_date(d)} → {fr_date(f) if f else 'en cours'}"

        texte = (
            f"Expérience déclarée n° {e.numero} ({periode(e.debut, e.fin)}) : l'attestation "
            f"indique {periode(a_debut, a_fin)} (écart de plus de 31 jours)."
        )
        if haute:
            texte += (
                f" Avec les dates attestées, le minimum exigé de {fr_nombre(Decimal(minimum))} "
                "an(s) n'est plus atteint."
            )
        constats.append(
            Constat(
                cle=f"ecart_declaration:periode:piece={piece.pk}:exp={e.empreinte}",
                type="ecart_declaration",
                code="periode",
                gravite=HAUTE if haute else MOYENNE,
                description=texte,
                details={
                    **_source_extraction(px, champ),
                    "experience": e.numero,
                    "declare": {
                        "debut": e.debut.isoformat(),
                        "fin": e.fin.isoformat() if e.fin else None,
                    },
                    "atteste": {
                        "debut": a_debut.isoformat(),
                        "fin": a_fin.isoformat() if a_fin else None,
                    },
                    "ecart_debut_jours": ecart_debut,
                    "ecart_fin_jours": ecart_fin,
                },
                piece_id=piece.pk,
            )
        )
    return constats
