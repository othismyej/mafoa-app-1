"""Pseudonymisation avant envoi à l'IA (loi 09-08 / CNDP).

Remplace nom, prénom, CIN, téléphone et email par des jetons stables. La table de
correspondance reste côté serveur et permet de ré-identifier la réponse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Téléphones marocains : 06/07/05 ou +212 / 00212, avec séparateurs éventuels.
PHONE_RE = re.compile(r"(?:\+212|00212|0)\s?[5-7](?:[\s.-]?\d{2}){4}")
# CIN marocaine : 1 ou 2 lettres suivies de 3 à 6 chiffres.
CIN_RE = re.compile(r"\b[A-Z]{1,2}\d{3,6}\b")


@dataclass
class Pseudonymizer:
    mapping: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def _token(self, kind: str, value: str) -> str:
        for token, original in self.mapping.items():
            if original == value and token.startswith(f"[{kind}_"):
                return token
        self._counters[kind] = self._counters.get(kind, 0) + 1
        token = f"[{kind}_{self._counters[kind]}]"
        self.mapping[token] = value
        return token

    def pseudonymize(self, text: str, known_names: list[str] | None = None) -> str:
        text = EMAIL_RE.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = PHONE_RE.sub(lambda m: self._token("TEL", m.group(0)), text)
        text = CIN_RE.sub(lambda m: self._token("CIN", m.group(0)), text)
        # Les noms en dernier : sinon ils casseraient la détection des emails.
        names = sorted({n for n in (known_names or []) if n and len(n) > 1}, key=len, reverse=True)
        for name in names:
            pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
            text = pattern.sub(lambda m: self._token("NOM", m.group(0)), text)
        return text

    def restore(self, text: str) -> str:
        for token, original in self.mapping.items():
            text = text.replace(token, original)
        return text
