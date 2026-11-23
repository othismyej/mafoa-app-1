"""Écran « Journal d'audit » et panneau des consultations d'un dossier (spec KAN-14).

Réservé au rôle ``admin`` ; ``GET``/``HEAD`` uniquement (405 sinon). Chaque refus est
journalisé (``access_denied``) et la 403 est renvoyée, non levée (``ATOMIC_REQUESTS``).
Chaque consultation du journal est elle-même journalisée (``vue = journal_*``).
"""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from apps.accounts.models import User
from apps.accounts.permissions import ADMIN_ROLES
from apps.recruitment.models import Candidature, Piece

from . import exports
from .filters import (
    ACTEUR_AUCUN,
    OBJECT_TYPES,
    PAGE_SIZE,
    PERIODE_MAX_JOURS,
    content_type_for,
    filter_journal,
    parse_filters,
)
from .filters import (
    consultations_dossier as consultations_qs,
)
from .models import AuditAction, JournalAudit
from .services import log, log_consultation, role_required_logged

JOURNAL_REPR = "Journal d'audit"
PANNEAU_TAILLE = 20
FORMATS = ("csv", "xlsx")


def _admin_only(**kwargs):
    return role_required_logged(*ADMIN_ROLES, **kwargs)


def _users():
    return list(User.objects.order_by("email").only("id", "email", "is_active"))


def _object_links(entries) -> dict[int, str]:
    """Lien vers la fiche candidature pour les entrées dont l'objet existe encore.

    Deux requêtes au plus, quel que soit le nombre d'entrées (règle 18).
    """
    ct_cand = content_type_for("candidature").pk
    ct_piece = content_type_for("piece").pk
    cand_ids, piece_ids = set(), set()
    for e in entries:
        if e.object_id.isdigit():
            if e.content_type_id == ct_cand:
                cand_ids.add(int(e.object_id))
            elif e.content_type_id == ct_piece:
                piece_ids.add(int(e.object_id))
    existing = (
        set(Candidature.objects.filter(pk__in=cand_ids).values_list("pk", flat=True))
        if cand_ids
        else set()
    )
    pieces = (
        dict(Piece.objects.filter(pk__in=piece_ids).values_list("pk", "candidature_id"))
        if piece_ids
        else {}
    )
    links = {}
    for e in entries:
        if not e.object_id.isdigit():
            continue
        oid = int(e.object_id)
        if e.content_type_id == ct_cand and oid in existing:
            links[e.pk] = reverse("recruitment:candidature_detail", args=[oid])
        elif e.content_type_id == ct_piece and oid in pieces:
            links[e.pk] = reverse("recruitment:candidature_detail", args=[pieces[oid]])
    return links


def _acteur(entry: JournalAudit) -> str:
    label = entry.actor_repr or (_("système") if entry.actor_id is None else "")
    if entry.actor_id is not None and not entry.actor.is_active:
        label = f"{label} {_('(désactivé)')}"
    return label


def _rows(entries) -> list[dict]:
    links = _object_links(entries)
    return [{"e": e, "acteur": _acteur(e), "lien": links.get(e.pk)} for e in entries]


@require_safe
@_admin_only(object_repr=JOURNAL_REPR)
def journal_liste(request):
    """Liste filtrable du journal (règles 10 à 12, 15)."""
    users = _users()
    flt = parse_filters(request.GET, users)
    qs = filter_journal(flt).select_related("actor", "content_type")
    page = Paginator(qs, PAGE_SIZE).get_page(flt.page)
    rows = _rows(list(page.object_list))
    log_consultation(
        request,
        None,
        "journal_liste",
        object_repr=JOURNAL_REPR,
        filtres=flt.as_metadata(),
        page=page.number,
        nb_resultats=page.paginator.count,
    )
    context = {
        "flt": flt,
        "page": page,
        "rows": rows,
        "users": users,
        "acteur_aucun": ACTEUR_AUCUN,
        "action_choices": AuditAction.choices,
        "type_choices": [(k, v[0]) for k, v in OBJECT_TYPES.items()],
        "querystring": flt.querystring(),
    }
    fragment = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    template = "audit/_journal_resultats.html" if fragment else "audit/journal_liste.html"
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request"])
    return response


