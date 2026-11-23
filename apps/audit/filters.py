"""Filtres de l'écran « Journal d'audit » (spec KAN-14, règle 12).

Une seule logique serveur pour la liste et l'export : :func:`parse_filters` lit les
paramètres d'URL (valeurs invalides ignorées avec un message), :func:`filter_journal`
construit la requête, filtrage et tri en base.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, QuerySet, TextField, Value
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import AuditAction, JournalAudit

PERIODE_DEFAUT_JOURS = 30
PERIODE_MAX_JOURS = 366
PAGE_SIZE = 50
ACTEUR_AUCUN = "aucun"
TYPE_AUCUN = "aucun"

#: Types d'objet proposés au filtre : clé d'URL -> (libellé, app_label, model).
OBJECT_TYPES: dict[str, tuple] = {
    "candidature": (gettext_lazy("Candidature"), "recruitment", "candidature"),
    "piece": (gettext_lazy("Pièce"), "recruitment", "piece"),
    "candidat": (gettext_lazy("Candidat"), "recruitment", "candidat"),
    "concours": (gettext_lazy("Concours"), "recruitment", "concours"),
    "poste": (gettext_lazy("Poste de concours"), "recruitment", "posteconcours"),
    "offre": (gettext_lazy("Offre d’emploi"), "recruitment", "offreemploi"),
    "decision": (gettext_lazy("Décision"), "evaluation", "decision"),
    "utilisateur": (gettext_lazy("Utilisateur"), "accounts", "user"),
    "invitation": (gettext_lazy("Invitation"), "accounts", "invitation"),
    "journal": (gettext_lazy("Entrée du journal"), "audit", "journalaudit"),
    TYPE_AUCUN: (gettext_lazy("Aucun objet"), None, None),
}


def content_type_for(key: str) -> ContentType:
    _label, app_label, model = OBJECT_TYPES[key]
    return ContentType.objects.get_by_natural_key(app_label, model)


@dataclass
class JournalFilters:
    du: date
    au: date
    acteur: str = ""
    actions: list[str] = field(default_factory=list)
    type: str = ""
    dossier: int | None = None
    ip: str = ""
    inclure_journal: bool = False
    page: str = "1"
    messages: list[str] = field(default_factory=list)
    periode_invalide: bool = False

    def params(self) -> list[tuple[str, str]]:
        """Paramètres d'URL des filtres retenus (hors page), dans un ordre stable."""
        items = [("du", self.du.isoformat()), ("au", self.au.isoformat())]
        if self.acteur:
            items.append(("acteur", self.acteur))
        items += [("action", a) for a in self.actions]
        if self.type:
            items.append(("type", self.type))
        if self.dossier is not None:
            items.append(("dossier", str(self.dossier)))
        if self.ip:
            items.append(("ip", self.ip))
        if self.inclure_journal:
            items.append(("journal", "1"))
        return items

    def querystring(self) -> str:
        return urlencode(self.params())

    def as_metadata(self) -> dict:
        """Filtres appliqués, pour le journal (consultation et export)."""
        data: dict = {"du": self.du.isoformat(), "au": self.au.isoformat()}
        if self.acteur:
            data["acteur"] = self.acteur
        if self.actions:
            data["actions"] = list(self.actions)
        if self.type:
            data["type"] = self.type
        if self.dossier is not None:
            data["dossier"] = self.dossier
        if self.ip:
            data["ip"] = self.ip
        if self.inclure_journal:
            data["inclure_journal"] = True
        return data


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def parse_filters(params, users) -> JournalFilters:
    """Lit les filtres de la requête. ``users`` : utilisateurs proposés au filtre acteur."""
    msgs: list[str] = []
    today = timezone.localdate()

    du_raw, au_raw = params.get("du", "").strip(), params.get("au", "").strip()
    du = _parse_date(du_raw) if du_raw else None
    au = _parse_date(au_raw) if au_raw else None
    if du_raw and du is None:
        msgs.append(
            _("La date de début « %(v)s » est invalide : elle est ignorée.") % {"v": du_raw}
        )
    if au_raw and au is None:
        msgs.append(_("La date de fin « %(v)s » est invalide : elle est ignorée.") % {"v": au_raw})
    if au is None:
        au = today
    if du is None:
        du = au - timedelta(days=PERIODE_DEFAUT_JOURS - 1)

    flt = JournalFilters(du=du, au=au, messages=msgs)
    if au < du:
        flt.periode_invalide = True
        msgs.append(_("La date de fin est antérieure à la date de début : aucune entrée affichée."))
    elif (au - du).days + 1 > PERIODE_MAX_JOURS:
        flt.periode_invalide = True
        msgs.append(
            _("La période dépasse %(max)s jours : restreignez-la pour afficher les entrées.")
            % {"max": PERIODE_MAX_JOURS}
        )

    acteur = params.get("acteur", "").strip()
    if acteur:
        if acteur == ACTEUR_AUCUN or (
            acteur.isascii() and acteur.isdigit() and int(acteur) in {u.pk for u in users}
        ):
            flt.acteur = acteur
        else:
            msgs.append(_("L’acteur demandé est inconnu : filtre ignoré."))

    valid_actions = set(AuditAction.values)
    for action in params.getlist("action"):
        if action in valid_actions:
            if action not in flt.actions:
                flt.actions.append(action)
        elif action:
            msgs.append(_("L’action « %(v)s » est inconnue : elle est ignorée.") % {"v": action})

    type_ = params.get("type", "").strip()
    if type_:
        if type_ in OBJECT_TYPES:
            flt.type = type_
        else:
            msgs.append(_("Le type d’objet demandé est inconnu : filtre ignoré."))

    dossier = params.get("dossier", "").strip()
    if dossier:
        if dossier.isascii() and dossier.isdigit() and 0 < int(dossier) < 2**63:
            flt.dossier = int(dossier)
        else:
            msgs.append(
                _("Le numéro de dossier « %(v)s » est invalide : filtre ignoré.") % {"v": dossier}
            )

    ip = params.get("ip", "").strip()
    if ip:
        try:
            flt.ip = str(ipaddress.ip_address(ip))
        except ValueError:
            msgs.append(_("L’adresse IP « %(v)s » est invalide : filtre ignorée.") % {"v": ip})

    flt.inclure_journal = params.get("journal") == "1"
    flt.page = params.get("page", "1")
    return flt


