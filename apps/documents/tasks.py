"""Tâches Celery de la lecture des pièces (KAN-41, règle 12)."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="documents.lire_piece")
def lire_piece(extraction_id: int) -> dict:
    """Lit une pièce (une tâche par pièce). Sans effet si la lecture n'est plus en attente
    (relivraison) ; ne crée jamais de décision."""
    from .lecture import executer_lecture

    extraction = executer_lecture(extraction_id)
    if extraction is None:
        return {"extraction_id": extraction_id, "statut": "deja_traitee"}
    return {"extraction_id": extraction.pk, "statut": extraction.statut, "code": extraction.code}
