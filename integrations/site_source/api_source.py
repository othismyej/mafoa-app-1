"""Lecture via l'API REST du site (cible), authentifiée par token, en lecture seule.

À implémenter lorsque l'API sera exposée par le site (CLAUDE.md §4 et §10).
"""

from .base import UnavailableSource


class ApiSource(UnavailableSource):
    name = "api"
    reason = "l'API du site n'existe pas encore."