def dossier_q(candidature_id: int) -> Q:
    """Entrées d'un dossier : objet = la candidature, ou ``metadata.candidature_id``.

    Le second terme utilise l'index d'expression ``(metadata->>'candidature_id')``.
    """
    return Q(meta_candidature_id=str(candidature_id)) | Q(
        content_type=content_type_for("candidature"), object_id=str(candidature_id)
    )


def with_dossier_alias(qs: QuerySet) -> QuerySet:
    return qs.alias(meta_candidature_id=KT("metadata__candidature_id"))


def exclude_journal_views(qs: QuerySet) -> QuerySet:
    """Masque les **consultations** du journal lui-même (``view`` avec ``metadata.vue`` =
    ``journal_*``). Les exports du journal (``export``) restent toujours visibles."""
    return qs.alias(
        meta_vue=Coalesce(KT("metadata__vue"), Value(""), output_field=TextField())
    ).exclude(action=AuditAction.VIEW, meta_vue__startswith="journal_")


def _bounds(flt: JournalFilters) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(flt.du, time.min), tz)
    end = timezone.make_aware(datetime.combine(flt.au + timedelta(days=1), time.min), tz)
    return start, end


def filter_journal(flt: JournalFilters) -> QuerySet:
    """Requête des entrées correspondant aux filtres (intersection), plus récentes d'abord."""
    qs = JournalAudit.objects.all()
    if flt.periode_invalide:
        return qs.none()
    start, end = _bounds(flt)
    qs = qs.filter(timestamp__gte=start, timestamp__lt=end)
    if flt.acteur == ACTEUR_AUCUN:
        qs = qs.filter(actor__isnull=True)
    elif flt.acteur:
        qs = qs.filter(actor_id=int(flt.acteur))
    if flt.actions:
        qs = qs.filter(action__in=flt.actions)
    if flt.type == TYPE_AUCUN:
        qs = qs.filter(content_type__isnull=True)
    elif flt.type:
        qs = qs.filter(content_type=content_type_for(flt.type))
    if flt.dossier is not None:
        qs = with_dossier_alias(qs).filter(dossier_q(flt.dossier))
    if flt.ip:
        qs = qs.filter(ip_address=flt.ip)
    if not flt.inclure_journal:
        qs = exclude_journal_views(qs)
    return qs.order_by("-timestamp", "-id")


def consultations_dossier(candidature_id: int) -> QuerySet:
    """Consultations d'un dossier (panneau de la fiche), hors consultations du journal."""
    qs = with_dossier_alias(JournalAudit.objects.filter(action=AuditAction.VIEW))
    return exclude_journal_views(qs.filter(dossier_q(candidature_id))).order_by("-timestamp", "-id")
