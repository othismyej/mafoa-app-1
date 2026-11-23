"""Tâches Celery de l'évaluation."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="evaluation.relancer_eligibilite_lot")
def relancer_eligibilite_lot(
    *, lot_id, user_id, poste_id, candidature_ids, meta, ip=None, user_agent=""
) -> dict:
    """Relance en lot du calcul d'éligibilité au-delà du seuil synchrone (KAN-15, règle 25).

    Agit au nom de l'utilisateur qui a lancé le lot ; ne crée jamais de ``Decision`` et ne
    reclasse jamais une candidature déjà décidée (``run_eligibility``). Une transaction par
    candidature ; l'entrée ``lot`` de fin n'est écrite qu'une fois, même si la tâche est
    relivrée.
    """
    from .lots import relancer_en_tache

    rapport = relancer_en_tache(
        lot_id=lot_id,
        user_id=user_id,
        poste_id=poste_id,
        candidature_ids=candidature_ids,
        meta=meta,
        ip=ip,
        user_agent=user_agent,
    )
    return rapport.as_metadata()
