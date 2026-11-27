"""Lecture des pièces par l'IA (spec KAN-41, règles 1 à 19 et 25).

- :func:`mettre_en_file` : crée une extraction « en attente » par pièce à lire et met une tâche
  Celery en file par pièce, après validation de la transaction ;
- :func:`executer_lecture` : exécutée par la tâche ; lit la pièce via ``SiteSource``, choisit la
  méthode (PDF texte ou vision), appelle l'IA (sortie validée par schéma, appel journalisé dans
  ``AppelIA``), complète l'extraction, journalise, puis relance l'éligibilité quand la dernière
  lecture de la candidature est terminée.

Le contenu des pièces n'est jamais écrit sur disque ni en cache ; seul le texte extrait et les
données structurées sont conservés.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ai.client import AIClient, AIError, PieceJointe, ia_configuree
from ai.prompts import Prompt, load_prompt
from apps.audit.models import AuditAction
from apps.audit.services import log
from apps.recruitment import pieces as pieces_service
from apps.recruitment.models import Candidature, Piece

from .models import Extraction
from .services import SCHEMAS, TYPES_LUS

logger = logging.getLogger(__name__)

#: Prompt versionné de chaque type de pièce (règle 7). Changer de version = relire (règle 13).
PROMPTS: dict[str, tuple[str, str]] = {
    Piece.Type.CV: ("lecture_cv", "1"),
    Piece.Type.DIPLOME: ("lecture_diplome", "1"),
    Piece.Type.ATTESTATION_TRAVAIL: ("lecture_attestation", "1"),
}
#: Limite de l'API pour une image (règle 6).
IMAGE_MAX_BYTES = 5 * 1024 * 1024

ACTEUR_SYNC = "système (lecture après synchronisation)"
ACTEUR_TACHE = "système (lecture des pièces)"

# Motifs d'échec ou de lecture ignorée, affichés sur la fiche.
MOTIFS = {
    "ia_non_configuree": _("IA non configurée"),
    "refus": _("Lecture refusée par l’IA"),
    "sortie_invalide": _("Réponse de l’IA invalide"),
    "ia_indisponible": _("IA momentanément indisponible"),
    "erreur_ia": _("Erreur lors de l’appel à l’IA"),
    "introuvable": _("Fichier introuvable à la source"),
    "source_indisponible": _("Source des pièces momentanément indisponible"),
    "vide": _("Le fichier reçu est vide"),
    "trop_volumineuse": _("Pièce trop volumineuse pour la lecture IA : vérification humaine"),
    "image_trop_volumineuse": _("Image de plus de 5 Mo : vérification humaine"),
    "trop_de_pages": _("PDF trop long pour la lecture IA : vérification humaine"),
    "texte_trop_long": _("Texte trop long pour la lecture IA : vérification humaine"),
    "type_non_pris_en_charge": _("Format non pris en charge (PDF, PNG ou JPEG attendu)"),
    "pdf_illisible": _("PDF illisible (protégé ou endommagé)"),
    "vision_non_autorisee": _(
        "Scan : lecture IA désactivée (pseudonymisation impossible) — vérification humaine"
    ),
    "empreinte_differente": _(
        "Contenu modifié à la source (empreinte différente) : anomalie enregistrée"
    ),
    "file_indisponible": _("File de traitement indisponible"),
    "lecture_interrompue": _("Lecture interrompue (délai dépassé)"),
}


class LectureIndisponible(Exception):
    """La lecture ne peut pas être lancée (IA non configurée, candidature spontanée)."""

    def __init__(self, code: str, message):
        super().__init__(str(message))
        self.code = code
        self.message = message


def get_client() -> AIClient:
    """Client IA de la lecture (remplacé par un client simulé dans les tests)."""
    return AIClient()


def prompt_pour(piece_type: str) -> Prompt:
    name, version = PROMPTS[piece_type]
    return load_prompt(name, version)


def lisible(piece: Piece) -> bool:
    return piece.type in TYPES_LUS


# --------------------------------------------------------------------------------------
# Mise en file (règles 12, 13, 17, 18, 19)
# --------------------------------------------------------------------------------------


@dataclass
class RapportMiseEnFile:
    en_file: list[int] = field(default_factory=list)  # identifiants des pièces
    deja_lues: list[int] = field(default_factory=list)
    en_cours: list[int] = field(default_factory=list)

    def as_metadata(self) -> dict:
        return {
            "pieces_en_file": self.en_file,
            "pieces_deja_lues": self.deja_lues,
            "pieces_en_cours": self.en_cours,
        }


def _limite_attente():
    return timezone.now() - timedelta(minutes=settings.AI_LECTURE_ATTENTE_MAX_MINUTES)


def _deja_lue(piece: Piece, prompt: Prompt) -> bool:
    """Lecture terminée pour l'empreinte enregistrée et la version de prompt courante."""
    sha = (piece.sha256 or "").strip().lower()
    return (
        bool(sha)
        and piece.extractions.filter(
            statut=Extraction.Statut.TERMINEE,
            empreinte=sha,
            prompt_nom=prompt.name,
            prompt_version=prompt.version,
        ).exists()
    )


