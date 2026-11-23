"""Candidatures spontanées (vivier) : filtrage de la liste, compteurs, conservation (KAN-39).

Logique serveur unique (règle 25) : la vue, une future action en lot ou un export obtiennent
exactement le même ensemble de candidatures pour les mêmes paramètres
(:func:`filter_spontanees`). Aucun filtre, tri ni colonne sur le sexe, l'âge, la région, la
ville de résidence, la photo ou la situation familiale (règles 22 et 23).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import (
    Case,
    Count,
    DateTimeField,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest
from django.http import QueryDict
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.evaluation.eligibility.levels import normalize_text

from .filters import (
    ANOMALIE_CHOICES,
    ANOMALIES_AVEC,
    ANOMALIES_SANS,
    ANOMALIES_TOUTES,
    PAGE_SIZES,
    SEARCH_MAX_LENGTH,
    SEARCH_MIN_CHARS,
    anomalies_annotations,
)
from .models import Candidature, DetailsSpontanee, Fonction, Piece

S = Candidature.Statut
SPONTANEE = Candidature.Type.SPONTANEE

FONCTION_NON_PRECISEE = "non_precisee"
PIECES_TOUTES, PIECES_AVEC_CV, PIECES_SANS_CV = "toutes", "avec_cv", "sans_cv"
PIECES_CHOICES = [
    (PIECES_TOUTES, _("Toutes")),
    (PIECES_AVEC_CV, _("Avec CV")),
    (PIECES_SANS_CV, _("Sans CV")),
]
SORT_KEYS = ("candidat", "date", "fonction", "statut", "anomalies")
DEFAULT_SORT, DEFAULT_DESC = "date", True  # plus récentes d'abord (Q8)
DEFAULT_PAGE_SIZE = 50
OPTIONS_MAX = 100  # valeurs distinctes de secteur et de lieu proposées (règle 23)

STATUT_SORT_RANK = {v: i for i, v in enumerate(Candidature.STATUTS_SPONTANEE)}
# Tri par fonction : ordre alphabétique des libellés, « non précisée » en dernier.
FONCTION_SORT_RANK = {
    v: i
    for i, (v, _label) in enumerate(
        sorted(Fonction.choices, key=lambda c: normalize_text(str(c[1])))
    )
}


def fonction_choices() -> list[tuple[str, str]]:
    return [*Fonction.choices, (FONCTION_NON_PRECISEE, _("Non précisée"))]


def spontanees() -> QuerySet[Candidature]:
    return Candidature.objects.filter(type=SPONTANEE)


def _options(field_name: str) -> list[str]:
    """Valeurs distinctes présentes en base, triées, plafonnées (une requête)."""
    return list(
        DetailsSpontanee.objects.filter(candidature__type=SPONTANEE)
        .exclude(**{field_name: ""})
        .order_by(field_name)
        .values_list(field_name, flat=True)
        .distinct()[:OPTIONS_MAX]
    )


def options_secteurs() -> list[str]:
    return _options("secteur_souhaite")


def options_lieux() -> list[str]:
    return _options("lieu_souhaite")


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()) if value else None
    except ValueError:
        return None


@dataclass
class SpontaneeFilter:
    statuts: list[str] = field(default_factory=list)
    fonctions: list[str] = field(default_factory=list)
    secteurs: list[str] = field(default_factory=list)
    lieux: list[str] = field(default_factory=list)
    du: date | None = None
    au: date | None = None
    pieces: str = PIECES_TOUTES
    anomalies: str = ANOMALIES_TOUTES
    q: str = ""
    tri: str = DEFAULT_SORT
    desc: bool = DEFAULT_DESC
    page: str = "1"
    taille: int = DEFAULT_PAGE_SIZE

    @classmethod
    def from_querydict(
        cls, params: QueryDict, *, secteurs: list[str], lieux: list[str]
    ) -> SpontaneeFilter:
        """Lit les paramètres d'URL ; toute valeur inconnue est ignorée (règle 25)."""

        def valides(name: str, allowed) -> list[str]:
            allowed = set(allowed)
            return [v for v in dict.fromkeys(params.getlist(name)) if v in allowed]

        pieces = params.get("pieces", PIECES_TOUTES)
        if pieces not in {p for p, _ in PIECES_CHOICES}:
            pieces = PIECES_TOUTES
        anomalies = params.get("anomalies", ANOMALIES_TOUTES)
        if anomalies not in {a for a, _ in ANOMALIE_CHOICES}:
            anomalies = ANOMALIES_TOUTES
        tri_raw = params.get("tri") or ("-" if DEFAULT_DESC else "") + DEFAULT_SORT
        desc = tri_raw.startswith("-")
        tri = tri_raw[1:] if desc else tri_raw
        if tri not in SORT_KEYS:
            tri, desc = DEFAULT_SORT, DEFAULT_DESC
        try:
            taille = int(params.get("taille", DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError):
            taille = DEFAULT_PAGE_SIZE
        if taille not in PAGE_SIZES:
            taille = DEFAULT_PAGE_SIZE
        return cls(
            statuts=valides("statut", Candidature.STATUTS_SPONTANEE),
            fonctions=valides("fonction", [v for v, _ in fonction_choices()]),
            secteurs=valides("secteur", secteurs),
            lieux=valides("lieu", lieux),
            du=_parse_date(params.get("du")),
            au=_parse_date(params.get("au")),
            pieces=pieces,
            anomalies=anomalies,
            q=(params.get("q") or "")[:SEARCH_MAX_LENGTH],
            tri=tri,
            desc=desc,
            page=params.get("page") or "1",
            taille=taille,
        )

    # --- État ---------------------------------------------------------------
    @property
    def search_tokens(self) -> list[str]:
        tokens = normalize_text(self.q).split()
        if len("".join(tokens)) < SEARCH_MIN_CHARS:
            return []
        return tokens

    @property
    def is_filtered(self) -> bool:
        return bool(
            self.statuts
            or self.fonctions
            or self.secteurs
            or self.lieux
            or self.du
            or self.au
            or self.pieces != PIECES_TOUTES
            or self.anomalies != ANOMALIES_TOUTES
            or self.search_tokens
        )

    @property
    def tri_param(self) -> str:
        return f"-{self.tri}" if self.desc else self.tri

    @property
    def tri_par_defaut(self) -> bool:
        return (self.tri, self.desc) == (DEFAULT_SORT, DEFAULT_DESC)

    def sort_param_for(self, key: str) -> str:
        if key == self.tri:
            return key if self.desc else f"-{key}"
        return f"-{key}" if key == "date" else key

    def querystring(self, include_tri: bool = True) -> str:
        """Paramètres reconstruits à partir de l'état validé (jamais des valeurs brutes)."""
        params: list[tuple[str, str]] = [("statut", v) for v in self.statuts]
        params += [("fonction", v) for v in self.fonctions]
        params += [("secteur", v) for v in self.secteurs]
        params += [("lieu", v) for v in self.lieux]
        if self.du:
            params.append(("du", self.du.isoformat()))
        if self.au:
            params.append(("au", self.au.isoformat()))
        if self.pieces != PIECES_TOUTES:
            params.append(("pieces", self.pieces))
        if self.anomalies != ANOMALIES_TOUTES:
            params.append(("anomalies", self.anomalies))
        if self.q:
            params.append(("q", self.q))
        if include_tri and not self.tri_par_defaut:
            params.append(("tri", self.tri_param))
        if self.taille != DEFAULT_PAGE_SIZE:
            params.append(("taille", str(self.taille)))
        return urlencode(params)

    def as_metadata(self) -> dict:
        return {
            "statuts": self.statuts,
            "fonctions": self.fonctions,
            "secteurs": self.secteurs,
            "lieux": self.lieux,
            "du": self.du.isoformat() if self.du else "",
            "au": self.au.isoformat() if self.au else "",
            "pieces": self.pieces,
            "anomalies": self.anomalies,
            # Un nom n'est conservé que si la recherche s'applique réellement (KAN-10, Q4).
            "recherche": self.q if self.search_tokens else "",
            "tri": self.tri_param,
            "page": self.page,
            "taille": self.taille,
        }

    # --- Requête ------------------------------------------------------------
    def apply(self, queryset: QuerySet[Candidature]) -> QuerySet[Candidature]:
        qs = annotate_spontanees(queryset)
        if self.statuts:
            qs = qs.filter(statut__in=self.statuts)
        if self.fonctions:
            cond = Q()
            for f in self.fonctions:
                if f == FONCTION_NON_PRECISEE:
                    cond |= Q(details_spontanee__isnull=True) | Q(
                        details_spontanee__fonction_souhaitee=""
                    )
                else:
                    cond |= Q(details_spontanee__fonction_souhaitee=f)
            qs = qs.filter(cond)
        if self.secteurs:
            qs = qs.filter(details_spontanee__secteur_souhaite__in=self.secteurs)
        if self.lieux:
            qs = qs.filter(details_spontanee__lieu_souhaite__in=self.lieux)
        # Bornes incluses, dans le fuseau de l'application (règle 23).
        if self.du:
            qs = qs.filter(date_candidature__date__gte=self.du)
        if self.au:
            qs = qs.filter(date_candidature__date__lte=self.au)
        if self.pieces == PIECES_AVEC_CV:
            qs = qs.filter(a_cv=True)
        elif self.pieces == PIECES_SANS_CV:
            qs = qs.filter(a_cv=False)
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
            "fonction": "fonction_rang",
            "statut": "statut_rang",
            "anomalies": "nb_anomalies",
        }[self.tri]
        return [f"{prefix}{column}", f"{prefix}id"]  # départage stable


