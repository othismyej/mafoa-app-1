"""Lecture directe de la base du site, en lecture seule (à implémenter).

Prévu : ``DATABASES["site"]`` avec un utilisateur en lecture seule, modèles non gérés
(``managed = False``) générés via ``inspectdb``, et un router qui interdit toute
écriture et toute migration sur ``site``. En attente des informations du développeur
du site (CLAUDE.md §10).
"""

from .base import UnavailableSource


class DatabaseSource(UnavailableSource):
    name = "database"
    reason = "accès à la base du site non encore obtenu."
