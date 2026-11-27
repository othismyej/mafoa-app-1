"""Résolution et réouverture humaines d'une anomalie (spec KAN-42, section H).

Seul chemin d'écriture de ``resolue`` : motif obligatoire, verrou de la ligne, audit. Aucun
effet sur le statut, le verdict, les décisions ni les autres anomalies (règles 27 et 28).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import log

from .. import decisions
from ..decisions import DecisionError
from ..models import Anomalie

RESOLUTION, REOUVERTURE = "resolution", "reouverture"


class AnomalieError(Exception):
    """Résolution ou réouverture refusée : rien n'est enregistré."""

    def __init__(self, message: str, code: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def nettoyer_motif(motif: str | None) -> str:
    """Mêmes bornes et messages que les décisions (``decisions.nettoyer_motif``)."""
    try:
        return decisions.nettoyer_motif(motif)
    except DecisionError as exc:
        raise AnomalieError(exc.message, exc.code) from exc


def issues_permises(anomalie: Anomalie) -> list[str]:
    issues = [Anomalie.Issue.CONFIRMEE, Anomalie.Issue.FAUX_POSITIF]
    if anomalie.cause_disparue_le is not None:
        issues.append(Anomalie.Issue.CAUSE_DISPARUE)
    return [str(i) for i in issues]


def _journaliser(anomalie, user, request, operation, motif, before, after):
    log(
        AuditAction.UPDATE,
        actor=user,
        obj=anomalie,
        object_repr=f"Anomalie #{anomalie.pk} (Candidature #{anomalie.candidature_id})",
        before=before,
        after=after,
        request=request,
        metadata={
            "candidature_id": anomalie.candidature_id,
            "anomalie_id": anomalie.pk,
            "motif": motif,
            "operation": operation,
        },
    )


def resoudre(anomalie_id: int, user, issue: str, motif: str, request=None) -> Anomalie:
    if not issue:
        raise AnomalieError(_("Choisissez l’issue de la vérification."), "issue_absente")
    if issue not in Anomalie.Issue.values:
        raise AnomalieError(_("Issue de résolution inconnue."), "issue_interdite")
    motif = nettoyer_motif(motif)
    with transaction.atomic():
        a = Anomalie.objects.select_for_update().get(pk=anomalie_id)
        if a.resolue:
            raise AnomalieError(
                _("Cette anomalie a déjà été résolue. La fiche a été actualisée."),
                "deja_resolue",
                status=409,
            )
        if issue not in issues_permises(a):
            raise AnomalieError(
                _(
                    "« Cause disparue, confirmée » n’est possible que pour une anomalie dont la "
                    "cause a disparu."
                ),
                "issue_interdite",
            )
        a.resolue = True
        a.resolue_par = user
        a.resolue_le = timezone.now()
        a.issue_resolution = issue
        a.motif_resolution = motif
        a.save(
            update_fields=[
                "resolue",
                "resolue_par",
                "resolue_le",
                "issue_resolution",
                "motif_resolution",
            ]
        )
        _journaliser(
            a,
            user,
            request,
            RESOLUTION,
            motif,
            before={"resolue": False, "issue_resolution": ""},
            after={"resolue": True, "issue_resolution": issue},
        )
    return a


def rouvrir(anomalie_id: int, user, motif: str, request=None) -> Anomalie:
    motif = nettoyer_motif(motif)
    with transaction.atomic():
        a = Anomalie.objects.select_for_update().get(pk=anomalie_id)
        if not a.resolue:
            raise AnomalieError(
                _("Cette anomalie a déjà été rouverte. La fiche a été actualisée."),
                "deja_ouverte",
                status=409,
            )
        issue_avant = a.issue_resolution
        a.resolue = False
        a.resolue_par = None
        a.resolue_le = None
        a.issue_resolution = ""
        a.motif_resolution = ""
        a.cause_disparue_le = None
        a.save(
            update_fields=[
                "resolue",
                "resolue_par",
                "resolue_le",
                "issue_resolution",
                "motif_resolution",
                "cause_disparue_le",
            ]
        )
        _journaliser(
            a,
            user,
            request,
            REOUVERTURE,
            motif,
            before={"resolue": True, "issue_resolution": issue_avant},
            after={"resolue": False, "issue_resolution": ""},
        )
    return a
