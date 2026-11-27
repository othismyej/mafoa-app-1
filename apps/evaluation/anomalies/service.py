"""Service de détection des anomalies (spec KAN-42, sections F, G, I et J).

Un seul point d'entrée, :func:`detecter_anomalies`, pour les trois déclencheurs (après
synchronisation, après extraction, relance manuelle) et la commande de gestion.

Garanties :

- **signaler, jamais décider** : seules des ``Anomalie`` sont créées et marquées « cause
  disparue » ; aucun statut, verdict, score ni ``Decision`` n'est lu pour décider ou modifié ;
  ``resolue`` n'est jamais écrit (règles 1 et 18) ;
- **idempotence** : clé non nominative par constat, unicité ``(candidature, cle)`` en base,
  verrous consultatifs PostgreSQL par candidature (règles 17 et 33) ;
- **volumes** : lots de ``DETECTION_LOT_TAILLE`` candidatures, une transaction par lot, nombre
  de requêtes borné par lot, écritures groupées ; l'échec d'un lot est compté et n'interrompt
  pas les autres (règle 33).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection, transaction
from django.utils import timezone

from apps.audit.models import AuditAction, JournalAudit
from apps.audit.services import client_ip, log
from apps.recruitment.models import Candidature, Piece

from ..models import Anomalie
from . import extractions as extractions_module
from .doublons import detecter_doublons
from .regles import (
    CLE_EXPERIENCE_AVANT_DIPLOME,
    FAMILLES_DOUBLONS,
    FAMILLES_TOUTES,
    PREFIXES_TOUJOURS,
    Constat,
    Couverture,
    controle_dates,
    controle_experience_avant_diplome,
    controle_extractions,
    controle_pieces_obligatoires,
)

logger = logging.getLogger(__name__)

DETECTEUR_VERSION = "1.0.0"
ACTEUR_DETECTION = "système (détection d'anomalies)"
DECLENCHEURS = ("sync", "extraction", "manuel", "commande")
#: Espace de noms des verrous consultatifs (``pg_advisory_xact_lock(int, int)``).
VERROU_ESPACE = 4242
MAX_IDS_AUDIT = 200
#: Contrôles dont l'exécution dépend des données (règles 12, 13 et 21).
CONTROLES_CONDITIONNELS = ("incoherence_nom", "ecart_declaration", "experience_avant_diplome")


@dataclass
class RapportDetection:
    run_id: str
    declencheur: str
    examinees: int = 0
    creees: dict[str, dict[str, int]] = field(default_factory=dict)
    cause_disparue: int = 0
    marques_retirees: int = 0
    non_executes: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(CONTROLES_CONDITIONNELS, 0)
    )
    erreurs: list[int] = field(default_factory=list)
    duree_s: float = 0.0

    @property
    def total_creees(self) -> int:
        return sum(n for par_gravite in self.creees.values() for n in par_gravite.values())

    def compter_creation(self, type_: str, gravite: str) -> None:
        bucket = self.creees.setdefault(type_, {})
        bucket[gravite] = bucket.get(gravite, 0) + 1

    def as_metadata(self) -> dict:
        """Compteurs et identifiants internes seulement (règles 23 et 34)."""
        return {
            "run_id": self.run_id,
            "declencheur": self.declencheur,
            "detecteur_version": DETECTEUR_VERSION,
            "examinees": self.examinees,
            "creees": self.creees,
            "creees_total": self.total_creees,
            "cause_disparue": self.cause_disparue,
            "marques_retirees": self.marques_retirees,
            "non_executes": self.non_executes,
            "erreurs": len(self.erreurs),
            "erreurs_ids": self.erreurs[:MAX_IDS_AUDIT],
            "duree_s": round(self.duree_s, 3),
        }


@dataclass
class _Contexte:
    rapport: RapportDetection
    declencheur: str
    acteur: object
    request: object
    now: object
    etendre: bool


def detecter_anomalies(
    candidatures: Iterable[int | Candidature],
    *,
    declencheur: str,
    acteur=None,
    request=None,
    run_id: str | None = None,
    etendre: bool = True,
    objet: Candidature | None = None,
) -> RapportDetection:
    """Détecte les anomalies des candidatures données (identifiants ou instances).

    ``etendre`` : réexamine aussi, pour les contrôles de doublons, les candidatures des comptes
    rapprochés (symétrie, règle 22) ; inutile quand toutes les candidatures sont traitées.
    ``objet`` : candidature portée par l'entrée d'audit ``detection`` (relance manuelle).
    """
    if declencheur not in DECLENCHEURS:
        raise ValueError(f"Déclencheur inconnu : {declencheur!r}")
    debut = time.monotonic()
    rapport = RapportDetection(run_id=run_id or str(uuid.uuid4()), declencheur=declencheur)
    if acteur is not None and not getattr(acteur, "is_authenticated", False):
        acteur = None
    ctx = _Contexte(rapport, declencheur, acteur, request, timezone.now(), etendre)
    ids = sorted({getattr(c, "pk", c) for c in candidatures})
    taille = max(1, settings.DETECTION_LOT_TAILLE)
    for i in range(0, len(ids), taille):
        lot = ids[i : i + taille]
        try:
            with transaction.atomic():
                _traiter_lot(lot, ctx)
        except Exception:  # noqa: BLE001 — un lot en échec n'interrompt pas les autres
            # Journal applicatif : identifiants internes seulement.
            logger.exception("Détection d'anomalies : échec du lot %s…%s", lot[0], lot[-1])
            rapport.erreurs.extend(lot)
    rapport.duree_s = time.monotonic() - debut
    metadata = rapport.as_metadata()
    if objet is not None:
        metadata["candidature_id"] = objet.pk
    log(
        AuditAction.DETECTION,
        actor=acteur,
        obj=objet,
        object_repr=f"Candidature #{objet.pk}" if objet is not None else "Détection d'anomalies",
        actor_repr="" if acteur else ACTEUR_DETECTION,
        request=request,
        metadata=metadata,
    )
    return rapport


# --------------------------------------------------------------------------------------
# Traitement d'un lot
# --------------------------------------------------------------------------------------


def _charger(ids) -> tuple[list[Candidature], dict[int, list[Piece]]]:
    candidatures = list(
        Candidature.objects.filter(pk__in=ids)
        .select_related("candidat", "poste_concours__concours", "offre")
        .order_by("pk")
    )
    pieces: dict[int, list[Piece]] = defaultdict(list)
    for p in (
        Piece.objects.filter(candidature_id__in=[c.pk for c in candidatures])
        .only("id", "candidature_id", "type", "taille", "sha256", "nom_fichier")
        .order_by("id")
    ):
        pieces[p.candidature_id].append(p)
    return candidatures, pieces


def _verrouiller(ids) -> None:
    """Sérialise les exécutions concurrentes sur les mêmes candidatures (règle 33)."""
    if not ids:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, s.id) "
            "FROM (SELECT unnest(%s::int4[]) AS id ORDER BY 1) AS s",
            [VERROU_ESPACE, sorted(ids)],
        )


def _traiter_lot(ids: list[int], ctx: _Contexte) -> None:
    rapport = ctx.rapport
    candidatures, pieces = _charger(ids)
    toutes_pieces = [p for c in candidatures for p in pieces[c.pk]]

    # Constats et couverture de chaque candidature examinée en entier.
    constats: dict[int, list[Constat]] = defaultdict(list)
    couvertures: dict[int, Couverture] = {}
    doublons = detecter_doublons(candidatures, toutes_pieces)
    extraites = extractions_module.extractions_par_candidature([c.pk for c in candidatures])
    for c in candidatures:
        couverture = Couverture(FAMILLES_TOUTES, set(PREFIXES_TOUJOURS))
        constats[c.pk].extend(doublons.constats.get(c.pk, []))
        constats[c.pk].extend(controle_pieces_obligatoires(c, pieces[c.pk]))
        constats[c.pk].extend(controle_dates(c))
        trouves, execute = controle_experience_avant_diplome(c)
        constats[c.pk].extend(trouves)
        if execute:
            couverture.prefixes.add(CLE_EXPERIENCE_AVANT_DIPLOME)
        else:
            rapport.non_executes["experience_avant_diplome"] += 1
        px = extraites.get(c.pk) or []
        if px:
            trouves, couverts = controle_extractions(c, pieces[c.pk], px)
            constats[c.pk].extend(trouves)
            couverture.prefixes |= couverts
        else:
            rapport.non_executes["incoherence_nom"] += 1
            rapport.non_executes["ecart_declaration"] += 1
        couvertures[c.pk] = couverture

    # Symétrie : comptes rapprochés réexaminés pour les seuls contrôles de doublons.
    examinees = {c.pk for c in candidatures}
    liees = sorted(doublons.liees - examinees) if ctx.etendre else []
    if liees:
        autres, autres_pieces = _charger(liees)
        resultat = detecter_doublons(autres, [p for c in autres for p in autres_pieces[c.pk]])
        for c in autres:
            constats[c.pk].extend(resultat.constats.get(c.pk, []))
            couvertures[c.pk] = Couverture(FAMILLES_DOUBLONS, set(FAMILLES_DOUBLONS))

    perimetre = sorted(couvertures)
    _verrouiller(perimetre)
    _reconcilier(perimetre, constats, couvertures, ctx)
    Candidature.objects.filter(pk__in=list(examinees)).update(anomalies_verifiees_le=ctx.now)
    rapport.examinees += len(examinees)


def _reconcilier(perimetre, constats, couvertures, ctx: _Contexte) -> None:
    """Règle 18 : création, marque « cause disparue », marque retirée ; rien d'autre."""
    rapport = ctx.rapport
    existantes: dict[tuple[int, str], Anomalie] = {}
    # Relues sous verrou : une résolution concurrente est visible ou attend (cas limites).
    for a in (
        Anomalie.objects.select_for_update(of=("self",))
        .filter(candidature_id__in=perimetre)
        .exclude(cle="")
        .only("id", "candidature_id", "cle", "resolue", "cause_disparue_le", "piece_id", "type")
    ):
        existantes[(a.candidature_id, a.cle)] = a

    a_creer: list[Anomalie] = []
    a_marquer: list[Anomalie] = []
    a_demarquer: list[Anomalie] = []
    for cid in perimetre:
        vus: dict[str, Constat] = {}
        for constat in constats.get(cid, []):
            vus.setdefault(constat.cle, constat)
        for cle, constat in vus.items():
            existante = existantes.get((cid, cle))
            if existante is None:
                a_creer.append(
                    Anomalie(
                        candidature_id=cid,
                        piece_id=constat.piece_id,
                        candidature_liee_id=constat.candidature_liee_id,
                        type=constat.type,
                        code=constat.code,
                        gravite=constat.gravite,
                        description=constat.description,
                        details=constat.details,
                        cle=cle,
                        detecteur_version=DETECTEUR_VERSION,
                    )
                )
            elif not existante.resolue and existante.cause_disparue_le is not None:
                a_demarquer.append(existante)
        couverture = couvertures[cid]
        for (acid, cle), existante in existantes.items():
            if (
                acid == cid
                and cle not in vus
                and not existante.resolue
                and existante.cause_disparue_le is None
                and couverture.couvre(cle, existante.piece_id)
            ):
                a_marquer.append(existante)

    creees = Anomalie.objects.bulk_create(a_creer) if a_creer else []
    if a_marquer:
        Anomalie.objects.filter(pk__in=[a.pk for a in a_marquer]).update(cause_disparue_le=ctx.now)
    if a_demarquer:
        Anomalie.objects.filter(pk__in=[a.pk for a in a_demarquer]).update(cause_disparue_le=None)

    entrees = []
    for a in creees:
        rapport.compter_creation(a.type, a.gravite)
        entrees.append(
            _entree(
                ctx,
                AuditAction.CREATE,
                a,
                after={"type": a.type, "code": a.code, "gravite": a.gravite},
            )
        )
    for a in a_marquer:
        entrees.append(
            _entree(
                ctx,
                AuditAction.UPDATE,
                a,
                before={"cause_disparue_le": None},
                after={"cause_disparue_le": ctx.now.isoformat()},
                operation="cause_disparue",
            )
        )
    for a in a_demarquer:
        entrees.append(
            _entree(
                ctx,
                AuditAction.UPDATE,
                a,
                before={"cause_disparue_le": a.cause_disparue_le.isoformat()},
                after={"cause_disparue_le": None},
                operation="marque_retiree",
            )
        )
    rapport.cause_disparue += len(a_marquer)
    rapport.marques_retirees += len(a_demarquer)
    if entrees:
        JournalAudit.objects.bulk_create(entrees)


def _entree(ctx: _Contexte, action, anomalie: Anomalie, *, before=None, after=None, operation=""):
    """Entrée d'audit d'une anomalie, sans aucune donnée personnelle (règle 34)."""
    request = ctx.request
    metadata = {
        "candidature_id": anomalie.candidature_id,
        "anomalie_id": anomalie.pk,
        "candidature_liee_id": anomalie.candidature_liee_id
        if action == AuditAction.CREATE
        else None,
        "piece_id": anomalie.piece_id,
        "run_id": ctx.rapport.run_id,
        "declencheur": ctx.declencheur,
        "detecteur_version": DETECTEUR_VERSION,
    }
    if action != AuditAction.CREATE:
        metadata.pop("candidature_liee_id")
        metadata["operation"] = operation
    return JournalAudit(
        actor=ctx.acteur,
        actor_repr=ctx.acteur.email if ctx.acteur else ACTEUR_DETECTION,
        action=action,
        content_type=ContentType.objects.get_for_model(Anomalie),
        object_id=str(anomalie.pk),
        object_repr=f"Anomalie #{anomalie.pk} (Candidature #{anomalie.candidature_id})",
        before=before,
        after=after,
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
        metadata=metadata,
    )
