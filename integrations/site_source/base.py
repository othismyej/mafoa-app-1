"""Interface commune de lecture du site Mafoa (lecture seule)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime

from .dto import (
    CandidatDTO,
    CandidatureDTO,
    ConcoursDTO,
    OffreEmploiDTO,
    OrganisationDTO,
    PieceDTO,
    PosteConcoursDTO,
)


class SiteSource(ABC):
    """Source de données du site. Toutes les implémentations sont en lecture seule."""

    name: str = "abstract"

    @abstractmethod
    def list_organisations(self) -> Iterable[OrganisationDTO]: ...

    @abstractmethod
    def list_concours(self) -> Iterable[ConcoursDTO]: ...

    @abstractmethod
    def list_postes(self) -> Iterable[PosteConcoursDTO]: ...

    @abstractmethod
    def list_offres(self) -> Iterable[OffreEmploiDTO]: ...

    @abstractmethod
    def list_candidats(self, since: datetime | None = None) -> Iterable[CandidatDTO]: ...

    @abstractmethod
    def list_candidatures(self, since: datetime | None = None) -> Iterable[CandidatureDTO]: ...

    @abstractmethod
    def list_pieces(self, since: datetime | None = None) -> Iterable[PieceDTO]: ...

    @abstractmethod
    def read_piece(self, storage_key: str) -> bytes:
        """Lit le contenu d'une pièce à la demande (aucune copie permanente)."""


class UnavailableSource(SiteSource):
    """Base des sources pas encore disponibles : chaque méthode lève NotImplementedError."""

    reason = "source non disponible"

    def _unavailable(self, *args, **kwargs):
        raise NotImplementedError(f"{type(self).__name__} : {self.reason}")

    list_organisations = list_concours = list_postes = list_offres = _unavailable
    list_candidats = list_candidatures = list_pieces = read_piece = _unavailable


def get_source(name: str | None = None) -> SiteSource:
    from django.conf import settings

    name = name or settings.SITE_SOURCE
    if name == "fixture":
        from .fixture_source import FixtureSource

        return FixtureSource(seed=settings.SITE_SOURCE_FIXTURE_SEED)
    if name == "database":
        from .database_source import DatabaseSource

        return DatabaseSource()
    if name == "api":
        from .api_source import ApiSource

        return ApiSource()
    raise ValueError(f"Source inconnue : {name!r}")
