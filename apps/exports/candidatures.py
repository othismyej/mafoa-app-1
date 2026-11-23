"""Classeur Excel des candidatures d'un poste ou d'un concours (spec KAN-37).

Une ligne par candidature : identité, formation et expériences déclarées, décision humaine en
vigueur, verdict du moteur avec le détail critère par critère, anomalies non résolues.

Le nombre de requêtes est constant (règle 19) : candidatures en une requête, puis dernières
vérifications, dernières décisions et anomalies non résolues du périmètre en une requête chacune.
Jamais de sexe, région, photo, situation familiale ni âge (règle 5).
"""

from __future__ import annotations

import io
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop, pgettext
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.audit.exports import sanitize
from apps.evaluation.eligibility.experience import compute_experience
from apps.evaluation.models import Anomalie, Decision, Verdict, VerificationEligibilite
from apps.evaluation.services import profil_from_declared
from apps.recruitment import presenters
from apps.recruitment.models import Candidature, Concours, PosteConcours
from apps.recruitment.selectors import NON_EVALUEE

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SHEET_TITLE_MAX = 31
SYNTHESE = "Synthèse"
CRITERES_EXPORTES = ("niveau_etudes", "specialite", "experience")

# --------------------------------------------------------------------------------------
# Colonnes
# --------------------------------------------------------------------------------------

TEXT, WRAP, DATE, DATETIME, NUMBER, INT = "text", "wrap", "date", "datetime", "number", "int"


@dataclass(frozen=True)
class Colonne:
    titre: str
    largeur: int
    kind: str = TEXT
    role: str = ""  # "statut" / "verdict" : fond coloré selon la valeur (règle 13)


def colonnes() -> list[Colonne]:
    """En-têtes, dans l'ordre des règles 4 à 10 de la spec."""
    cols = [
        Colonne(_("N° de dossier"), 11, INT),
        Colonne(_("Poste"), 30),
        Colonne(_("Date de candidature"), 17, DATETIME),
        Colonne(_("Nom"), 18),
        Colonne(_("Prénom"), 18),
        Colonne(_("CIN"), 12),
        Colonne(_("Date de naissance"), 13, DATE),
        # Contexte : sans lui, le catalogue français de Django traduit « Email » en « Courriel ».
        Colonne(pgettext("colonne d'export", "Email"), 28),
        Colonne(_("Téléphone"), 16),
        Colonne(_("Ville"), 14),
        Colonne(_("Formations déclarées"), 45, WRAP),
        Colonne(_("Expériences déclarées"), 45, WRAP),
        Colonne(_("Expérience calculée (années)"), 13, NUMBER),
        Colonne(_("Statut actuel"), 16, TEXT, "statut"),
        Colonne(_("Décision humaine"), 18),
        Colonne(_("Motif de la décision"), 40, WRAP),
        Colonne(_("Auteur de la décision"), 26),
        Colonne(_("Date de la décision"), 17, DATETIME),
        Colonne(_("Verdict du moteur (pas une décision)"), 18, TEXT, "verdict"),
        Colonne(_("Date du verdict"), 17, DATETIME),
        Colonne(_("Version du moteur"), 10),
    ]
    libelles = {
        "niveau_etudes": _("Niveau d'études"),
        "specialite": _("Spécialité"),
        "experience": _("Expérience"),
    }
    for key in CRITERES_EXPORTES:
        lib = libelles[key]
        cols += [
            Colonne(f"{lib} — {_('attendu')}", 22, WRAP),
            Colonne(f"{lib} — {_('constaté')}", 22, WRAP),
            Colonne(f"{lib} — {_('verdict')}", 14, TEXT, "verdict"),
            Colonne(f"{lib} — {_('raison')}", 45, WRAP),
        ]
    cols += [
        Colonne(_("Autres critères (vérification humaine)"), 40, WRAP),
        Colonne(_("Anomalies non résolues (nombre)"), 12, INT),
        Colonne(_("Anomalies non résolues"), 45, WRAP),
    ]
    return cols


COLONNES_SYNTHESE_FIXES = (
    (gettext_noop("Référence"), 14, TEXT),
    (gettext_noop("Intitulé du poste"), 40, WRAP),
    (gettext_noop("Postes à pourvoir"), 11, INT),
    (gettext_noop("Date limite"), 13, DATE),
    (gettext_noop("Candidatures"), 13, INT),
    (gettext_noop("Éligibles (moteur)"), 12, INT),
    (gettext_noop("Non éligibles (moteur)"), 12, INT),
    (gettext_noop("À vérifier (moteur)"), 12, INT),
    (gettext_noop("Non évaluées"), 12, INT),
)
VERDICTS_SYNTHESE = (Verdict.ELIGIBLE, Verdict.NON_ELIGIBLE, Verdict.A_VERIFIER, NON_EVALUEE)

