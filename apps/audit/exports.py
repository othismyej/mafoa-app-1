"""Export du journal d'audit en CSV et Excel (spec KAN-14, règle 17).

Avant / après et les termes de recherche ne sont jamais exportés (minimisation). Toute cellule susceptible d'être
interprétée comme une formule par un tableur est neutralisée par une apostrophe.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator

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


# Caractères de contrôle refusés par openpyxl (\x00-\x08, \x0b, \x0c, \x0e-\x1f) : un
# User-Agent forgé lors d'une tentative de connexion ne doit pas rendre l'export impossible.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Métadonnées non exportées (minimisation) : une recherche peut contenir un nom ou une CIN.
METADATA_NON_EXPORTEES = ("recherche",)


def neutralize(value: str) -> str:
    """Préfixe d'une apostrophe une cellule qui commence (espaces de tête ignorés) par
    ``= + - @``, tabulation ou CR. Seul le début de cellule compte pour un tableur."""
    return "'" + value if value.lstrip(" ").startswith(FORMULA_PREFIXES) else value


def _clean(value: str) -> str:
    return _ILLEGAL.sub("\ufffd", value)


def row_for(entry: JournalAudit) -> list[str]:
    ct = entry.content_type
    metadata = {k: v for k, v in (entry.metadata or {}).items() if k not in METADATA_NON_EXPORTEES}
    metadata_json = json.dumps(
        metadata,
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
        metadata_json,
    ]
    return [neutralize(_clean(v)) for v in values]


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