def _envoyer(extraction_ids: list[int]) -> None:
    from .tasks import lire_piece

    for pk in extraction_ids:
        try:
            lire_piece.delay(pk)
        except Exception:  # noqa: BLE001 — broker indisponible : échec visible (règle 19)
            logger.exception("Extraction #%s : mise en file impossible", pk)
            Extraction.objects.filter(pk=pk, statut=Extraction.Statut.EN_ATTENTE).update(
                statut=Extraction.Statut.ECHEC,
                code="file_indisponible",
                terminee_le=timezone.now(),
            )


def mettre_en_file(
    pieces: list[Piece],
    *,
    force: bool = False,
    user=None,
    origine: str = Extraction.Origine.FICHE,
) -> RapportMiseEnFile:
    """Met en file la lecture des pièces lisibles (une tâche par pièce).

    Sans ``force``, une pièce déjà lue (même empreinte, même version de prompt) n'est pas remise
    en file ; une pièce dont la lecture est en cours (moins de 30 min) ne l'est jamais. Les tâches
    partent après validation de la transaction courante.
    """
    if not ia_configuree():
        raise LectureIndisponible("ia_non_configuree", MOTIFS["ia_non_configuree"])
    rapport = RapportMiseEnFile()
    nouvelles: list[int] = []
    limite = _limite_attente()
    for piece in pieces:
        if not lisible(piece):
            continue
        prompt = prompt_pour(piece.type)
        with transaction.atomic():
            Piece.objects.select_for_update().only("pk").get(pk=piece.pk)
            attente = piece.extractions.filter(statut=Extraction.Statut.EN_ATTENTE)
            if attente.filter(created_at__gte=limite).exists():
                rapport.en_cours.append(piece.pk)
                continue
            # Lecture « en attente » trop ancienne : considérée comme perdue (règle 12).
            attente.update(
                statut=Extraction.Statut.ECHEC,
                code="lecture_interrompue",
                terminee_le=timezone.now(),
            )
            if not force and _deja_lue(piece, prompt):
                rapport.deja_lues.append(piece.pk)
                continue
            extraction = Extraction.objects.create(
                piece=piece,
                type_piece=piece.type,
                prompt_nom=prompt.name,
                prompt_version=prompt.version,
                forcee=force,
                origine=origine,
                declenchee_par=user if getattr(user, "is_authenticated", False) else None,
            )
        nouvelles.append(extraction.pk)
        rapport.en_file.append(piece.pk)
    if nouvelles:
        transaction.on_commit(lambda: _envoyer(nouvelles))
    return rapport


def lancer_pour_candidature(candidature: Candidature, *, force: bool, user) -> RapportMiseEnFile:
    """Bouton « Lire les pièces » / « Relire » de la fiche (règle 17)."""
    if candidature.est_spontanee:
        raise LectureIndisponible(
            "spontanee", _("Lecture des pièces non disponible pour une candidature spontanée.")
        )
    pieces = list(candidature.pieces.filter(type__in=TYPES_LUS).order_by("type", "id"))
    return mettre_en_file(pieces, force=force, user=user)


