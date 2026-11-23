"""Actions en lot sur les candidatures d'un poste (spec KAN-15).

Deux actions seulement : relancer le calcul d'éligibilité, ou enregistrer une même décision
d'avancement (éligible, présélectionnée, convoquée) avec un motif commun.

Déroulé :

1. ``preparer_lot`` classe la sélection (concernées, non concernées par raison, dossiers
   signalés par un avertissement) et fige le résultat dans une charge utile ;
2. ``signer_jeton`` / ``lire_jeton`` : jeton signé (30 minutes) portant cette charge ;
3. ``executer_decisions`` / ``executer_relance`` : traitement des seules candidatures du jeton,
   une transaction par candidature (``record_decision`` / ``run_eligibility``), rapport partiel.

Aucun nom de candidat n'est écrit dans le journal : seulement des identifiants internes.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace

from django.conf import settings
from django.core import signing
from django.db import connection, transaction
from django.db.models import OuterRef, QuerySet, Subquery

from apps.audit.models import AuditAction, JournalAudit
from apps.audit.services import log
from apps.recruitment.models import Candidature, Concours, PosteConcours

from . import decisions
from .decisions import ConfirmationRequise, DecisionError
from .models import Decision, Verdict, VerificationEligibilite

logger = logging.getLogger(__name__)

S = Candidature.Statut

OPERATION_DECISION = "decision"
OPERATION_RELANCE = "relance_eligibilite"
MODES = ("ids", "filtre")

# Raisons « non concernée » (règle 13).
NC_TRANSITION = "transition_interdite"
NC_DEJA = "deja"
NC_REVISION = "revision"
NC_CONCOURS_TERMINE = "concours_termine"

# Raisons d'échec à l'exécution (règle 21).
ECHEC_MODIFIEE = "modifiee"
ECHEC_TRANSITION = "transition"
ECHEC_CONCOURS_TERMINE = "concours_termine"
ECHEC_AVERTISSEMENT = "nouvel_avertissement"
ECHEC_TECHNIQUE = "erreur_technique"
ECHECS = (
    ECHEC_MODIFIEE,
    ECHEC_TRANSITION,
    ECHEC_CONCOURS_TERMINE,
    ECHEC_AVERTISSEMENT,
    ECHEC_TECHNIQUE,
)

CHOIX_EXCLURE, CHOIX_INCLURE = "exclure", "inclure"
AVERTISSEMENTS_LOT = (decisions.AVERTISSEMENT_ANOMALIE, decisions.AVERTISSEMENT_VERDICT)

JETON_SALT = "mafoa.kan15.lot"
JETON_VERSION = 1


def taille_max() -> int:
    return int(getattr(settings, "LOT_TAILLE_MAX", 500))


def seuil_synchrone() -> int:
    return int(getattr(settings, "LOT_ELIGIBILITE_SYNC_MAX", 50))


def duree_jeton() -> int:
    return int(getattr(settings, "LOT_JETON_DUREE", 30 * 60))


class LotError(Exception):
    """Lot refusé : rien n'est fait. ``code`` sert à la journalisation des refus anormaux."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message


class JetonInvalide(LotError):
    pass


class LotDejaExecute(LotError):
    pass


# --------------------------------------------------------------------------------------
# Sélection et classement
# --------------------------------------------------------------------------------------


def annoter_pour_lot(queryset: QuerySet[Candidature]) -> QuerySet[Candidature]:
    """Ajoute l'identifiant de la dernière décision humaine (même ordre que KAN-13)."""
    return queryset.annotate(
        derniere_decision_id=Subquery(
            Decision.objects.filter(candidature=OuterRef("pk"))
            .order_by("-created_at", "-id")
            .values("id")[:1]
        )
    )


