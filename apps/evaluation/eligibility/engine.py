"""Moteur d'éligibilité déterministe.

Entrées : profil du candidat (diplômes, expériences) et critères du poste.
Sortie : verdict global + verdict et raison pour chaque critère.

Principe de prudence : « non éligible » n'est rendu que lorsqu'un critère est
certainement non rempli à partir de données complètes et cohérentes. Toute donnée
manquante, illisible ou ambiguë donne « à vérifier ».
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .experience import Periode, ResultatExperience, compute_experience
from .levels import NiveauEtudes, parse_niveau
from .specialites import match_specialite

ENGINE_VERSION = "1.0.0"


class Verdict(StrEnum):
    ELIGIBLE = "eligible"
    NON_ELIGIBLE = "non_eligible"
    A_VERIFIER = "a_verifier"


@dataclass(frozen=True)
class Diplome:
    intitule: str = ""
    niveau: str | NiveauEtudes | None = None
    specialite: str = ""
    annee_obtention: int | None = None

    def niveau_resolu(self) -> NiveauEtudes | None:
        if isinstance(self.niveau, NiveauEtudes):
            return self.niveau
        return parse_niveau(self.niveau) or parse_niveau(self.intitule)


@dataclass(frozen=True)
class ProfilCandidat:
    diplomes: list[Diplome] = field(default_factory=list)
    # None = information non fournie ; [] = aucune expérience déclarée.
    experiences: list[Periode] | None = None


@dataclass(frozen=True)
class CriteresPoste:
    date_reference: date
    niveau_min: NiveauEtudes | None = None
    specialites: list[str] = field(default_factory=list)
    experience_min: Decimal | None = None
    experience_max: Decimal | None = None
    autres_criteres: str = ""


@dataclass
class ResultatCritere:
    critere: str
    libelle: str
    verdict: Verdict
    raison: str
    attendu: str = ""
    constate: str = ""


@dataclass
class ResultatEligibilite:
    verdict: Verdict
    criteres: list[ResultatCritere]
    experience: ResultatExperience | None = None
    version: str = ENGINE_VERSION

    @property
    def justification(self) -> str:
        return " ".join(f"[{c.libelle}] {c.raison}" for c in self.criteres)

    def details(self) -> list[dict]:
        return [{**asdict(c), "verdict": c.verdict.value} for c in self.criteres]


def _fmt_years(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",") + " an(s)"


def check_niveau(profil: ProfilCandidat, criteres: CriteresPoste) -> ResultatCritere:
    libelle = "Niveau d'études"
    if criteres.niveau_min is None:
        return ResultatCritere("niveau_etudes", libelle, Verdict.ELIGIBLE, "Aucun niveau exigé.")
    attendu = f"{criteres.niveau_min.label} minimum"
    if not profil.diplomes:
        return ResultatCritere(
            "niveau_etudes", libelle, Verdict.A_VERIFIER, "Aucun diplôme déclaré.", attendu
        )
    niveaux = [d.niveau_resolu() for d in profil.diplomes]
    connus = [n for n in niveaux if n is not None]
    inconnus = [
        d.intitule or str(d.niveau)
        for d, n in zip(profil.diplomes, niveaux, strict=True)
        if n is None
    ]
    meilleur = max(connus) if connus else None
    constate = meilleur.label if meilleur is not None else "non déterminé"

    if meilleur is not None and meilleur >= criteres.niveau_min:
        return ResultatCritere(
            "niveau_etudes",
            libelle,
            Verdict.ELIGIBLE,
            f"Niveau {meilleur.label} ≥ {criteres.niveau_min.label}.",
            attendu,
            constate,
        )
    if inconnus:
        return ResultatCritere(
            "niveau_etudes",
            libelle,
            Verdict.A_VERIFIER,
            "Niveau non reconnu pour : " + ", ".join(inconnus) + ".",
            attendu,
            constate,
        )
    return ResultatCritere(
        "niveau_etudes",
        libelle,
        Verdict.NON_ELIGIBLE,
        f"Niveau le plus élevé {meilleur.label} < {criteres.niveau_min.label} exigé.",
        attendu,
        constate,
    )


def check_specialite(profil: ProfilCandidat, criteres: CriteresPoste) -> ResultatCritere:
    libelle = "Spécialité"
    if not criteres.specialites:
        return ResultatCritere(
            "specialite", libelle, Verdict.ELIGIBLE, "Toute spécialité acceptée."
        )
    attendu = " ; ".join(criteres.specialites)
    # Seuls les diplômes qui atteignent le niveau exigé (ou de niveau inconnu) comptent.
    diplomes = [
        d
        for d in profil.diplomes
        if criteres.niveau_min is None
        or d.niveau_resolu() is None
        or d.niveau_resolu() >= criteres.niveau_min
    ]
    constates = [d.specialite for d in diplomes if d.specialite]
    for d in diplomes:
        accepted, how = match_specialite(d.specialite, criteres.specialites)
        if accepted:
            return ResultatCritere(
                "specialite",
                libelle,
                Verdict.ELIGIBLE,
                f"« {d.specialite} » correspond à « {accepted} » ({how}).",
                attendu,
                d.specialite,
            )
    raison = (
        "Aucune spécialité déclarée sur un diplôme du niveau exigé."
        if not constates
        else "Pas de correspondance automatique avec les spécialités acceptées : "
        "appréciation humaine requise."
    )
    return ResultatCritere(
        "specialite", libelle, Verdict.A_VERIFIER, raison, attendu, " ; ".join(constates)
    )


def check_experience(
    profil: ProfilCandidat, criteres: CriteresPoste
) -> tuple[ResultatCritere, ResultatExperience | None]:
    libelle = "Expérience"
    mini, maxi = criteres.experience_min, criteres.experience_max
    if mini is None and maxi is None:
        return ResultatCritere(
            "experience", libelle, Verdict.ELIGIBLE, "Aucune exigence d'expérience."
        ), None
    attendu_parts = []
    if mini is not None:
        attendu_parts.append(f"≥ {_fmt_years(mini)}")
    if maxi is not None:
        attendu_parts.append(f"≤ {_fmt_years(maxi)}")
    attendu = " et ".join(attendu_parts)
    if profil.experiences is None:
        return ResultatCritere(
            "experience", libelle, Verdict.A_VERIFIER, "Expérience non renseignée.", attendu
        ), None

    exp = compute_experience(profil.experiences, criteres.date_reference)
    constate = _fmt_years(exp.annees) + f" au {criteres.date_reference:%d/%m/%Y}"
    notes = (" " + " ".join(exp.avertissements)) if exp.avertissements else ""

    too_low = mini is not None and exp.annees < mini
    too_high = maxi is not None and exp.annees > maxi

    if not too_low and not too_high:
        # Une période écartée (incohérente) ne bloque pas si le total suffit : elle est
        # mentionnée dans la raison et donnera lieu à une anomalie.
        return ResultatCritere(
            "experience",
            libelle,
            Verdict.ELIGIBLE,
            f"Expérience calculée {_fmt_years(exp.annees)} conforme ({attendu}).{notes}",
            attendu,
            constate,
        ), exp

    # Une période non comptée pourrait combler un manque : prudence.
    if too_low and (exp.incertain or exp.incoherent):
        return ResultatCritere(
            "experience",
            libelle,
            Verdict.A_VERIFIER,
            f"Expérience calculée {_fmt_years(exp.annees)} insuffisante, mais certaines "
            f"périodes n'ont pas pu être comptées.{notes}",
            attendu,
            constate,
        ), exp
    if too_high and exp.incoherent:
        return ResultatCritere(
            "experience",
            libelle,
            Verdict.A_VERIFIER,
            f"Expérience calculée {_fmt_years(exp.annees)} au-delà du maximum, avec des "
            f"dates incohérentes.{notes}",
            attendu,
            constate,
        ), exp
    sens = "inférieure au minimum" if too_low else "supérieure au maximum"
    return ResultatCritere(
        "experience",
        libelle,
        Verdict.NON_ELIGIBLE,
        f"Expérience calculée {_fmt_years(exp.annees)} {sens} ({attendu}).{notes}",
        attendu,
        constate,
    ), exp


def evaluate(profil: ProfilCandidat, criteres: CriteresPoste) -> ResultatEligibilite:
    resultats = [check_niveau(profil, criteres), check_specialite(profil, criteres)]
    res_exp, exp = check_experience(profil, criteres)
    resultats.append(res_exp)
    if criteres.autres_criteres.strip():
        resultats.append(
            ResultatCritere(
                "autres",
                "Autres critères",
                Verdict.A_VERIFIER,
                "Critères non automatisés, vérification humaine requise.",
                criteres.autres_criteres.strip(),
            )
        )

    verdicts = {r.verdict for r in resultats}
    if Verdict.NON_ELIGIBLE in verdicts:
        verdict = Verdict.NON_ELIGIBLE
    elif Verdict.A_VERIFIER in verdicts:
        verdict = Verdict.A_VERIFIER
    else:
        verdict = Verdict.ELIGIBLE
    return ResultatEligibilite(verdict=verdict, criteres=resultats, experience=exp)