def lancer_apres_synchronisation(piece_ids: list[int], *, actor=None) -> RapportMiseEnFile | None:
    """Lecture des pièces créées ou modifiées par une synchronisation (règle 18)."""
    if not (piece_ids and settings.AI_LECTURE_APRES_SYNC and ia_configuree()):
        return None
    pieces = list(
        Piece.objects.filter(pk__in=piece_ids, type__in=TYPES_LUS)
        .exclude(candidature__type=Candidature.Type.SPONTANEE)
        .order_by("pk")
    )
    return mettre_en_file(pieces, user=actor, origine=Extraction.Origine.SYNCHRONISATION)


# --------------------------------------------------------------------------------------
# Exécution d'une lecture (règles 2 à 11, 13 à 15, 25)
# --------------------------------------------------------------------------------------


class _Arret(Exception):
    """Fin anticipée d'une lecture : ``statut`` (échec ou ignorée) et motif."""

    def __init__(self, statut: str, code: str, erreur: str = ""):
        super().__init__(code)
        self.statut = statut
        self.code = code
        self.erreur = erreur


def _ignoree(code: str) -> _Arret:
    return _Arret(Extraction.Statut.IGNOREE, code)


def _echec(code: str, erreur: str = "") -> _Arret:
    return _Arret(Extraction.Statut.ECHEC, code, erreur)


_BLANCS = re.compile(r"\s+")


