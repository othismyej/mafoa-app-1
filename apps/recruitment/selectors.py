"""Requêtes de lecture partagées par les écrans (concours, postes, candidatures)."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, OuterRef, Q, QuerySet, Subquery

from apps.evaluation.models import Verdict, VerificationEligibilite

from .models import Candidature, Concours, PosteConcours

NON_EVALUEE = "non_evaluee"


def latest_verdict_subquery() -> Subquery:
    """Résultat de la dernière vérification d'éligibilité d'une candidature.

    « Dernière » = la plus récente par ``created_at``, puis par identifiant le plus élevé.
    ``NULL`` si la candidature n'a jamais été évaluée.
    """
    return Subquery(
        VerificationEligibilite.objects.filter(candidature=OuterRef("pk"))
        .order_by("-created_at", "-id")
        .values("resultat")[:1]
    )


def with_latest_verdict(queryset: QuerySet[Candidature]) -> QuerySet[Candidature]:
    return queryset.annotate(dernier_verdict=latest_verdict_subquery())


@dataclass
class PosteStats:
    poste: PosteConcours
    candidatures: int = 0
    eligibles: int = 0
    non_eligibles: int = 0
    a_verifier: int = 0
    non_evaluees: int = 0

    @property
    def date_limite(self):
        # Même règle que le calcul d'expérience : une seule source de vérité.
        return self.poste.date_reference

    @property
    def date_limite_du_concours(self) -> bool:
        return self.poste.date_limite is None and self.poste.concours.date_limite is not None


@dataclass
class ConcoursStats:
    concours: Concours
    postes: list[PosteStats]

    def _sum(self, attr: str) -> int:
        return sum(getattr(p, attr) for p in self.postes)

    @property
    def nombre_postes(self) -> int:
        return sum(p.poste.nombre_postes for p in self.postes)

    @property
    def candidatures(self) -> int:
        return self._sum("candidatures")

    @property
    def eligibles(self) -> int:
        return self._sum("eligibles")

    @property
    def non_eligibles(self) -> int:
        return self._sum("non_eligibles")

    @property
    def a_verifier(self) -> int:
        return self._sum("a_verifier")

    @property
    def non_evaluees(self) -> int:
        return self._sum("non_evaluees")


def concours_stats(concours: Concours) -> ConcoursStats:
    """Postes d'un concours avec la répartition de leurs candidatures par dernier verdict.

    Deux requêtes quel que soit le volume : les postes, puis un agrégat calculé en base.
    La répartition ignore volontairement ``Candidature.statut`` (spec KAN-9, règle 5).
    """
    postes = list(concours.postes.select_related("concours"))
    rows = (
        with_latest_verdict(Candidature.objects.filter(poste_concours__concours=concours))
        .values("poste_concours")
        .annotate(
            total=Count("id"),
            eligibles=Count("id", filter=Q(dernier_verdict=Verdict.ELIGIBLE)),
            non_eligibles=Count("id", filter=Q(dernier_verdict=Verdict.NON_ELIGIBLE)),
            a_verifier=Count("id", filter=Q(dernier_verdict=Verdict.A_VERIFIER)),
            non_evaluees=Count("id", filter=Q(dernier_verdict__isnull=True)),
        )
        .order_by()
    )
    by_poste = {r["poste_concours"]: r for r in rows}
    stats = []
    for poste in postes:
        r = by_poste.get(poste.pk, {})
        stats.append(
            PosteStats(
                poste=poste,
                candidatures=r.get("total", 0),
                eligibles=r.get("eligibles", 0),
                non_eligibles=r.get("non_eligibles", 0),
                a_verifier=r.get("a_verifier", 0),
                non_evaluees=r.get("non_evaluees", 0),
            )
        )
    return ConcoursStats(concours=concours, postes=stats)
