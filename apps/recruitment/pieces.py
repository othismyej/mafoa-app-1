"""Lecture d'une pièce pour la visionneuse (spec KAN-12).

Règles déterministes, sans accès réseau ni disque : détection du type réel sur les octets,
assainissement du nom de fichier, vérification d'empreinte et anomalie associée. Le contenu
d'une pièce n'est jamais conservé (ni disque, ni cache, ni base) : il ne vit que le temps de la
requête.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import log
from apps.evaluation.models import Anomalie

from .models import Piece

PDF = "application/pdf"
PNG = "image/png"
JPEG = "image/jpeg"
OCTET_STREAM = "application/octet-stream"

#: Types affichables en ligne (règle 8, Q5) et extension proposée.
EXTENSIONS = {PDF: ".pdf", PNG: ".png", JPEG: ".jpg"}
#: Extensions acceptées comme correspondant au type détecté (règle 13).
EXTENSIONS_ACCEPTEES = {PDF: {".pdf"}, PNG: {".png"}, JPEG: {".jpg", ".jpeg"}}

NOM_MAX = 150
PDF_SIGNATURE_FENETRE = 1024


def detect_type(content: bytes) -> str | None:
    """Type réel d'après la signature des octets ; ``None`` si non affichable (règle 8)."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if content.startswith(b"\xff\xd8\xff"):
        return JPEG
    if b"%PDF-" in content[:PDF_SIGNATURE_FENETRE]:
        return PDF
    return None


def mode_affichage(content_type_declare: str) -> str:
    """Mode d'aperçu du fragment, d'après le seul type déclaré (règle 9) : ``iframe``,
    ``img`` ou ``aucun``. La vue de contenu reste seule juge de la sécurité."""
    declared = (content_type_declare or "").split(";")[0].strip().lower()
    if declared == PDF:
        return "iframe"
    if declared in (PNG, JPEG):
        return "img"
    return "aucun"


_CONTROLE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def nom_assaini(nom_fichier: str, piece_pk: int, type_detecte: str | None) -> str:
    """Nom proposé au navigateur (règle 13, Q14) : sans chemin, caractère de contrôle ni
    guillemet, 150 caractères au plus, extension alignée sur le type détecté."""
    nom = (nom_fichier or "").replace("/", "").replace("\\", "")
    nom = _CONTROLE.sub("", nom).replace('"', "")
    nom = re.sub(r"\s+", " ", nom).strip().lstrip(".").strip()
    stem, dot, current = nom.rpartition(".")
    if not dot:
        stem, current = nom, ""
    ext = f".{current}" if dot else ""
    if type_detecte in EXTENSIONS and ext.lower() not in EXTENSIONS_ACCEPTEES[type_detecte]:
        ext = EXTENSIONS[type_detecte]
    if len(ext) >= NOM_MAX:  # « extension » aberrante : simple troncature
        stem, ext = (stem + ext)[:NOM_MAX], ""
    stem = stem[: NOM_MAX - len(ext)].strip()
    if not stem:
        return f"piece-{piece_pk}{EXTENSIONS.get(type_detecte or '', '.bin')}"
    return stem + ext


def content_disposition(disposition: str, nom: str) -> str:
    """``inline`` / ``attachment`` avec ``filename`` (ASCII) et ``filename*`` (UTF-8)."""
    ascii_nom = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode("ascii")
    ascii_nom = _CONTROLE.sub("", ascii_nom).replace('"', "").replace("\\", "") or "piece"
    return f"{disposition}; filename=\"{ascii_nom}\"; filename*=UTF-8''{quote(nom, safe='')}"


def empreinte(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class ControleEmpreinte:
    statut: str  # "conforme" | "differente" | "non_verifiee"
    attendue: str = ""
    constatee: str = ""


def controler_empreinte(piece: Piece, content: bytes) -> ControleEmpreinte:
    """Compare l'empreinte du contenu lu à celle enregistrée (règle 15, insensible à la casse).

    ``Piece.sha256`` n'est jamais modifiée ici (Q13)."""
    attendue = (piece.sha256 or "").strip().lower()
    if not attendue:
        return ControleEmpreinte("non_verifiee")
    constatee = empreinte(content)
    if constatee == attendue:
        return ControleEmpreinte("conforme", attendue, constatee)
    return ControleEmpreinte("differente", attendue, constatee)


ACTEUR_CONTROLE = "système (contrôle d’empreinte)"


def _marqueur_constatee(constatee: str) -> str:
    return f"Empreinte constatée : {constatee}."


def signaler_empreinte_differente(
    piece: Piece, controle: ControleEmpreinte, user
) -> Anomalie | None:
    """Enregistre l'anomalie d'empreinte (règle 16), au plus une par empreinte constatée.

    Vérification et création sous verrou de la ligne ``Piece`` (consultations simultanées).
    Renvoie l'anomalie créée, ou ``None`` si elle existait déjà (résolue ou non).
    Aucun effet sur le statut ni sur l'éligibilité de la candidature.
    """
    with transaction.atomic():
        Piece.objects.select_for_update().only("pk").get(pk=piece.pk)
        if Anomalie.objects.filter(
            piece_id=piece.pk,
            type=Anomalie.Type.EMPREINTE_DIFFERENTE,
            description__contains=_marqueur_constatee(controle.constatee),
        ).exists():
            return None
        lu_le = timezone.localtime().strftime("%d/%m/%Y %H:%M")
        anomalie = Anomalie.objects.create(
            candidature_id=piece.candidature_id,
            piece_id=piece.pk,
            type=Anomalie.Type.EMPREINTE_DIFFERENTE,
            gravite=Anomalie.Gravite.HAUTE,
            # Description sans donnée nominative (ni nom, ni nom de fichier, ni clé de stockage).
            description=(
                f"Le contenu lu à la source le {lu_le} ne correspond pas à l’empreinte "
                f"enregistrée. Empreinte attendue : {controle.attendue}. "
                f"{_marqueur_constatee(controle.constatee)}"
            ),
        )
        log(
            AuditAction.CREATE,
            obj=anomalie,
            actor_repr=ACTEUR_CONTROLE,
            object_repr=(
                f"Anomalie #{anomalie.pk} (Pièce #{piece.pk}, Candidature #{piece.candidature_id})"
            ),
            after={"type": anomalie.type, "gravite": anomalie.gravite},
            metadata={
                "piece_id": piece.pk,
                "candidature_id": piece.candidature_id,
                "declenche_par_user_id": getattr(user, "pk", None),
            },
        )
        return anomalie
