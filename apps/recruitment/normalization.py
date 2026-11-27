"""Clés de rapprochement des candidats (détection de doublons). Code déterministe."""

from __future__ import annotations

import re
from datetime import date

from apps.evaluation.eligibility.levels import normalize_text


def normalize_email(email: str | None) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def normalize_phone(phone: str | None) -> str:
    """Format canonique marocain : 212XXXXXXXXX (sans + ni espaces)."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "212" + digits[1:]
    return digits


def normalize_name(last_name: str | None, first_name: str | None) -> str:
    """Nom + prénom normalisés, ordre des mots indifférent (« EL AMRANI Sara » = « Sara El Amrani »)."""
    tokens = normalize_text(f"{last_name or ''} {first_name or ''}").split()
    return " ".join(sorted(tokens))


def identity_key(last_name: str | None, first_name: str | None, birth_date: date | None) -> str:
    if not birth_date:
        return ""
    return f"{normalize_name(last_name, first_name)}|{birth_date.isoformat()}"


def normalize_cin(cin: str | None) -> str:
    """CIN en majuscules, sans espaces ni ponctuation (KAN-42, Q3 bis révisée).

    Une valeur de moins de 4 caractères est ignorée (vide) : trop courte pour rapprocher.
    """
    value = re.sub(r"[^0-9A-Z]", "", (cin or "").upper())
    return value if len(value) >= 4 else ""