def classer(statut: str, decision: str, a_une_decision: bool, concours_termine: bool) -> str | None:
    """Raison de non-concernement d'une candidature pour une décision en lot, ou ``None``."""
    if concours_termine:
        return NC_CONCOURS_TERMINE
    if decisions.transition_lot_permise(statut, decision, a_une_decision):
        return None
    if statut == decision:
        return NC_DEJA
    if decisions.est_revision(statut, decision, a_une_decision):
        return NC_REVISION
    return NC_TRANSITION


def avertissements_candidature(decision: str, gravite_max: str | None, verdict: str | None):
    """Avertissements d'une décision en lot (règle 14)."""
    result = []
    if decision in (S.PRESELECTIONNEE, S.CONVOQUEE) and gravite_max == "haute":
        result.append(decisions.AVERTISSEMENT_ANOMALIE)
    if decision == S.ELIGIBLE and verdict != Verdict.ELIGIBLE:
        result.append(decisions.AVERTISSEMENT_VERDICT)
    return result


def filtres_sans_recherche(flt) -> dict:
    """Filtres appliqués, sans le terme de recherche (règle 29)."""
    meta = flt.as_metadata()
    return {
        "statuts": meta["statuts"],
        "verdicts": meta["verdicts"],
        "anomalies": meta["anomalies"],
        "tri": meta["tri"],
        "taille": meta["taille"],
    }


def preparer_lot(
    *,
    poste: PosteConcours,
    user,
    operation: str,
    decision: str,
    mode: str,
    flt,
    candidatures: list[Candidature],
) -> dict:
    """Classe la sélection et renvoie la charge utile figée du lot (règles 13 à 16).

    ``candidatures`` : objets issus de ``filter_candidatures`` (annotés ``dernier_verdict``,
    ``gravite_max``) puis ``annoter_pour_lot`` (``derniere_decision_id``).
    """
    termine = poste.concours.statut == Concours.Statut.TERMINE
    repartition = Counter(c.statut for c in candidatures)
    concernees: list[list] = []
    groupes: dict[tuple[str, str], list[int]] = defaultdict(list)
    signales: dict[str, list[int]] = defaultdict(list)
    deja_decidees: list[int] = []
    for c in sorted(candidatures, key=lambda c: c.pk):
        a_decision = c.derniere_decision_id is not None
        if operation == OPERATION_RELANCE:
            raison = NC_CONCOURS_TERMINE if termine else None
        else:
            raison = classer(c.statut, decision, a_decision, termine)
        if raison is not None:
            statut = c.statut if raison in (NC_TRANSITION, NC_DEJA) else ""
            groupes[(raison, statut)].append(c.pk)
            continue
        concernees.append([c.pk, c.statut, c.derniere_decision_id])
        if operation == OPERATION_RELANCE:
            if a_decision:
                deja_decidees.append(c.pk)
        else:
            for a in avertissements_candidature(decision, c.gravite_max, c.dernier_verdict):
                signales[a].append(c.pk)
    return {
        "v": JETON_VERSION,
        "lot": str(uuid.uuid4()),
        "u": user.pk,
        "p": poste.pk,
        "op": operation,
        "d": decision if operation == OPERATION_DECISION else "",
        "m": mode,
        "r": flt.querystring(include_tri=True),
        "f": filtres_sans_recherche(flt) if mode == "filtre" else None,
        "q": bool(flt.search_tokens) if mode == "filtre" else False,
        "n": len(candidatures),
        "rep": dict(repartition),
        "c": concernees,
        "nc": [[raison, statut, ids] for (raison, statut), ids in groupes.items()],
        "w": dict(signales),
        "dd": deja_decidees,
        "ct": termine,
    }


def ids_selection(payload: dict) -> list[int]:
    ids = [c[0] for c in payload["c"]]
    for _raison, _statut, groupe in payload["nc"]:
        ids.extend(groupe)
    return ids


# --------------------------------------------------------------------------------------
# Jeton signé (règles 16 à 18)
# --------------------------------------------------------------------------------------


def signer_jeton(payload: dict) -> str:
    return signing.dumps(payload, salt=JETON_SALT, compress=True)


