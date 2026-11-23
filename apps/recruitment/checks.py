"""Contrôles système Django de l'application « recrutement »."""

from __future__ import annotations

from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.compatibility)
def check_retention_spontanees(app_configs=None, **kwargs) -> list[checks.CheckMessage]:
    """Durée de conservation des candidatures spontanées (KAN-39, règle 36) : au moins 1 mois.

    Une valeur nulle ou négative rendrait « dépassée » toute candidature dès sa réception : elle
    est refusée au démarrage (``manage.py check``, ``migrate``, ``runserver``).
    """
    valeur = getattr(settings, "RETENTION_CANDIDATURE_SPONTANEE_MOIS", None)
    if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 1:
        return [
            checks.Error(
                "RETENTION_CANDIDATURE_SPONTANEE_MOIS doit être un nombre entier de mois "
                f"supérieur ou égal à 1 (valeur : {valeur!r}).",
                hint="Corriger la variable d'environnement RETENTION_CANDIDATURE_SPONTANEE_MOIS.",
                id="recruitment.E001",
            )
        ]
    return []
