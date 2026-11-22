"""Prompts versionnés. Chaque prompt est un fichier ``<nom>.v<N>.md`` dans ce dossier."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


def load_prompt(name: str, version: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.v{version}.md"
    return Prompt(name=name, version=version, text=path.read_text(encoding="utf-8"))