def lire_jeton(jeton: str, *, user, poste: PosteConcours) -> dict:
    """Vérifie signature, durée, utilisateur et poste. Lève ``JetonInvalide``.

    ``code`` : ``expire`` (jeton périmé, non journalisé) ou ``jeton_invalide`` (signature
    altérée, autre utilisateur ou autre poste : journalisé, règle 30).
    """
    try:
        payload = signing.loads(jeton or "", salt=JETON_SALT, max_age=duree_jeton())
    except signing.SignatureExpired as exc:
        raise JetonInvalide("expire") from exc
    except (signing.BadSignature, ValueError, TypeError) as exc:
        raise JetonInvalide("jeton_invalide") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != JETON_VERSION
        or payload.get("u") != user.pk
        or payload.get("p") != poste.pk
    ):
        raise JetonInvalide("jeton_invalide")
    return payload


# --------------------------------------------------------------------------------------
# Journal des lots (règles 29 et 30)
# --------------------------------------------------------------------------------------


def _requete_differee(user, ip: str | None, user_agent: str):
    """Substitut de requête pour une tâche Celery : IP et navigateur de la requête d'origine."""
    return SimpleNamespace(
        META={"REMOTE_ADDR": ip or "", "HTTP_USER_AGENT": user_agent or ""}, user=user
    )


def lot_deja_journalise(lot_id: str, etape: str | None = None) -> bool:
    qs = JournalAudit.objects.filter(action=AuditAction.LOT, metadata__lot_id=str(lot_id))
    if etape:
        qs = qs.filter(metadata__etape=etape)
    return qs.exists()


def meta_lot(payload: dict, *, motif: str = "", exclues: list[int], a_traiter: list[list]):
    """Métadonnées communes des entrées ``lot`` : identifiants et effectifs, jamais de nom."""
    signales_inclus = {i for ids in payload["w"].values() for i in ids} - set(exclues)
    non_concernees = Counter()
    for raison, _statut, ids in payload["nc"]:
        non_concernees[raison] += len(ids)
    meta = {
        "lot_id": payload["lot"],
        "operation": payload["op"],
        "decision": payload["d"],
        "mode": payload["m"],
        "filtres": payload["f"],
        "recherche_appliquee": payload["q"],
        "nb_selectionnees": payload["n"],
        "nb_concernees": len(payload["c"]),
        "non_concernees": dict(non_concernees),
        "avertissements": {k: len(v) for k, v in payload["w"].items()},
        "nb_exclues_avertissement": len(exclues),
        "nb_avertissements_confirmes": len(signales_inclus),
        "candidatures_ids": [c[0] for c in a_traiter],
        "exclues_ids": exclues,
    }
    if payload["op"] == OPERATION_DECISION:
        meta["motif"] = motif
    else:
        meta["nb_deja_decidees"] = len(payload["dd"])
    return meta


