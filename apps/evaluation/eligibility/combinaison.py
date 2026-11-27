"""Combinaison prudente du verdict déclaré et du verdict sur pièces (spec KAN-41, règles 21-24).

Le moteur déterministe est exécuté deux fois (profil déclaré, profil extrait des pièces).
Pour chaque critère alimenté par des pièces lues :

- déclaré et pièces concordent, avec une confiance suffisante → verdict conservé ;
- sinon (écart de verdict ou confiance faible) → « à vérifier » ;
- le déclaré ne dit rien sur le critère (le site ne stocke aucune expérience datée) : les pièces
  sont la seule source ; « éligible » sur pièces avec une confiance suffisante est retenu,
  tout autre résultat donne « à vérifier » (règle 22 bis).

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
    #: Références lisibles des documents (« n° 12 », « n° 14 p. 3-4 ») ; à défaut, ``pieces``.
    references: tuple[str, ...] = ()
    #: Le déclaré ne renseigne pas ce critère : les pièces sont la seule source.
    declare_absent: bool = False

    def texte(self) -> str:
        refs = self.references or tuple(f"n° {i}" for i in self.pieces)
        return ", ".join(dict.fromkeys(refs))


@dataclass
class ResultatCombine:
    verdict: Verdict
    criteres: list[ResultatCritere]
    details: list[dict] = field(default_factory=list)
    version: str = COMBINAISON_VERSION

    @property
    def justification(self) -> str:
        return " ".join(f"[{c.libelle}] {c.raison}" for c in self.criteres)


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
        refs = alim.texte()
        confiant = alim.confiance is not None and alim.confiance >= confiance_min
        source = "declare_et_pieces"
        if alim.declare_absent:
            source = "pieces"
            constate = p.constate
            if p.verdict == Verdict.ELIGIBLE and confiant:
                verdict = Verdict.ELIGIBLE
                raison = f"Établi sur les pièces {refs} (aucune donnée déclarée) : {p.raison}"
            elif not confiant:
                verdict = Verdict.A_VERIFIER
                raison = (
                    f"Aucune donnée déclarée ; confiance faible de la lecture des pièces {refs} "
                    f"({_fmt_conf(alim.confiance)}) : vérification humaine requise. "
                    f"Pièces : {p.raison}"
                )
            else:
                verdict = Verdict.A_VERIFIER
                raison = (
                    f"Aucune donnée déclarée ; les pièces {refs} indiquent "
                    f"« {LIBELLES_VERDICT[p.verdict]} », ce qui n'est jamais conclu sur la seule "
                    f"lecture par l'IA : vérification humaine requise. Pièces : {p.raison}"
                )
        elif p.verdict == d.verdict and confiant:
            verdict = d.verdict
            raison = f"{d.raison} Confirmé par les pièces {refs}."
            constate = d.constate
        elif p.verdict != d.verdict:
            verdict = Verdict.A_VERIFIER
            raison = (
                f"Écart entre le déclaré ({LIBELLES_VERDICT[d.verdict]}) et les pièces "
                f"{refs} ({LIBELLES_VERDICT[p.verdict]}) : vérification humaine "
                f"requise. Déclaré : {d.raison} Pièces : {p.raison}"
            )
            constate = f"Déclaré : {d.constate or '—'} ; pièces : {p.constate or '—'}"
        else:
            verdict = Verdict.A_VERIFIER
            raison = (
                f"Confiance faible de la lecture des pièces {refs} "
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
                "source": source,
                "verdict_pieces": p.verdict.value,
                "pieces": list(ids),
                "references": list(dict.fromkeys(alim.references)),
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
