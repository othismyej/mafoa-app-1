"""Wrapper de l'API Claude : sortie JSON validée (Pydantic), pseudonymisation, traçabilité.

Chaque appel est enregistré dans ``AppelIA`` (modèle, version du prompt, entrée
pseudonymisée, sortie, durée, jetons). Dans les tests, injecter un faux ``transport``
(aucun appel réseau).
"""

from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import Callable
from typing import Any, TypedDict, TypeVar

from django.conf import settings
from pydantic import BaseModel, ValidationError

from .models import AppelIA
from .prompts import Prompt
from .pseudonymize import Pseudonymizer

T = TypeVar("T", bound=BaseModel)


class PieceJointe(TypedDict):
    """Contenu envoyé à la vision : ``media_type`` (``application/pdf``, ``image/png``,
    ``image/jpeg``) et octets bruts. Jamais journalisé (seuls type, taille, empreinte)."""

    media_type: str
    data: bytes


# transport(model, system, user_text, schema[, attachments=[...]]) -> (instance, usage dict)
Transport = Callable[..., tuple[BaseModel, dict[str, Any]]]

# Codes d'erreur (KAN-41) : affichés sur la fiche via ``apps.documents``.
IA_NON_CONFIGUREE = "ia_non_configuree"
REFUS = "refus"
SORTIE_INVALIDE = "sortie_invalide"
INDISPONIBLE = "ia_indisponible"
ERREUR = "erreur_ia"


class AIError(Exception):
    code = ERREUR

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code
        #: Trace de l'appel en erreur (renseignée par ``AIClient.structured``).
        self.appel: AppelIA | None = None


class AIRefusal(AIError):
    code = REFUS


class AINotConfigured(AIError):
    code = IA_NON_CONFIGUREE


def ia_configuree() -> bool:
    """L'IA est-elle utilisable ? (clé présente ; jamais d'appel pour le vérifier)."""
    return bool(settings.ANTHROPIC_API_KEY)


def _content_blocks(user_text: str, attachments: list[PieceJointe] | None) -> list[dict]:
    """Pièces jointes d'abord, consigne ensuite (recommandation de l'API)."""
    blocks: list[dict] = []
    for a in attachments or []:
        kind = "document" if a["media_type"] == "application/pdf" else "image"
        blocks.append(
            {
                "type": kind,
                "source": {
                    "type": "base64",
                    "media_type": a["media_type"],
                    "data": base64.standard_b64encode(a["data"]).decode("ascii"),
                },
            }
        )
    blocks.append({"type": "text", "text": user_text})
    return blocks


def anthropic_transport(
    model: str,
    system: str,
    user_text: str,
    schema: type[BaseModel],
    attachments: list[PieceJointe] | None = None,
) -> tuple[BaseModel, dict[str, Any]]:
    """Appel réel via le SDK ``anthropic`` (sorties structurées validées par Pydantic)."""
    import anthropic

    if not ia_configuree():
        raise AINotConfigured("IA non configurée : ANTHROPIC_API_KEY est vide.")
    client = anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY, max_retries=3, timeout=settings.AI_TIMEOUT
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 16000,
        "system": system,
        "messages": [{"role": "user", "content": _content_blocks(user_text, attachments)}],
        "output_format": schema,
        "output_config": {"effort": settings.AI_EFFORT},
    }
    try:
        if settings.AI_FALLBACKS:
            # Repli côté serveur si le modèle refuse (routage par catégorie de refus).
            response = client.beta.messages.parse(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            response = client.messages.parse(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise AINotConfigured("Clé API refusée par le service.") from exc
    except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
        raise AIError(type(exc).__name__, code=INDISPONIBLE) from exc
    except anthropic.APIStatusError as exc:
        code = INDISPONIBLE if exc.status_code >= 500 else ERREUR
        raise AIError(f"{exc.status_code}: {exc.message}", code=code) from exc
    if response.stop_reason == "refusal":
        raise AIRefusal("Le modèle a refusé de traiter la demande.")
    if response.parsed_output is None:
        raise AIError(
            f"Réponse non exploitable (stop_reason={response.stop_reason}).", code=SORTIE_INVALIDE
        )
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return response.parsed_output, usage


def _description_piece_jointe(a: PieceJointe) -> dict:
    return {
        "media_type": a["media_type"],
        "taille": len(a["data"]),
        "sha256": hashlib.sha256(a["data"]).hexdigest(),
    }


class AIClient:
    def __init__(
        self,
        transport: Transport | None = None,
        model: str | None = None,
        pseudonymize: bool | None = None,
    ):
        self.transport = transport or anthropic_transport
        self.model = model or settings.AI_MODEL
        self.pseudonymize = settings.AI_PSEUDONYMIZE if pseudonymize is None else pseudonymize

    def structured(
        self,
        *,
        task: str,
        prompt: Prompt,
        user_text: str,
        schema: type[T],
        known_names: list[str] | None = None,
        known_values: dict[str, list[str]] | None = None,
        attachments: list[PieceJointe] | None = None,
    ) -> tuple[T, AppelIA]:
        """Appel à sortie structurée. ``attachments`` : contenu envoyé à la vision (non
        pseudonymisable : l'appel est alors tracé ``pseudonymise=False``, sans le contenu)."""
        pseudo = Pseudonymizer()
        sent_text = (
            pseudo.pseudonymize(user_text, known_names, known_values)
            if self.pseudonymize
            else user_text
        )
        entree: dict[str, Any] = {"system": prompt.text, "user": sent_text}
        if attachments:
            entree["pieces_jointes"] = [_description_piece_jointe(a) for a in attachments]
        started = time.monotonic()
        appel = AppelIA(
            tache=task,
            modele=self.model,
            prompt_nom=prompt.name,
            prompt_version=prompt.version,
            pseudonymise=self.pseudonymize and not attachments,
            entree=entree,
        )
        try:
            if attachments:
                result, usage = self.transport(
                    self.model, prompt.text, sent_text, schema, attachments=attachments
                )
            else:
                result, usage = self.transport(self.model, prompt.text, sent_text, schema)
            result = schema.model_validate(result.model_dump())
        except Exception as exc:
            appel.statut = AppelIA.Statut.ERREUR
            appel.erreur = f"{type(exc).__name__}: {exc}"[:2000]
            appel.duree_ms = int((time.monotonic() - started) * 1000)
            appel.save()
            if isinstance(exc, AIError):
                error = exc
            elif isinstance(exc, ValidationError):
                error = AIError("Réponse de l'IA invalide.", code=SORTIE_INVALIDE)
            else:
                error = AIError(str(exc))
            error.appel = appel
            if error is exc:
                raise
            raise error from exc

        appel.statut = AppelIA.Statut.SUCCES
        appel.sortie = result.model_dump(mode="json")
        appel.duree_ms = int((time.monotonic() - started) * 1000)
        appel.tokens_entree = usage.get("input_tokens")
        appel.tokens_sortie = usage.get("output_tokens")
        appel.save()

        if self.pseudonymize and pseudo.mapping:
            restored = pseudo.restore(result.model_dump_json())
            result = schema.model_validate_json(restored)
        return result, appel
