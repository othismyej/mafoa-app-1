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
    DetailsSpontanee,
    OffreEmploi,
    Organisation,
    Piece,
    PosteConcours,
)

from . import spontanees
from .base import SiteSource

#: Nombre maximal d'identifiants listés dans l'entrée d'audit pour chaque écart (les nombres
#: restent exacts).
MAX_IDS_AUDIT = 200


class SyncError(LookupError):
    """Donnée de la source inutilisable : la synchronisation échoue, transaction annulée."""


@dataclass
class SyncReport:
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    # KAN-39, règle 16 : candidatures créées / mises à jour par type, DTO refusés, conflits.
    candidatures_par_type: dict[str, dict[str, int]] = field(
        default_factory=lambda: {t: {"created": 0, "updated": 0} for t in Candidature.Type.values}
    )
    refusees: list[dict] = field(default_factory=list)
    conflits_type: list[dict] = field(default_factory=list)
    souhaits_ignores: list[str] = field(default_factory=list)
    pieces_ignorees: list[str] = field(default_factory=list)

    def count(self, model: str, created: bool) -> None:
        bucket = self.created if created else self.updated
        bucket[model] = bucket.get(model, 0) + 1

    def audit_metadata(self) -> dict:
        """Compteurs et identifiants seulement : aucun nom, email, téléphone ni CIN."""
        return {
            "candidatures_par_type": self.candidatures_par_type,
            "refusees": len(self.refusees),
            "refusees_detail": self.refusees[:MAX_IDS_AUDIT],
            "conflits_type": len(self.conflits_type),
            "conflits_type_detail": self.conflits_type[:MAX_IDS_AUDIT],
            "souhaits_ignores": len(self.souhaits_ignores),
            "souhaits_ignores_detail": self.souhaits_ignores[:MAX_IDS_AUDIT],
            "pieces_ignorees": len(self.pieces_ignorees),
        }


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

    candidatures = _sync_candidatures(source, report, now, candidats, postes, offres)
    refusees = {r["source_id"] for r in report.refusees}

    for f in source.list_pieces():
        if f.candidature_source_id in refusees:
            # Pièce d'une candidature refusée (règle 12) : non importée, comptée.
            report.pieces_ignorees.append(f.source_id)
            continue
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
        metadata={
            "source": source.name,
            "created": report.created,
            "updated": report.updated,
            **report.audit_metadata(),
        },
    )
    return report


def _sync_candidatures(source, report, now, candidats, postes, offres) -> dict[str, Candidature]:
    """Import des candidatures (KAN-39, règles 11 à 16).

    - le type est explicite (ou déduit d'une cible, jamais « spontanée » par défaut) ; un DTO
      incohérent est refusé et compté ;
    - une cible inconnue fait échouer la synchronisation (comportement inchangé, règle 13) ;
    - le type, la cible et le statut d'une candidature existante ne sont jamais modifiés : un
      changement à la source est compté comme conflit (règle 14) ;
    - les souhaits d'une candidature spontanée sont créés ou mis à jour (règle 15).
    """
    dtos = list(source.list_candidatures())
    ids = [a.source_id for a in dtos if a.source_id]
    existantes = {c.source_id: c for c in Candidature.objects.filter(source_id__in=ids)}
    result: dict[str, Candidature] = {}
    for a in dtos:
        resolu = spontanees.resoudre_type(a)
        if resolu.refus:
            report.refusees.append({"source_id": a.source_id, "motif": resolu.refus})
            continue
        type_ = resolu.type
        poste = offre = None
        if type_ == spontanees.CONCOURS:
            poste = postes.get(a.poste_source_id)
            if poste is None:
                raise SyncError(
                    f"Candidature {a.source_id} : poste {a.poste_source_id} inconnu de la source."
                )
        elif type_ == spontanees.OFFRE:
            offre = offres.get(a.offre_source_id)
            if offre is None:
                raise SyncError(
                    f"Candidature {a.source_id} : offre {a.offre_source_id} inconnue de la source."
                )
        candidat = candidats.get(a.candidat_source_id)
        if candidat is None:
            raise SyncError(
                f"Candidature {a.source_id} : candidat {a.candidat_source_id} inconnu de la source."
            )

        obj = existantes.get(a.source_id)
        created = obj is None
        if created:
            obj = Candidature(source_id=a.source_id, type=type_, poste_concours=poste, offre=offre)
        elif (obj.type, obj.poste_concours_id, obj.offre_id) != (
            type_,
            poste.pk if poste else None,
            offre.pk if offre else None,
        ):
            # Règle 14 : la candidature de l'application reste inchangée (type, cible, statut,
            # décisions) ; traitement humain (QS5).
            report.conflits_type.append({"candidature_id": obj.pk, "source_id": a.source_id})
        obj.candidat = candidat
        obj.date_candidature = a.date_candidature
        obj.donnees_declarees = a.donnees_declarees
        obj.source_synced_at = now
        if created:
            obj.save()
        else:
            obj.save(
                update_fields=[
                    "candidat",
                    "date_candidature",
                    "donnees_declarees",
                    "source_synced_at",
                    "updated_at",
                ]
            )
        report.count("Candidature", created)
        report.candidatures_par_type[obj.type]["created" if created else "updated"] += 1

        if obj.type == spontanees.SPONTANEE:
            if type_ == spontanees.SPONTANEE or a.souhaits is not None:
                DetailsSpontanee.objects.update_or_create(
                    candidature=obj,
                    defaults=spontanees.valeurs_souhaits(a.souhaits, a.date_mise_a_jour),
                )
        elif a.souhaits is not None:
            # Règle 12 : souhaits ignorés pour une candidature qui n'est pas spontanée.
            report.souhaits_ignores.append(a.source_id)
        result[a.source_id] = obj
    return result
