"""Requêtes de lecture partagées par les écrans (concours, postes, candidatures)."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import (
    Case,
    Count,
    F,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)

from apps.evaluation.models import Verdict, VerificationEligibilite

from .models import Candidature, Concours, OffreEmploi, PosteConcours

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


# --------------------------------------------------------------------------------------
# Offres d'emploi, tableau de bord et dossiers à vérifier (spec KAN-38)
# --------------------------------------------------------------------------------------

#: « Dossier à vérifier » = statut « À vérifier » : doute du moteur, aucune décision humaine
#: (KAN-38, règle 2).
A_VERIFIER = Candidature.Statut.A_VERIFIER


def offres_avec_compteurs() -> QuerySet[OffreEmploi]:
    """Offres annotées (candidatures, à vérifier), ouvertes d'abord puis les plus récentes.

    Une seule requête (agrégat en base), quel que soit le volume (règles 5, 7 et 9).
    """
    return (
        OffreEmploi.objects.select_related("organisation")
        .annotate(
            nb_candidatures=Count("candidatures", distinct=True),
            nb_a_verifier=Count(
                "candidatures", filter=Q(candidatures__statut=A_VERIFIER), distinct=True
            ),
            ouverte_rang=Case(
                When(statut=OffreEmploi.Statut.OUVERTE, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by("ouverte_rang", F("date_publication").desc(nulls_last=True), "-id")
    )


@dataclass
class TableauDeBord:
    concours: int
    concours_en_cours: int
    postes: int
    offres: int
    offres_ouvertes: int
    candidatures_concours: int
    candidatures_offres: int
    a_verifier: int
    candidatures_spontanees: int = 0  # KAN-39 : troisième famille

    @property
    def candidatures(self) -> int:
        return self.candidatures_concours + self.candidatures_offres + self.candidatures_spontanees


def tableau_de_bord() -> TableauDeBord:
    """Chiffres clés du tableau de bord : quatre requêtes d'agrégat, constantes (règle 23)."""
    concours = Concours.objects.aggregate(
        total=Count("id"),
        en_cours=Count(
            "id",
            filter=~Q(statut__in=[Concours.Statut.BROUILLON, Concours.Statut.TERMINE]),
        ),
    )
    offres = OffreEmploi.objects.aggregate(
        total=Count("id"), ouvertes=Count("id", filter=Q(statut=OffreEmploi.Statut.OUVERTE))
    )
    candidatures = Candidature.objects.aggregate(
        concours=Count("id", filter=Q(poste_concours__isnull=False)),
        offres=Count("id", filter=Q(offre__isnull=False)),
        spontanees=Count("id", filter=Q(type=Candidature.Type.SPONTANEE)),
        a_verifier=Count("id", filter=Q(statut=A_VERIFIER)),
    )
    return TableauDeBord(
        concours=concours["total"],
        concours_en_cours=concours["en_cours"],
        postes=PosteConcours.objects.count(),
        offres=offres["total"],
        offres_ouvertes=offres["ouvertes"],
        candidatures_concours=candidatures["concours"],
        candidatures_offres=candidatures["offres"],
        a_verifier=candidatures["a_verifier"],
        candidatures_spontanees=candidatures["spontanees"],
    )


@dataclass
class CibleAVerifier:
    cible: PosteConcours | OffreEmploi
    nombre: int

    @property
    def est_offre(self) -> bool:
        return isinstance(self.cible, OffreEmploi)

    @property
    def libelle(self) -> str:
        return str(self.cible)


def dossiers_a_verifier() -> list[CibleAVerifier]:
    """Postes et offres ayant des dossiers à vérifier, du plus chargé au moins chargé.

    Trois requêtes quel que soit le volume : l'agrégat, puis les postes et les offres concernés
    (règles 25 et 26).
    """
    rows = list(
        Candidature.objects.filter(statut=A_VERIFIER)
        .values("poste_concours", "offre")
        .annotate(n=Count("id"))
        .order_by()
    )
    postes = PosteConcours.objects.select_related("concours").in_bulk(
        [r["poste_concours"] for r in rows if r["poste_concours"]]
    )
    offres = OffreEmploi.objects.select_related("organisation").in_bulk(
        [r["offre"] for r in rows if r["offre"]]
    )
    result = []
    for r in rows:
        cible = postes.get(r["poste_concours"]) or offres.get(r["offre"])
        if cible is not None:
            result.append(CibleAVerifier(cible=cible, nombre=r["n"]))
    result.sort(key=lambda c: (-c.nombre, c.libelle.casefold(), c.cible.pk))
    return result
