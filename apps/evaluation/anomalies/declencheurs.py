"""Mise en file de la détection (spec KAN-42, règle 22) : tâche Celery après validation.

La mise en file n'échoue jamais pour l'appelant : broker indisponible → détection « non
lancée », rattrapée à la synchronisation suivante ou manuellement.
"""

from __future__ import annotations

import logging
import uuid

from celery.result import EagerResult
from django.db import transaction

logger = logging.getLogger(__name__)

LANCEE, NON_LANCEE = "lancee", "non_lancee"


def mettre_en_file(candidature_ids, *, declencheur: str, run_id: str | None = None) -> dict:
    """Met la tâche en file immédiatement ; renvoie ``{"detection": état, "run_id", ...}``.

    En mode immédiat (``CELERY_TASK_ALWAYS_EAGER``), le rapport de la tâche est joint.
    """
    from ..tasks import detecter_anomalies_task

    run_id = run_id or str(uuid.uuid4())
    ids = sorted({int(i) for i in candidature_ids})
    try:
        resultat = detecter_anomalies_task.apply_async(
            kwargs={"candidature_ids": ids, "declencheur": declencheur, "run_id": run_id},
            retry=False,
        )
    except Exception as exc:  # noqa: BLE001 — broker indisponible : jamais bloquant
        logger.warning(
            "Détection d'anomalies non lancée (%s) : %s", declencheur, type(exc).__name__
        )
        return {"detection": NON_LANCEE, "run_id": run_id, "erreur": type(exc).__name__}
    etat = {"detection": LANCEE, "run_id": run_id}
    if isinstance(resultat, EagerResult) and resultat.successful():
        etat["rapport"] = resultat.result
    return etat


def planifier_detection(candidature_ids, *, declencheur: str, rappel=None) -> str:
    """Met la détection en file **à la validation** de la transaction courante.

    ``rappel(etat)`` est appelé avec l'état de la mise en file (``mettre_en_file``). Rien
    n'est mis en file si la transaction est annulée (CA41). Renvoie le ``run_id``.
    """
    ids = sorted({int(i) for i in candidature_ids})
    run_id = str(uuid.uuid4())

    def lancer():
        etat = mettre_en_file(ids, declencheur=declencheur, run_id=run_id)
        if rappel is not None:
            rappel(etat)

    if ids:
        transaction.on_commit(lancer)
    return run_id