# --------------------------------------------------------------------------------------
# Styles (règles 12 et 13) : fonds pâles, jamais le rouge de marque.
# --------------------------------------------------------------------------------------

FILL_ENTETE = PatternFill("solid", fgColor="E7E5E4")
FILL_VERT = PatternFill("solid", fgColor="D1FAE5")
FILL_AMBRE = PatternFill("solid", fgColor="FEF3C7")
FILL_GRIS = PatternFill("solid", fgColor="E7E5E4")
FONT_ENTETE = Font(bold=True)
FONT_TOTAL = Font(bold=True)
ALIGN_ENTETE = Alignment(wrap_text=True, vertical="top")
ALIGN_WRAP = Alignment(wrap_text=True, vertical="top")
ALIGN_TOP = Alignment(vertical="top")
FORMATS = {DATE: "DD/MM/YYYY", DATETIME: "DD/MM/YYYY HH:MM", NUMBER: "0.00"}

FILL_PAR_CODE = {
    Candidature.Statut.ELIGIBLE: FILL_VERT,
    Candidature.Statut.PRESELECTIONNEE: FILL_VERT,
    Candidature.Statut.CONVOQUEE: FILL_VERT,
    Candidature.Statut.RETENUE: FILL_VERT,
    Candidature.Statut.A_VERIFIER: FILL_AMBRE,
    Candidature.Statut.NON_ELIGIBLE: FILL_GRIS,
    Candidature.Statut.REJETEE: FILL_GRIS,
}

# --------------------------------------------------------------------------------------
# Noms d'onglets (règle 15)
# --------------------------------------------------------------------------------------

_SHEET_INVALID = re.compile(r"[\[\]:*?/\\\x00-\x1f]")


class SheetTitles:
    """Noms d'onglets valides et uniques (comparaison insensible à la casse, comme Excel)."""

    def __init__(self, reserved: Iterable[str] = ()):
        self._used = {r.casefold() for r in reserved}

    def __call__(self, base: str, fallback: str) -> str:
        base = re.sub(r"\s+", " ", _SHEET_INVALID.sub("-", base or "")).strip().strip("'").strip()
        base = base or fallback
        candidate, n = base[:SHEET_TITLE_MAX].rstrip("' "), 2
        while not candidate or candidate.casefold() in self._used:
            suffix = f" ({n})"
            candidate = base[: SHEET_TITLE_MAX - len(suffix)].rstrip("' ") + suffix
            n += 1
        self._used.add(candidate.casefold())
        return candidate


def poste_sheet_base(poste: PosteConcours) -> tuple[str, str]:
    return (poste.reference or poste.intitule, f"Poste {poste.pk}")


# --------------------------------------------------------------------------------------
# Données d'une ligne
# --------------------------------------------------------------------------------------


@dataclass
class Contexte:
    """Dernière vérification, dernière décision et anomalies non résolues par candidature."""

    verifications: dict[int, VerificationEligibilite] = field(default_factory=dict)
    decisions: dict[int, Decision] = field(default_factory=dict)
    anomalies: dict[int, list[Anomalie]] = field(default_factory=dict)


def charger_contexte(**perimetre) -> Contexte:
    """Trois requêtes pour tout le périmètre (``candidature__poste_concours=…`` ou
    ``candidature__poste_concours__concours=…``), quel que soit le nombre de candidatures."""
    verifs = (
        VerificationEligibilite.objects.filter(**perimetre)
        .order_by("candidature_id", "-created_at", "-id")
        .distinct("candidature_id")
        .only("candidature_id", "resultat", "details", "moteur_version", "created_at")
    )
    decisions = (
        Decision.objects.filter(**perimetre)
        .select_related("auteur")
        .order_by("candidature_id", "-created_at", "-id")
        .distinct("candidature_id")
    )
    anomalies: dict[int, list[Anomalie]] = defaultdict(list)
    for a in Anomalie.objects.filter(resolue=False, **perimetre).order_by(
        "candidature_id", "created_at", "id"
    ):
        anomalies[a.candidature_id].append(a)
    return Contexte(
        verifications={v.candidature_id: v for v in verifs},
        decisions={d.candidature_id: d for d in decisions},
        anomalies=dict(anomalies),
    )


def _local(value: datetime | None) -> datetime | None:
    # Excel ne connaît pas les fuseaux : heure de Casablanca, sans fuseau (règle 14).
    return timezone.localtime(value).replace(tzinfo=None) if value else None


