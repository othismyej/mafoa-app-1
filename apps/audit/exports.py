"""Export du journal d'audit en CSV et Excel (spec KAN-14, règle 17).

Avant / après ne sont jamais exportés (minimisation). Toute cellule susceptible d'être
interprétée comme une formule par un tableur est neutralisée par une apostrophe.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import QuerySet
from django.utils import timezone

from .models import AuditAction, JournalAudit

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CHUNK_SIZE = 2000

HEADERS = [
    "Horodatage",
    "Acteur",
    "Action",
    "Type d'objet",
    "Identifiant d'objet",
    "Objet",
    "Adresse IP",
    "Navigateur",
    "Métadonnées",
]

_ACTION_LABELS = dict(AuditAction.choices)


def neutralize(value: str) -> str:
    """Préfixe d'une apostrophe une valeur qui commence par ``= + - @``, tabulation ou CR."""
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def _neutralize_json(value: Any) -> Any:
    """Neutralise aussi les chaînes à l'intérieur des métadonnées (recopiées dans un tableur)."""
    if isinstance(value, str):
        return neutralize(value)
    if isinstance(value, dict):
        return {k: _neutralize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_neutralize_json(v) for v in value]
    return value


def row_for(entry: JournalAudit) -> list[str]:
    ct = entry.content_type
    metadata = json.dumps(
        _neutralize_json(entry.metadata or {}),
        cls=DjangoJSONEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    values = [
        timezone.localtime(entry.timestamp).isoformat(),
        entry.actor_repr,
        str(_ACTION_LABELS.get(entry.action, entry.action)),
        str(ct.name) if ct is not None else "",
        entry.object_id,
        entry.object_repr,
        entry.ip_address or "",
        entry.user_agent,
        metadata,
    ]
    return [neutralize(v) for v in values]


def _entries(qs: QuerySet) -> Iterator[JournalAudit]:
    return qs.select_related("content_type").iterator(chunk_size=CHUNK_SIZE)


class _Echo:
    def write(self, value):
        return value


def csv_stream(qs: QuerySet) -> Iterable[str]:
    """Lignes CSV (UTF-8 avec BOM, séparateur « ; ») produites au fil de l'eau."""
    writer = csv.writer(_Echo(), delimiter=";")
    yield "﻿" + writer.writerow(HEADERS)
    for entry in _entries(qs):
        yield writer.writerow(row_for(entry))


def xlsx_bytes(qs: QuerySet) -> bytes:
    """Classeur Excel en mode « écriture seule » (lignes écrites par lots, sans tout charger)."""
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Journal d'audit")
    sheet.append(HEADERS)
    for entry in _entries(qs):
        sheet.append(row_for(entry))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