def _rank(field_name: str, ranks: dict[str, int], default: int) -> Case:
    return Case(
        *[When(**{field_name: k}, then=Value(v)) for k, v in ranks.items()],
        default=Value(default),
        output_field=IntegerField(),
    )


def annotate_spontanees(queryset: QuerySet[Candidature]) -> QuerySet[Candidature]:
    """Colonnes de la liste calculées en base : pièces, CV, anomalies, dossiers du candidat."""
    pieces = Piece.objects.filter(candidature=OuterRef("pk"))
    nb_pieces = Subquery(
        pieces.order_by().values("candidature").annotate(n=Count("id")).values("n")[:1],
        output_field=IntegerField(),
    )
    actives = Candidature.objects.filter(candidat=OuterRef("candidat"), type=SPONTANEE).exclude(
        statut=S.REJETEE
    )
    nb_actives = Subquery(
        actives.order_by().values("candidat").annotate(n=Count("id")).values("n")[:1],
        output_field=IntegerField(),
    )
    return queryset.annotate(
        a_cv=Exists(pieces.filter(type=Piece.Type.CV)),
        nb_pieces=Coalesce(nb_pieces, Value(0)),
        nb_spontanees_actives=Coalesce(nb_actives, Value(0)),
        **anomalies_annotations(),
    ).annotate(
        statut_rang=_rank("statut", STATUT_SORT_RANK, len(STATUT_SORT_RANK)),
        fonction_rang=_rank(
            "details_spontanee__fonction_souhaitee", FONCTION_SORT_RANK, len(FONCTION_SORT_RANK)
        ),
    )