def texte_pdf(content: bytes) -> tuple[str, int]:
    """Texte extrait d'un PDF (``pypdf``) et nombre de pages."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise _echec("pdf_illisible")
        pages = len(reader.pages)
        texte = "\n".join((page.extract_text() or "") for page in reader.pages)
    except _Arret:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise _echec("pdf_illisible", type(exc).__name__) from exc
    return texte.strip(), pages


def _noms_connus(piece: Piece) -> tuple[list[str], dict[str, list[str]]]:
    """Noms et date de naissance du candidat, masqués avant envoi (pseudonymisation)."""
    cand = piece.candidature.candidat
    noms = [cand.nom, cand.prenom]
    noms += [m for n in (cand.nom, cand.prenom) for m in re.split(r"[\s-]+", n or "") if len(m) > 2]
    valeurs: dict[str, list[str]] = {}
    if cand.date_naissance:
        d = cand.date_naissance
        valeurs["NAISSANCE"] = [
            f"{d:%d/%m/%Y}",
            f"{d:%d-%m-%Y}",
            f"{d:%d.%m.%Y}",
            d.isoformat(),
        ]
    return noms, valeurs


def _lire_contenu(piece: Piece) -> bytes:
    from integrations.site_source.base import get_source

    max_bytes = settings.AI_PIECE_MAX_BYTES
    if piece.taille is not None and piece.taille > max_bytes:
        raise _ignoree("trop_volumineuse")
    try:
        content = get_source().read_piece(
            piece.storage_key, max_bytes=max_bytes, timeout=settings.PIECE_SOURCE_TIMEOUT
        )
    except FileNotFoundError as exc:
        raise _echec("introuvable") from exc
    except Exception as exc:  # noqa: BLE001 — toute erreur de la source
        raise _echec("source_indisponible", type(exc).__name__) from exc
    if not content:
        raise _echec("vide")
    if len(content) > max_bytes:
        raise _ignoree("trop_volumineuse")
    return content


def _vision_autorisee() -> bool:
    return not settings.AI_PSEUDONYMIZE or settings.AI_VISION_NON_PSEUDONYMISEE


CONSIGNE_TEXTE = (
    "Voici le texte extrait de la pièce (type : {type}). Extrais les informations demandées.\n"
    "<document>\n{texte}\n</document>"
)
CONSIGNE_VISION = (
    "La pièce jointe (type : {type}) est un document numérisé ou une image. "
    "Extrais les informations demandées."
)


def _preparer(piece: Piece, content: bytes, extraction: Extraction):
    """Méthode, texte à envoyer et pièces jointes (règles 4 à 6)."""
    type_detecte = pieces_service.detect_type(content)
    libelle = piece.get_type_display()
    if type_detecte == pieces_service.PDF:
        texte, pages = texte_pdf(content)
        if len(_BLANCS.sub("", texte)) >= settings.AI_TEXTE_MIN_CARACTERES:
            if len(texte) > settings.AI_TEXTE_MAX_CARACTERES:
                raise _ignoree("texte_trop_long")
            extraction.methode = Extraction.Methode.PDF_TEXTE
            extraction.texte = texte
            return CONSIGNE_TEXTE.format(type=libelle, texte=texte), None
        if pages > settings.AI_PDF_MAX_PAGES:
            raise _ignoree("trop_de_pages")
    elif type_detecte in (pieces_service.PNG, pieces_service.JPEG):
        if len(content) > IMAGE_MAX_BYTES:
            raise _ignoree("image_trop_volumineuse")
    else:
        raise _ignoree("type_non_pris_en_charge")
    extraction.methode = Extraction.Methode.VISION
    if not _vision_autorisee():
        raise _ignoree("vision_non_autorisee")
    jointe: PieceJointe = {"media_type": type_detecte, "data": content}
    return CONSIGNE_VISION.format(type=libelle), [jointe]


def _journaliser(extraction: Extraction, piece: Piece) -> None:
    user = extraction.declenchee_par
    if user is not None:
        actor_repr = ""
    elif extraction.origine == Extraction.Origine.SYNCHRONISATION:
        actor_repr = ACTEUR_SYNC
    else:
        actor_repr = ACTEUR_TACHE
    log(
        AuditAction.AI_CALL,
        actor=user,
        actor_repr=actor_repr,
        obj=extraction,
        object_repr=(
            f"Extraction #{extraction.pk} (Pièce #{piece.pk}, Candidature #{piece.candidature_id})"
        ),
        metadata={
            "operation": "lecture_piece",
            "piece_id": piece.pk,
            "candidature_id": piece.candidature_id,
            "type_piece": piece.type,
            "statut": extraction.statut,
            "methode": extraction.methode,
            "code": extraction.code,
            "appel_ia_id": extraction.appel_ia_id,
            "modele": extraction.modele,
            "prompt": extraction.prompt_nom,
            "prompt_version": extraction.prompt_version,
            "confiance": extraction.confiance,
            "forcee": extraction.forcee,
            "origine": extraction.origine,
        },
    )


def executer_lecture(extraction_id: int, *, client: AIClient | None = None) -> Extraction | None:
    """Exécute une lecture « en attente ». Renvoie l'extraction complétée, l'extraction déjà
    terminée qui la rend inutile (idempotence), ou ``None`` si elle n'est plus en attente."""
    extraction = (
        Extraction.objects.select_related("piece__candidature__candidat", "declenchee_par")
        .filter(pk=extraction_id, statut=Extraction.Statut.EN_ATTENTE)
        .first()
    )
    if extraction is None:  # relivraison de la tâche, ou lecture déjà close
        return None
    piece = extraction.piece
    resultat: Extraction | None = extraction
    try:
        if piece.type not in SCHEMAS:
            raise _ignoree("type_non_pris_en_charge")
        prompt = prompt_pour(piece.type)
        extraction.type_piece = piece.type
        extraction.prompt_nom, extraction.prompt_version = prompt.name, prompt.version
        if not ia_configuree():
            raise _echec("ia_non_configuree")
        content = _lire_contenu(piece)
        controle = pieces_service.controler_empreinte(piece, content)
        extraction.empreinte = pieces_service.empreinte(content)
        if controle.statut == "differente":
            pieces_service.signaler_empreinte_differente(piece, controle, extraction.declenchee_par)
            raise _echec("empreinte_differente")
        if not extraction.forcee:
            existante = (
                piece.extractions.filter(
                    statut=Extraction.Statut.TERMINEE,
                    empreinte=extraction.empreinte,
                    prompt_nom=prompt.name,
                    prompt_version=prompt.version,
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if existante is not None:
                # Déjà lue pour ce contenu et ce prompt (règle 13) : aucun appel.
                extraction.delete()
                resultat = existante
                raise _Arret("", "deja_lue")
        user_text, jointes = _preparer(piece, content, extraction)
        del content
        client = client or get_client()
        noms, valeurs = _noms_connus(piece)
        extraction.modele = client.model
        try:
            data, appel = client.structured(
                task=prompt.name,
                prompt=prompt,
                user_text=user_text,
                schema=SCHEMAS[piece.type],
                known_names=noms,
                known_values=valeurs,
                attachments=jointes,
            )
        except AIError as exc:
            extraction.appel_ia = exc.appel
            raise _echec(exc.code, str(exc)[:500]) from exc
        extraction.appel_ia = appel
        extraction.donnees = data.model_dump(mode="json")
        extraction.confiance = data.confiance
        extraction.statut = Extraction.Statut.TERMINEE
    except _Arret as arret:
        if arret.code == "deja_lue":
            _reevaluer_si_termine(piece.candidature_id)
            return resultat
        extraction.statut = arret.statut
        extraction.code = arret.code
        extraction.erreur = arret.erreur
    extraction.terminee_le = timezone.now()
    extraction.save()
    _journaliser(extraction, piece)
    _reevaluer_si_termine(piece.candidature_id)
    return extraction


def _reevaluer_si_termine(candidature_id: int) -> None:
    """Relance l'éligibilité quand la dernière lecture en attente est terminée (règle 25).

    Seulement si une lecture s'est terminée depuis la dernière vérification ; jamais pour une
    candidature spontanée ; ``run_eligibility`` ne reclasse jamais une candidature décidée.
    """
    from apps.evaluation.models import VerificationEligibilite
    from apps.evaluation.services import run_eligibility

    with transaction.atomic():
        candidature = (
            Candidature.objects.select_for_update(of=("self",))
            .select_related("poste_concours__concours", "offre", "candidat")
            .filter(pk=candidature_id)
            .first()
        )
        if candidature is None or candidature.est_spontanee or candidature.cible is None:
            return
        lectures = Extraction.objects.filter(piece__candidature_id=candidature_id)
        if lectures.filter(statut=Extraction.Statut.EN_ATTENTE).exists():
            return
        derniere = (
            VerificationEligibilite.objects.filter(candidature_id=candidature_id)
            .order_by("-created_at", "-id")
            .values_list("created_at", flat=True)
            .first()
        )
        terminees = lectures.filter(statut=Extraction.Statut.TERMINEE)
        if derniere is not None:
            terminees = terminees.filter(terminee_le__gt=derniere)
        if terminees.exists():
            run_eligibility(candidature)


# --------------------------------------------------------------------------------------
# État de lecture d'une pièce (affichage, règles 27 et 28)
# --------------------------------------------------------------------------------------


@dataclass
class EtatLecture:
    piece: Piece
    etat: str  # non_lue | en_cours | lue | obsolete | echec | ignoree
    courante: Extraction | None = None
    derniere: Extraction | None = None  # dernier essai, s'il est plus récent que la courante
    motif: str = ""


def etats_lecture(pieces: list[Piece]) -> list[EtatLecture]:
    """État de lecture de chaque pièce lisible (``pieces`` : extractions pré-chargées ou non)."""
    from .services import est_courante

    limite = _limite_attente()
    out = []
    for piece in pieces:
        if not lisible(piece):
            continue
        extractions = sorted(
            piece.extractions.all(), key=lambda e: (e.created_at, e.pk), reverse=True
        )
        courante = next((e for e in extractions if est_courante(e, piece)), None)
        en_attente = next(
            (
                e
                for e in extractions
                if e.statut == Extraction.Statut.EN_ATTENTE and e.created_at >= limite
            ),
            None,
        )
        closes = [e for e in extractions if e.statut != Extraction.Statut.EN_ATTENTE]
        derniere = closes[0] if closes else None
        if en_attente is not None:
            etat = "en_cours"
        elif courante is not None:
            etat = "lue"
        elif derniere is not None and derniere.statut == Extraction.Statut.TERMINEE:
            etat = "obsolete"
        elif derniere is not None:
            etat = derniere.statut  # echec | ignoree
        else:
            etat = "non_lue"
        if derniere is courante or (
            courante is not None
            and derniere is not None
            and derniere.created_at < courante.created_at
        ):
            derniere = None
        motif = str(MOTIFS.get(derniere.code, derniere.code)) if derniere and derniere.code else ""
        out.append(EtatLecture(piece, etat, courante, derniere, motif))
    return out