def _display(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _diff(before, after) -> list[dict]:
    """Avant / après côte à côte, une ligne par clé ; ``modifie`` si la valeur diffère."""
    before = before if isinstance(before, dict) else ({} if before is None else {"": before})
    after = after if isinstance(after, dict) else ({} if after is None else {"": after})
    keys = sorted(set(before) | set(after))
    missing = object()
    rows = []
    for key in keys:
        b, a = before.get(key, missing), after.get(key, missing)
        rows.append(
            {
                "cle": key,
                "avant": "—" if b is missing else _display(b),
                "apres": "—" if a is missing else _display(a),
                "modifie": b != a,
            }
        )
    return rows


@require_safe
@_admin_only(object_repr=lambda kw: f"Entrée du journal #{kw.get('pk')}")
def journal_detail(request, pk: int):
    """Détail d'une entrée (règle 13)."""
    entry = get_object_or_404(JournalAudit.objects.select_related("actor", "content_type"), pk=pk)
    links = _object_links([entry])
    context = {
        "e": entry,
        "acteur": _acteur(entry),
        "lien": links.get(entry.pk),
        "metadata": [(k, _display(v)) for k, v in sorted((entry.metadata or {}).items())],
        "diff": _diff(entry.before, entry.after),
        "a_avant_apres": entry.before is not None or entry.after is not None,
        "action_label": dict(AuditAction.choices).get(entry.action, entry.action),
    }
    log_consultation(request, entry, "journal_detail")
    return render(request, "audit/journal_detail.html", context)


@require_safe
@_admin_only(object_repr=JOURNAL_REPR)
def journal_export(request, fmt: str):
    """Export CSV ou Excel des entrées filtrées (règle 17)."""
    if fmt not in FORMATS:
        raise Http404
    flt = parse_filters(request.GET, _users())
    # Instantané : les entrées écrites pendant l'export (dont la sienne) n'y figurent pas.
    last_id = JournalAudit.objects.aggregate(m=Max("id"))["m"] or 0
    qs = filter_journal(flt).filter(id__lte=last_id)
    total = qs.count()
    plafond = settings.AUDIT_EXPORT_MAX_ROWS
    if total > plafond:
        messages.warning(
            request,
            _(
                "L’export est limité à %(max)s lignes (%(n)s entrées correspondent) : "
                "restreignez les filtres."
            )
            % {"max": f"{plafond:,}".replace(",", " "), "n": f"{total:,}".replace(",", " ")},
        )
        return redirect(f"{reverse('audit:journal_liste')}?{flt.querystring()}")
    log(
        AuditAction.EXPORT,
        request=request,
        object_repr=JOURNAL_REPR,
        metadata={
            "vue": "journal_export",
            "format": fmt,
            "filtres": flt.as_metadata(),
            "nb_lignes": total,
        },
    )
    filename = f"journal-audit-{timezone.localtime():%Y%m%d-%H%M}.{fmt}"
    if fmt == "csv":
        response = StreamingHttpResponse(
            exports.csv_stream(qs), content_type="text/csv; charset=utf-8"
        )
    else:
        response = HttpResponse(
            exports.xlsx_bytes(qs),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _nature(entry: JournalAudit) -> str:
    meta = entry.metadata or {}
    vue = meta.get("vue")
    if vue == "piece":
        mode = {"apercu": _("aperçu"), "telechargement": _("téléchargement")}.get(
            meta.get("mode"), meta.get("mode") or "—"
        )
        return _("Pièce #%(id)s (%(mode)s)") % {"id": meta.get("piece_id", "?"), "mode": mode}
    if vue in ("admin_detail", "admin_liste"):
        return _("Administration")
    if vue == "fragment":
        return _("Fragment")
    return _("Fiche")


def _lien_journal_dossier(pk: int) -> str:
    """Journal filtré sur le dossier, sur l'étendue maximale (366 jours jusqu'à aujourd'hui)."""
    today = timezone.localdate()
    du = today - timedelta(days=PERIODE_MAX_JOURS - 1)
    query = urlencode({"du": du.isoformat(), "au": today.isoformat(), "dossier": pk})
    return f"{reverse('audit:journal_liste')}?{query}"


@require_safe
@_admin_only(candidature_kwarg="pk")
def consultations_dossier(request, pk: int):
    """Fragment « Consultations de ce dossier » sur la fiche (règle 16)."""
    candidature = get_object_or_404(Candidature.objects.only("id", "candidat_id"), pk=pk)
    qs = consultations_qs(pk)
    stats = qs.aggregate(total=Count("id"), personnes=Count("actor", distinct=True))
    recentes = list(qs.select_related("actor")[:PANNEAU_TAILLE])
    log_consultation(request, candidature, "journal_dossier")
    context = {
        "c": candidature,
        "total": stats["total"],
        "personnes": stats["personnes"],
        "rows": [{"e": e, "acteur": _acteur(e), "nature": _nature(e)} for e in recentes],
        "lien_journal": _lien_journal_dossier(pk),
    }
    return render(request, "audit/_consultations_dossier.html", context)