def journaliser_lancement(poste, user, request, meta: dict) -> None:
    """Écrit l'entrée ``lot`` de lancement ; refuse un lot déjà exécuté (règle 19).

    Le verrou consultatif PostgreSQL sérialise deux envois simultanés du même jeton : le second
    voit l'entrée du premier et est refusé.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"lot:{meta['lot_id']}"])
        if lot_deja_journalise(meta["lot_id"]):
            raise LotDejaExecute("deja_execute")
        log(
            AuditAction.LOT,
            actor=user,
            obj=poste,
            request=request,
            metadata={**meta, "etape": "lance"},
        )


def journaliser_fin(poste, user, request, meta: dict, rapport: Rapport) -> None:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", [f"lot-fin:{meta['lot_id']}"]
            )
        # Tâche relivrée (acks tardifs) : l'entrée de fin n'est écrite qu'une fois.
        if lot_deja_journalise(meta["lot_id"], "termine"):
            return
        log(
            AuditAction.LOT,
            actor=user,
            obj=poste,
            request=request,
            metadata={**meta, "etape": "termine", **rapport.as_metadata()},
        )


# --------------------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------------------


@dataclass
class Rapport:
    operation: str
    succes: list[int] = field(default_factory=list)
    echecs: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    exclues: list[int] = field(default_factory=list)
    statuts_modifies: list[int] = field(default_factory=list)

    @property
    def nb_echecs(self) -> int:
        return sum(len(v) for v in self.echecs.values())

    def as_metadata(self) -> dict:
        data = {
            "nb_succes": len(self.succes),
            "echecs": {k: len(v) for k, v in self.echecs.items() if v},
            "succes_ids": self.succes,
            "echecs_ids": {k: v for k, v in self.echecs.items() if v},
        }
        if self.operation == OPERATION_RELANCE:
            data["nb_statuts_modifies"] = len(self.statuts_modifies)
        return data


CODES_ECHEC = {
    "concurrence": ECHEC_MODIFIEE,
    "concours_termine": ECHEC_CONCOURS_TERMINE,
    "transition_interdite": ECHEC_TRANSITION,
    "deja_dans_ce_statut": ECHEC_TRANSITION,
    "valeur_interdite": ECHEC_TRANSITION,
    "valeur_interdite_en_lot": ECHEC_TRANSITION,
    "transition_interdite_en_lot": ECHEC_TRANSITION,
}


def partager_signales(payload: dict, choix: dict[str, str]) -> tuple[list[int], set[int]]:
    """Dossiers exclus par l'utilisateur et dossiers signalés inclus (règle 14)."""
    exclues: set[int] = set()
    for type_, ids in payload["w"].items():
        if choix.get(type_) == CHOIX_EXCLURE:
            exclues.update(ids)
    inclus = {i for ids in payload["w"].values() for i in ids} - exclues
    return sorted(exclues), inclus


def _dernier_verdict(candidature_id: int) -> str | None:
    return (
        VerificationEligibilite.objects.filter(candidature_id=candidature_id)
        .order_by("-created_at", "-id")
        .values_list("resultat", flat=True)
        .first()
    )


def executer_decisions(
    payload: dict, *, poste, user, motif: str, choix: dict[str, str], request
) -> Rapport:
    """Enregistre une décision par candidature concernée (règles 20 à 23).

    ``motif`` doit avoir été validé par ``decisions.nettoyer_motif``. Chaque candidature est
    traitée par ``record_decision`` dans sa propre transaction, par identifiant croissant.
    """
    from .services import record_decision

    exclues, inclus = partager_signales(payload, choix)
    a_traiter = [c for c in payload["c"] if c[0] not in set(exclues)]
    meta = meta_lot(payload, motif=motif, exclues=exclues, a_traiter=a_traiter)
    journaliser_lancement(poste, user, request, meta)
    decision = payload["d"]
    signales_par_id: dict[int, list[str]] = defaultdict(list)
    for type_, ids in payload["w"].items():
        for i in ids:
            signales_par_id[i].append(type_)
    rapport = Rapport(OPERATION_DECISION, exclues=exclues)
    for pk, statut, derniere in sorted(a_traiter, key=lambda c: c[0]):
        try:
            if (
                decision == S.ELIGIBLE
                and decisions.AVERTISSEMENT_VERDICT not in signales_par_id[pk]
                and _dernier_verdict(pk) != Verdict.ELIGIBLE
            ):
                raise ConfirmationRequise(avertissements=[decisions.AVERTISSEMENT_VERDICT])
            record_decision(
                Candidature(pk=pk),
                user,
                decision,
                motif,
                request,
                statut_attendu=statut,
                derniere_decision_attendue=derniere,
                confirmer_revision=False,
                confirmer_avertissements=pk in inclus,
                lot_id=payload["lot"],
                avertissements_lot=signales_par_id[pk] if pk in inclus else (),
            )
        except ConfirmationRequise:
            rapport.echecs[ECHEC_AVERTISSEMENT].append(pk)
        except DecisionError as exc:
            rapport.echecs[CODES_ECHEC.get(exc.code, ECHEC_TECHNIQUE)].append(pk)
        except Candidature.DoesNotExist:
            rapport.echecs[ECHEC_MODIFIEE].append(pk)
        except Exception:  # noqa: BLE001 — une erreur n'interrompt jamais le lot (règle 20)
            logger.exception("Lot %s : erreur technique sur la candidature #%s", payload["lot"], pk)
            rapport.echecs[ECHEC_TECHNIQUE].append(pk)
        else:
            rapport.succes.append(pk)
    journaliser_fin(poste, user, request, meta, rapport)
    return rapport