def _label(choices_cls, value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(choices_cls(value).label)
    except ValueError:
        return value


def verdict_label(value: str | None) -> str:
    return _label(Verdict, value) if value else _("Non évaluée")


def formations_texte(donnees) -> str:
    lignes = []
    for f in presenters.formations(donnees):
        parts = [f[k] for k in ("intitule", "niveau", "specialite", "etablissement")]
        parts.append(f["annee_obtention"])
        lignes.append(" — ".join(p for p in parts if p))
    lignes = [ligne for ligne in lignes if ligne]
    return "\n".join(lignes) if lignes else _("Aucune formation déclarée")


def experiences_texte(donnees) -> str:
    items = presenters.experiences(donnees)
    if items is None:
        return _("Non renseignées")
    lignes = []
    for e in items:
        qui = " chez ".join(p for p in (e["poste"], e["employeur"]) if p) or _("Sans intitulé")
        lignes.append(f"{qui} ({e['debut'].texte} → {e['fin'].texte})")
    return "\n".join(lignes) if lignes else _("Aucune expérience déclarée")


def experience_calculee(donnees, poste: PosteConcours) -> Decimal | None:
    """Années d'expérience par le code déterministe du moteur (règle 7) ; ``None`` si les
    expériences ne sont pas renseignées ou si leur structure est inexploitable."""
    try:
        profil = profil_from_declared(donnees if isinstance(donnees, dict) else {})
    except (AttributeError, TypeError, ValueError):
        return None
    if profil.experiences is None:
        return None
    reference = poste.date_reference or timezone.localdate()
    return compute_experience(profil.experiences, reference).annees


def ligne_candidature(c: Candidature, poste: PosteConcours, ctx: Contexte) -> list:
    candidat = c.candidat
    donnees = c.donnees_declarees or {}
    decision = ctx.decisions.get(c.pk)
    verif = ctx.verifications.get(c.pk)
    criteres = {}
    if verif is not None:
        criteres = {d["critere"]: d for d in presenters.criteres(verif.details)}
    anomalies = ctx.anomalies.get(c.pk, [])

    row = [
        c.pk,
        str(poste),
        _local(c.date_candidature),
        candidat.nom,
        candidat.prenom,
        candidat.cin,
        candidat.date_naissance,
        candidat.email,
        candidat.telephone,
        candidat.ville,
        formations_texte(donnees),
        experiences_texte(donnees),
        experience_calculee(donnees, poste),
        _label(Candidature.Statut, c.statut),
        _label(Candidature.Statut, decision.decision) if decision else _("Aucune décision humaine"),
        decision.motif if decision else "",
        decision.auteur.email if decision else "",
        _local(decision.created_at) if decision else None,
        verdict_label(verif.resultat if verif else None),
        _local(verif.created_at) if verif else None,
        verif.moteur_version if verif else "",
    ]
    for key in CRITERES_EXPORTES:
        d = criteres.get(key)
        row += (
            [d["attendu"], d["constate"], _label(Verdict, d["verdict"]), d["raison"]]
            if d
            else ["", "", "", ""]
        )
    autres = criteres.get("autres")
    row += [
        "\n".join(p for p in (autres["attendu"], autres["raison"]) if p) if autres else "",
        len(anomalies),
        "\n".join(
            f"[{a.get_gravite_display()}] {a.get_type_display()} : {a.description}"
            for a in anomalies
        ),
    ]
    return row


# --------------------------------------------------------------------------------------
# Écriture du classeur
# --------------------------------------------------------------------------------------


def _fill_for(col: Colonne, code: str | None) -> PatternFill | None:
    if not col.role or not code:
        return None
    return FILL_PAR_CODE.get(code)


def _cell(ws, value, col: Colonne, fill=None, font=None):
    if isinstance(value, str):
        value = sanitize(value) if value else None
    cell = WriteOnlyCell(ws, value=value)
    if col.kind in FORMATS and value is not None:
        cell.number_format = FORMATS[col.kind]
    cell.alignment = ALIGN_WRAP if col.kind == WRAP else ALIGN_TOP
    if fill is not None:
        cell.fill = fill
    if font is not None:
        cell.font = font
    return cell


def _prepare(ws, cols: list[Colonne]) -> None:
    """Largeurs, volet figé et en-têtes (avant toute ligne : écriture seule)."""
    for i, col in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = col.largeur
    ws.freeze_panes = "A2"
    header = []
    for col in cols:
        cell = WriteOnlyCell(ws, value=sanitize(col.titre))
        cell.font, cell.fill, cell.alignment = FONT_ENTETE, FILL_ENTETE, ALIGN_ENTETE
        header.append(cell)
    ws.append(header)


def _finish(ws, ncols: int, nrows: int) -> None:
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}{max(nrows, 0) + 1}"


