"""Wrapper de l'API Claude : sortie JSON validée (Pydantic), pseudonymisation, traçabilité.

Chaque appel est enregistré dans ``AppelIA`` (modèle, version du prompt, entrée
pseudonymisée, sortie, durée). Dans les tests, injecter un faux ``transport``
(aucun appel réseau).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

from django.conf import settings
from pydantic import BaseModel

from .models import AppelIA
from .prompts import Prompt
from .pseudonymize import Pseudonymizer

T = TypeVar("T", bound=BaseModel)

# transport(model, system, user_text, schema) -> (instance validée, usage dict)
Transport = Callable[[str, str, str, type[BaseModel]], tuple[BaseModel, dict[str, Any]]]


class AIError(Exception):
    pass


class AIRefusal(AIError):
    pass


def anthropic_transport(
    model: str, system: str, user_text: str, schema: type[BaseModel]
) -> tuple[BaseModel, dict[str, Any]]:
    """Appel réel via le SDK ``anthropic`` (sorties structurées validées par Pydantic)."""
    import anthropic

    if not settings.ANTHROPIC_API_KEY:
        raise AIError("ANTHROPIC_API_KEY n'est pas configurée.")
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=3)
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_text}],
        output_format=schema,
    )
    if response.stop_reason == "refusal":
        raise AIRefusal("Le modèle a refusé de traiter la demande.")
    if response.parsed_output is None:
        raise AIError(f"Réponse non exploitable (stop_reason={response.stop_reason}).")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return response.parsed_output, usage


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
    ) -> tuple[T, AppelIA]:
        pseudo = Pseudonymizer()
        sent_text = pseudo.pseudonymize(user_text, known_names) if self.pseudonymize else user_text
        started = time.monotonic()
        appel = AppelIA(
            tache=task,
            modele=self.model,
            prompt_nom=prompt.name,
            prompt_version=prompt.version,
            pseudonymise=self.pseudonymize,
            entree={"system": prompt.text, "user": sent_text},
        )
        try:
            result, usage = self.transport(self.model, prompt.text, sent_text, schema)
            result = schema.model_validate(result.model_dump())
        except Exception as exc:
            appel.statut = AppelIA.Statut.ERREUR
            appel.erreur = f"{type(exc).__name__}: {exc}"[:2000]
            appel.duree_ms = int((time.monotonic() - started) * 1000)
            appel.save()
            if isinstance(exc, AIError):
                raise
            raise AIError(str(exc)) from exc

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
