"""Combinaison prudente du verdict déclaré et du verdict sur pièces (spec KAN-41, règles 21-24).

Le moteur déterministe est exécuté deux fois (profil déclaré, profil extrait des pièces).
Pour chaque critère alimenté par des pièces lues :

- déclaré et pièces concordent, avec une confiance suffisante → verdict conservé ;
- sinon (écart de verdict ou confiance faible) → « à vérifier ».

Conséquence : un critère n'est jamais « non éligible » sur la seule base des pièces lues par
l'IA ; il ne l'est que si le déclaré l'est aussi.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import ResultatCritere, ResultatEligibilite, Verdict

COMBINAISON_VERSION = "1.1.0"

LIBELLES_VERDICT = {
    Verdict.ELIGIBLE: "Éligible",
    Verdict.NON_ELIGIBLE: "Non éligible",
    Verdict.A_VERIFIER: "À vérifier",
}


@dataclass(frozen=True)
class Alimentation:
    """Pièces lues qui alimentent un critère et leur confiance minimale."""

    pieces: tuple[int, ...] = ()
    confiance: float | None = None


@dataclass
class ResultatCombine:
    verdict: Verdict
    criteres: list[ResultatCritere]
    details: list[dict] = field(default_factory=list)
    version: str = COMBINAISON_VERSION

    @property
    def justification(self) -> str:
        return " ".join(f"[{c.libelle}] {c.raison}" for c in self.criteres)


def _pieces_txt(ids: tuple[int, ...]) -> str:
    return ", ".join(f"n° {i}" for i in ids)


def _fmt_conf(value: float | None) -> str:
    return "inconnue" if value is None else f"{value:.2f}".replace(".", ",")


def combiner(
    declare: ResultatEligibilite,
    pieces: ResultatEligibilite,
    alimentation: dict[str, Alimentation],
    confiance_min: float,
) -> ResultatCombine:
    par_critere = {c.critere: c for c in pieces.criteres}
    criteres: list[ResultatCritere] = []
    details: list[dict] = []
    for d in declare.criteres:
        alim = alimentation.get(d.critere)
        p = par_critere.get(d.critere)
        base = {
            "critere": d.critere,
            "libelle": d.libelle,
            "attendu": d.attendu,
            "verdict_declare": d.verdict.value,
        }
        if not alim or not alim.pieces or p is None:
            criteres.append(d)
            details.append(
                {
                    **base,
                    "verdict": d.verdict.value,
                    "raison": d.raison,
                    "constate": d.constate,
                    "source": "declare",
                }
            )
            continue
        ids = alim.pieces
        confiant = alim.confiance is not None and alim.confiance >= confiance_min
        if p.verdict == d.verdict and confiant:
            verdict = d.verdict
            raison = f"{d.raison} Confirmé par les pièces {_pieces_txt(ids)}."
            constate = d.constate
        elif p.verdict != d.verdict:
            verdict = Verdict.A_VERIFIER
            raison = (
                f"Écart entre le déclaré ({LIBELLES_VERDICT[d.verdict]}) et les pièces "
                f"{_pieces_txt(ids)} ({LIBELLES_VERDICT[p.verdict]}) : vérification humaine "
                f"requise. Déclaré : {d.raison} Pièces : {p.raison}"
            )
            constate = f"Déclaré : {d.constate or '—'} ; pièces : {p.constate or '—'}"
        else:
            verdict = Verdict.A_VERIFIER
            raison = (
                f"Confiance faible de la lecture des pièces {_pieces_txt(ids)} "
                f"({_fmt_conf(alim.confiance)}) : vérification humaine requise. "
                f"Déclaré : {d.raison}"
            )
            constate = d.constate
        criteres.append(ResultatCritere(d.critere, d.libelle, verdict, raison, d.attendu, constate))
        details.append(
            {
                **base,
                "verdict": verdict.value,
                "raison": raison,
                "constate": constate,
                "source": "declare_et_pieces",
                "verdict_pieces": p.verdict.value,
                "pieces": list(ids),
                "confiance": alim.confiance,
            }
        )
    verdicts = {c.verdict for c in criteres}
    if Verdict.NON_ELIGIBLE in verdicts:
        verdict = Verdict.NON_ELIGIBLE
    elif Verdict.A_VERIFIER in verdicts:
        verdict = Verdict.A_VERIFIER
    else:
        verdict = Verdict.ELIGIBLE
    return ResultatCombine(verdict=verdict, criteres=criteres, details=details)