def relancer(ids: list[int], *, poste_id: int, lot_id: str, user, request) -> Rapport:
    """Relance le moteur pour chaque candidature (règle 24), une transaction chacune."""
    from .services import ContexteLot, run_eligibility

    contexte = ContexteLot(lot_id=lot_id, acteur=user, request=request)
    rapport = Rapport(OPERATION_RELANCE)
    concours_id = PosteConcours.objects.filter(pk=poste_id).values_list("concours_id", flat=True)[0]
    for pk in sorted(ids):
        try:
            if Concours.objects.filter(pk=concours_id, statut=Concours.Statut.TERMINE).exists():
                rapport.echecs[ECHEC_CONCOURS_TERMINE].append(pk)
                continue
            candidature = Candidature.objects.select_related("poste_concours", "offre").get(
                pk=pk, poste_concours_id=poste_id
            )
            verification = run_eligibility(candidature, lot=contexte)
        except Candidature.DoesNotExist:
            rapport.echecs[ECHEC_MODIFIEE].append(pk)
        except Exception:  # noqa: BLE001 — une erreur n'interrompt jamais le lot (règle 20)
            logger.exception("Lot %s : erreur technique sur la candidature #%s", lot_id, pk)
            rapport.echecs[ECHEC_TECHNIQUE].append(pk)
        else:
            rapport.succes.append(pk)
            if verification.statut_modifie:
                rapport.statuts_modifies.append(pk)
    return rapport


@dataclass
class ResultatRelance:
    rapport: Rapport | None
    asynchrone: bool
    nb: int


def executer_relance(payload: dict, *, poste, user, request) -> ResultatRelance:
    """Relance synchrone jusqu'au seuil, tâche Celery au-delà (règle 25)."""
    a_traiter = payload["c"]
    meta = meta_lot(payload, exclues=[], a_traiter=a_traiter)
    journaliser_lancement(poste, user, request, meta)
    ids = [c[0] for c in a_traiter]
    if len(ids) > seuil_synchrone():
        from .tasks import relancer_eligibilite_lot

        relancer_eligibilite_lot.delay(
            lot_id=payload["lot"],
            user_id=user.pk,
            poste_id=poste.pk,
            candidature_ids=ids,
            meta=meta,
            ip=meta_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255] if request else "",
        )
        return ResultatRelance(None, True, len(ids))
    rapport = relancer(ids, poste_id=poste.pk, lot_id=payload["lot"], user=user, request=request)
    journaliser_fin(poste, user, request, meta, rapport)
    return ResultatRelance(rapport, False, len(ids))


def meta_ip(request) -> str | None:
    from apps.audit.services import client_ip

    return client_ip(request)


def relancer_en_tache(
    *, lot_id: str, user_id: int, poste_id: int, candidature_ids, meta, ip, user_agent
) -> Rapport:
    """Corps de la tâche Celery : agit au nom de l'utilisateur, ne crée jamais de décision."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(pk=user_id)
    poste = PosteConcours.objects.get(pk=poste_id)
    request = _requete_differee(user, ip, user_agent)
    rapport = relancer(
        list(candidature_ids), poste_id=poste_id, lot_id=lot_id, user=user, request=request
    )
    journaliser_fin(poste, user, request, meta, rapport)
    return rapport
