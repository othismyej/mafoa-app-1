"""Filtrage, recherche et tri des candidatures d'un poste (spec KAN-10).

Logique serveur unique : la liste (KAN-10) et les futures actions en lot (KAN-15) obtiennent
exactement le même ensemble de candidatures pour les mêmes paramètres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db.models import Case, Count, IntegerField, OuterRef, Q, QuerySet, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.http import QueryDict
from django.utils.translation import gettext_lazy as _

from apps.evaluation.eligibility.levels import normalize_text
from apps.evaluation.models import Anomalie, Verdict

from .models import Candidature, PosteConcours
from .selectors import NON_EVALUEE, latest_verdict_subquery

VERDICT_CHOICES: list[tuple[str, str]] = [
    (Verdict.ELIGIBLE.value, _("Éligible")),
    (Verdict.NON_ELIGIBLE.value, _("Non éligible")),
    (Verdict.A_VERIFIER.value, _("À vérifier")),
    (NON_EVALUEE, _("Non évaluée")),
]
ANOMALIES_TOUTES, ANOMALIES_AVEC, ANOMALIES_SANS = "toutes", "avec", "sans"
ANOMALIE_CHOICES = [
    (ANOMALIES_TOUTES, _("Toutes")),
    (ANOMALIES_AVEC, _("Avec anomalie non résolue")),
    (ANOMALIES_SANS, _("Sans anomalie non résolue")),
]
SORT_KEYS = ("candidat", "date", "statut", "verdict", "anomalies")
DEFAULT_SORT = "date"
PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50
SEARCH_MAX_LENGTH = 100
SEARCH_MIN_CHARS = 2

GRAVITE_RANK = {Anomalie.Gravite.INFO: 1, Anomalie.Gravite.MOYENNE: 2, Anomalie.Gravite.HAUTE: 3}
# Ordre « travail d'abord » du tri par verdict (règle 17).
VERDICT_SORT_RANK = {
    Verdict.A_VERIFIER.value: 0,
    Verdict.NON_ELIGIBLE.value: 1,
    Verdict.ELIGIBLE.value: 2,
}
STATUT_SORT_RANK = {value: i for i, (value, _label) in enumerate(Candidature.Statut.choices)}


def _rank_case(field_name: str, ranks: dict[str, int], default: int) -> Case:
    return Case(
        *[When(**{field_name: k}, then=Value(v)) for k, v in ranks.items()],
        default=Value(default),
        output_field=IntegerField(),
    )


@dataclass
class CandidatureFilter:
    statuts: list[str] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    anomalies: str = ANOMALIES_TOUTES
    q: str = ""
    tri: str = DEFAULT_SORT
    desc: bool = False
    page: str = "1"
    taille: int = DEFAULT_PAGE_SIZE

    @classmethod
    def from_querydict(cls, params: QueryDict) -> CandidatureFilter:
        """Lit les paramètres d'URL ; toute valeur inconnue est ignorée (règle 13)."""
        valid_statuts = {v for v, _ in Candidature.Statut.choices}
        valid_verdicts = {v for v, _ in VERDICT_CHOICES}
        statuts = [s for s in dict.fromkeys(params.getlist("statut")) if s in valid_statuts]
        verdicts = [v for v in dict.fromkeys(params.getlist("verdict")) if v in valid_verdicts]
        anomalies = params.get("anomalies", ANOMALIES_TOUTES)
        if anomalies not in {a for a, _ in ANOMALIE_CHOICES}:
            anomalies = ANOMALIES_TOUTES
        tri_raw = params.get("tri", DEFAULT_SORT)
        desc = tri_raw.startswith("-")
        tri = tri_raw[1:] if desc else tri_raw  # un seul « - » accepté
        if tri not in SORT_KEYS:
            tri, desc = DEFAULT_SORT, False
        try:
            taille = int(params.get("taille", DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError):
            taille = DEFAULT_PAGE_SIZE
        if taille not in PAGE_SIZES:
            taille = DEFAULT_PAGE_SIZE
        return cls(
            statuts=statuts,
            verdicts=verdicts,
            anomalies=anomalies,
            q=(params.get("q") or "")[:SEARCH_MAX_LENGTH],
            tri=tri,
            desc=desc,
            page=params.get("page") or "1",
            taille=taille,
        )

    # --- Recherche ----------------------------------------------------------
    @property
    def search_tokens(self) -> list[str]:
        """Mots de la recherche, normalisés comme ``Candidat.nom_normalise``."""
        tokens = normalize_text(self.q).split()
        if len("".join(tokens)) < SEARCH_MIN_CHARS:
            return []
        return tokens

    @property
    def is_filtered(self) -> bool:
        return bool(
            self.statuts
            or self.verdicts
            or self.anomalies != ANOMALIES_TOUTES
            or self.search_tokens
        )

    @property
    def tri_param(self) -> str:
        return f"-{self.tri}" if self.desc else self.tri

    def sort_param_for(self, key: str) -> str:
        """Paramètre ``tri`` d'un en-tête : bascule l'ordre si la colonne est déjà triée."""
        if key == self.tri:
            return key if self.desc else f"-{key}"
        return key

    def querystring(self, include_tri: bool = True) -> str:
        """Paramètres d'URL reconstruits à partir de l'état validé (jamais des valeurs brutes)."""
        params: list[tuple[str, str]] = [("statut", v) for v in self.statuts]
        params += [("verdict", v) for v in self.verdicts]
        if self.anomalies != ANOMALIES_TOUTES:
            params.append(("anomalies", self.anomalies))
        if self.q:
            params.append(("q", self.q))
        if include_tri and (self.tri, self.desc) != (DEFAULT_SORT, False):
            params.append(("tri", self.tri_param))
        if self.taille != DEFAULT_PAGE_SIZE:
            params.append(("taille", str(self.taille)))
        return urlencode(params)

    def as_metadata(self) -> dict:
        return {
            "statuts": self.statuts,
            "verdicts": self.verdicts,
            "anomalies": self.anomalies,
            # Un nom n'est conservé que si la recherche s'applique réellement (≥ 2 caractères).
            "recherche": self.q if self.search_tokens else "",
            "tri": self.tri_param,
            "page": self.page,
            "taille": self.taille,
        }

    # --- Requête ------------------------------------------------------------
    def apply(self, queryset: QuerySet[Candidature]) -> QuerySet[Candidature]:
        qs = annotate_list_columns(queryset)
        if self.statuts:
            qs = qs.filter(statut__in=self.statuts)
        if self.verdicts:
            cond = Q()
            for v in self.verdicts:
                cond |= (
                    Q(dernier_verdict__isnull=True) if v == NON_EVALUEE else Q(dernier_verdict=v)
                )
            qs = qs.filter(cond)
        if self.anomalies == ANOMALIES_AVEC:
            qs = qs.filter(nb_anomalies__gt=0)
        elif self.anomalies == ANOMALIES_SANS:
            qs = qs.filter(nb_anomalies=0)
        for token in self.search_tokens:
            qs = qs.filter(candidat__nom_normalise__contains=token)
        return qs.order_by(*self.ordering())

    def ordering(self) -> list:
        prefix = "-" if self.desc else ""
        column = {
            "candidat": "candidat__nom_normalise",
            "date": "date_candidature",
            "statut": "statut_rang",
            "verdict": "verdict_rang",
            "anomalies": "nb_anomalies",
        }[self.tri]
        # Départage stable par identifiant (règle 19).
        return [f"{prefix}{column}", f"{prefix}id"]


def annotate_list_columns(queryset: QuerySet[Candidature]) -> QuerySet[Candidature]:
    """Ajoute dernier verdict, anomalies non résolues et rangs de tri, calculés en base."""
    unresolved = Anomalie.objects.filter(candidature=OuterRef("pk"), resolue=False)
    nb_anomalies = Subquery(
        unresolved.order_by().values("candidature").annotate(n=Count("id")).values("n")[:1],
        output_field=IntegerField(),
    )
    gravite_max = Subquery(
        unresolved.annotate(rang=_rank_case("gravite", GRAVITE_RANK, 0))
        .order_by("-rang", "-id")
        .values("gravite")[:1]
    )
    return queryset.annotate(
        dernier_verdict=latest_verdict_subquery(),
        nb_anomalies=Coalesce(nb_anomalies, Value(0)),
        gravite_max=gravite_max,
    ).annotate(
        verdict_rang=_rank_case("dernier_verdict", VERDICT_SORT_RANK, len(VERDICT_SORT_RANK)),
        statut_rang=_rank_case("statut", STATUT_SORT_RANK, len(STATUT_SORT_RANK)),
    )


def filter_candidatures(poste: PosteConcours, params: QueryDict | CandidatureFilter):
    """Point d'entrée réutilisable (KAN-15) : candidatures d'un poste pour ces paramètres."""
    flt = (
        params
        if isinstance(params, CandidatureFilter)
        else CandidatureFilter.from_querydict(params)
    )
    qs = flt.apply(Candidature.objects.filter(poste_concours=poste).select_related("candidat"))
    return qs, flt