# Indices des colonnes colorées : statut (code brut) et verdicts (code brut).
def _codes(c: Candidature, ctx: Contexte) -> list[str | None]:
    verif = ctx.verifications.get(c.pk)
    criteres = (
        {d["critere"]: d["verdict"] for d in presenters.criteres(verif.details)} if verif else {}
    )
    return [c.statut, verif.resultat if verif else None] + [
        criteres.get(k) for k in CRITERES_EXPORTES
    ]


def ecrire_onglet(wb: Workbook, title: str, poste: PosteConcours, candidatures, ctx: Contexte):
    cols = colonnes()
    ws = wb.create_sheet(title)
    _prepare(ws, cols)
    roles = [i for i, col in enumerate(cols) if col.role]
    n = 0
    for c in candidatures:
        values = ligne_candidature(c, poste, ctx)
        codes = dict(zip(roles, _codes(c, ctx), strict=True))
        ws.append(
            [
                _cell(ws, v, col, fill=_fill_for(col, codes.get(i)))
                for i, (v, col) in enumerate(zip(values, cols, strict=True))
            ]
        )
        n += 1
    _finish(ws, len(cols), n)
    return n


def _save(wb: Workbook) -> bytes:
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def classeur_poste(poste: PosteConcours, candidatures: QuerySet[Candidature]) -> bytes:
    """Un onglet : les candidatures données (déjà filtrées et triées), avec ``candidat`` chargé."""
    wb = Workbook(write_only=True)
    ctx = charger_contexte(candidature__poste_concours=poste)
    title = SheetTitles(reserved=[SYNTHESE])(*poste_sheet_base(poste))
    ecrire_onglet(wb, title, poste, candidatures.iterator(chunk_size=2000), ctx)
    return _save(wb)


def candidatures_concours(concours: Concours) -> QuerySet[Candidature]:
    return (
        Candidature.objects.filter(poste_concours__concours=concours)
        .select_related("candidat")
        .order_by("poste_concours_id", "date_candidature", "id")
    )


def _ecrire_synthese(wb: Workbook, postes, par_poste: dict[int, list[Candidature]], ctx):
    ws = wb.create_sheet(SYNTHESE)
    statuts = list(Candidature.Statut.choices)
    cols = [Colonne(_(t), w, k) for t, w, k in COLONNES_SYNTHESE_FIXES] + [
        Colonne(f"{_('Statut')} : {label}", 12, INT) for _v, label in statuts
    ]
    _prepare(ws, cols)
    totaux = Counter()
    for poste in postes:
        cands = par_poste.get(poste.pk, [])
        verdicts = Counter(
            (ctx.verifications[c.pk].resultat if c.pk in ctx.verifications else NON_EVALUEE)
            for c in cands
        )
        par_statut = Counter(c.statut for c in cands)
        nombres = (
            [poste.nombre_postes, poste.date_reference, len(cands)]
            + [verdicts.get(v, 0) for v in VERDICTS_SYNTHESE]
            + [par_statut.get(v, 0) for v, _l in statuts]
        )
        values = [poste.reference, poste.intitule, *nombres]
        ws.append([_cell(ws, v, col) for v, col in zip(values, cols, strict=True)])
        for i, v in enumerate(values):
            if isinstance(v, int) and i != 3:
                totaux[i] += v
    total_row = [_("Total"), ""] + [
        None if i == 3 else totaux.get(i, 0) for i in range(2, len(cols))
    ]
    ws.append([_cell(ws, v, col, font=FONT_TOTAL) for v, col in zip(total_row, cols, strict=True)])
    _finish(ws, len(cols), len(postes) + 1)


def classeur_concours(concours: Concours) -> tuple[bytes, int, int]:
    """Onglet « Synthèse » puis un onglet par poste. Renvoie (contenu, nb_lignes, nb_postes)."""
    postes = list(concours.postes.select_related("concours"))
    par_poste: dict[int, list[Candidature]] = defaultdict(list)
    for c in candidatures_concours(concours):
        par_poste[c.poste_concours_id].append(c)
    ctx = charger_contexte(candidature__poste_concours__concours=concours)
    wb = Workbook(write_only=True)
    _ecrire_synthese(wb, postes, par_poste, ctx)
    titles = SheetTitles(reserved=[SYNTHESE])
    total = 0
    for poste in postes:
        total += ecrire_onglet(
            wb, titles(*poste_sheet_base(poste)), poste, par_poste.get(poste.pk, []), ctx
        )
    return _save(wb), total, len(postes)


def nom_fichier(perimetre: str, pk: int) -> str:
    """Règle 18 : aucun nom, aucun terme de recherche."""
    return f"candidatures-{perimetre}-{pk}-{timezone.localtime():%Y%m%d-%H%M}.xlsx"
