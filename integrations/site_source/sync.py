"""Import idempotent des données d'une ``SiteSource`` vers les modèles de l'application.

La clé de rapprochement est ``source_id``. Le site reste la source de vérité pour les
données importées ; les statuts et évaluations propres à l'application ne sont pas écrasés.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log
from apps.recruitment.models import (
    Candidat,
    Candidature,
    Concours,
    OffreEmploi,
    Organisation,
    Piece,
    PosteConcours,
)

from .base import SiteSource


@dataclass
class SyncReport:
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)

    def count(self, model: str, created: bool) -> None:
        bucket = self.created if created else self.updated
        bucket[model] = bucket.get(model, 0) + 1


def _upsert(model, report: SyncReport, source_id: str, now, **values):
    obj, created = model.objects.update_or_create(
        source_id=source_id, defaults={**values, "source_synced_at": now}
    )
    report.count(model.__name__, created)
    return obj


@transaction.atomic
def sync_from_source(source: SiteSource, *, actor=None, compute_hashes: bool = True) -> SyncReport:
    now = timezone.now()
    report = SyncReport()

    orgs = {
        o.source_id: _upsert(
            Organisation, report, o.source_id, now, nom=o.nom, type=o.type, ville=o.ville
        )
        for o in source.list_organisations()
    }
    concours = {}
    for c in source.list_concours():
        data = asdict(c)
        data.pop("source_id")
        data["organisation"] = orgs[data.pop("organisation_source_id")]
        concours[c.source_id] = _upsert(Concours, report, c.source_id, now, **data)
    postes = {}
    for p in source.list_postes():
        data = asdict(p)
        data.pop("source_id")
        data["concours"] = concours[data.pop("concours_source_id")]
        postes[p.source_id] = _upsert(PosteConcours, report, p.source_id, now, **data)
    offres = {}
    for o in source.list_offres():
        data = asdict(o)
        data.pop("source_id")
        data["organisation"] = orgs[data.pop("organisation_source_id")]
        offres[o.source_id] = _upsert(OffreEmploi, report, o.source_id, now, **data)

    candidats = {}
    for c in source.list_candidats():
        data = asdict(c)
        data.pop("source_id")
        candidats[c.source_id] = _upsert(Candidat, report, c.source_id, now, **data)

    candidatures = {}
    for a in source.list_candidatures():
        candidatures[a.source_id] = _upsert(
            Candidature,
            report,
            a.source_id,
            now,
            candidat=candidats[a.candidat_source_id],
            poste_concours=postes.get(a.poste_source_id) if a.poste_source_id else None,
            offre=offres.get(a.offre_source_id) if a.offre_source_id else None,
            date_candidature=a.date_candidature,
            donnees_declarees=a.donnees_declarees,
        )

    for f in source.list_pieces():
        values = {
            "candidature": candidatures[f.candidature_source_id],
            "type": f.type,
            "nom_fichier": f.nom_fichier,
            "storage_key": f.storage_key,
            "content_type": f.content_type,
            "recue_le": f.recue_le,
        }
        if compute_hashes:
            # Lecture à la demande : seule l'empreinte est conservée, pas le fichier.
            content = source.read_piece(f.storage_key)
            values["sha256"] = hashlib.sha256(content).hexdigest()
            values["taille"] = len(content)
        _upsert(Piece, report, f.source_id, now, **values)

    log(
        AuditAction.SYNC,
        actor=actor,
        actor_repr="" if actor else f"synchronisation ({source.name})",
        metadata={"source": source.name, "created": report.created, "updated": report.updated},
    )
    return report