def filter_spontanees(
    params: QueryDict | SpontaneeFilter, *, secteurs=None, lieux=None
) -> tuple[QuerySet[Candidature], SpontaneeFilter]:
    """Point d'entrée réutilisable : candidatures spontanées pour ces paramètres."""
    if isinstance(params, SpontaneeFilter):
        flt = params
    else:
        flt = SpontaneeFilter.from_querydict(
            params,
            secteurs=options_secteurs() if secteurs is None else secteurs,
            lieux=options_lieux() if lieux is None else lieux,
        )
    qs = flt.apply(spontanees().select_related("candidat", "details_spontanee"))
    return qs, flt


# --------------------------------------------------------------------------------------
# Tableau de bord (règle 41)
# --------------------------------------------------------------------------------------


@dataclass
class CompteursSpontanees:
    total: int
    a_traiter: int
    vivier: int
    rejetees: int
    recues_30_jours: int


def compteurs_spontanees(reference: datetime | None = None) -> CompteursSpontanees:
    """Une seule requête d'agrégat, quel que soit le volume."""
    reference = reference or timezone.now()
    depuis = reference - timedelta(days=30)
    r = spontanees().aggregate(
        total=Count("id"),
        a_traiter=Count("id", filter=Q(statut__in=[S.RECUE, S.EN_ANALYSE])),
        vivier=Count("id", filter=Q(statut=S.VIVIER)),
        rejetees=Count("id", filter=Q(statut=S.REJETEE)),
        recues_30_jours=Count(
            "id", filter=Q(date_candidature__gt=depuis, date_candidature__lte=reference)
        ),
    )
    return CompteursSpontanees(**r)


# --------------------------------------------------------------------------------------
# Conservation (loi 09-08, règle 36) : calcul seulement, la purge relève de KAN-31.
# --------------------------------------------------------------------------------------


def ajouter_mois(valeur, mois: int):
    """``valeur`` (date ou date-heure) + ``mois`` mois, jour ramené à la fin du mois si besoin."""
    total = valeur.month - 1 + mois
    annee, mois_ = valeur.year + total // 12, total % 12 + 1
    jour = min(valeur.day, calendar.monthrange(annee, mois_)[1])
    return valeur.replace(year=annee, month=mois_, day=jour)


def duree_conservation_mois() -> int:
    return settings.RETENTION_CANDIDATURE_SPONTANEE_MOIS


def debut_conservation(candidature: Candidature):
    """Date de candidature, ou dernière mise à jour à la source si plus récente (QS3)."""
    details = getattr(candidature, "details_spontanee", None)
    maj = details.mise_a_jour_source_le if details else None
    return max(candidature.date_candidature, maj) if maj else candidature.date_candidature


def fin_conservation(candidature: Candidature, mois: int | None = None) -> date:
    debut = timezone.localtime(debut_conservation(candidature)).date()
    return ajouter_mois(debut, duree_conservation_mois() if mois is None else mois)


def conservation_depassee(
    mois: int | None = None, reference: datetime | None = None
) -> QuerySet[Candidature]:
    """Candidatures spontanées dont la durée de conservation est dépassée à ``reference``.

    Lecture seule : ne supprime ni ne modifie rien (la purge relève de KAN-31).
    """
    mois = duree_conservation_mois() if mois is None else mois
    reference = reference or timezone.now()
    limite = ajouter_mois(reference, -mois)
    return (
        spontanees()
        .annotate(
            debut_conservation=Greatest(
                "date_candidature",
                Coalesce("details_spontanee__mise_a_jour_source_le", "date_candidature"),
                output_field=DateTimeField(),
            )
        )
        .filter(debut_conservation__lt=limite)
        .order_by("date_candidature", "id")
    )
