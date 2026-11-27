import logging
from urllib.parse import parse_qsl

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST
from django.views.defaults import permission_denied

from apps.accounts.permissions import DIRECTION_ROLES, role_required
from apps.audit.models import AuditAction
from apps.audit.services import log, log_access_denied, log_consultation, role_required_logged
from apps.evaluation import decisions as decision_rules
from apps.evaluation.anomalies import presentation as presentation_anomalies
from apps.evaluation.decisions import ConfirmationRequise, DecisionError
from apps.evaluation.models import Anomalie, Decision, VerificationEligibilite
from apps.evaluation.services import record_decision
from integrations.site_source.base import get_source

from . import pieces as pieces_service
from . import presenters
from . import spontanees as spontanees_service
from .filters import ANOMALIE_CHOICES, PAGE_SIZES, VERDICT_CHOICES, filter_candidatures
from .models import Candidature, Concours, Piece, PosteConcours
from .selectors import concours_stats


@require_GET
@role_required(*DIRECTION_ROLES)
def concours_detail(request, pk: int):
    """Fiche d'un concours : postes et répartition des candidatures (spec KAN-9)."""
    concours = get_object_or_404(Concours.objects.select_related("organisation"), pk=pk)
    stats = concours_stats(concours)
    log(AuditAction.VIEW, obj=concours, request=request)
    return render(request, "recruitment/concours_detail.html", {"stats": stats})


@require_GET
@role_required(*DIRECTION_ROLES)
def poste_candidatures(request, concours_pk: int, poste_pk: int):
    """Liste des candidatures d'un poste : filtres, recherche, tri, pagination (spec KAN-10)."""
    poste = get_object_or_404(
        PosteConcours.objects.select_related("concours"), pk=poste_pk, concours_id=concours_pk
    )
    return _liste_candidatures(
        request,
        poste,
        "recruitment/poste_candidatures.html",
        {"poste": poste, "concours": poste.concours, "lot_actif": True},
    )


def _liste_candidatures(request, cible, page_template: str, extra: dict):
    """Liste filtrée des candidatures d'une cible, poste ou offre (KAN-10, KAN-38 règle 14).

    Même logique serveur (``filter_candidatures``), mêmes gabarits partagés, même
    journalisation (une entrée ``view`` par réponse servie, objet = la cible).
    """
    queryset, flt = filter_candidatures(cible, request.GET)
    page = Paginator(queryset, flt.taille).get_page(flt.page)
    total = cible.candidatures.count()
    context = {
        **extra,
        "flt": flt,
        "page": page,
        "total": total,
        # « Conservée au vivier » est impossible pour un poste ou une offre (KAN-39, règle 44).
        "statut_choices": Candidature.statut_choices_avec_cible(),
        "verdict_choices": VERDICT_CHOICES,
        "anomalie_choices": ANOMALIE_CHOICES,
        "page_sizes": PAGE_SIZES,
        "querystring": flt.querystring(include_tri=True),
        "querystring_sans_tri": flt.querystring(include_tri=False),
        "sort_headers": [
            {
                "key": key,
                "label": label,
                "param": flt.sort_param_for(key),
                "active": key == flt.tri,
                "desc": flt.desc,
            }
            for key, label in SORT_HEADERS
        ],
    }
    if context.get("lot_actif"):
        # Barre d'actions en lot (KAN-15) : paramètres validés et décisions permises en lot.
        context["lot_params"] = parse_qsl(flt.querystring(include_tri=True))
        context["lot_decisions"] = [
            (d, Candidature.Statut(d).label) for d in decision_rules.DECISIONS_LOT
        ]
    log(
        AuditAction.VIEW,
        obj=cible,
        request=request,
        metadata={**flt.as_metadata(), "page": page.number, "nb_resultats": page.paginator.count},
    )
    # Fragment pour les requêtes HTMX, sauf restauration d'historique (page complète attendue).
    fragment = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    template = "recruitment/_candidatures_resultats.html" if fragment else page_template
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request", "HX-History-Restore-Request"])
    return response


SORT_HEADERS = [
    ("candidat", _("Candidat")),
    ("date", _("Date de candidature")),
    ("statut", _("Statut")),
    ("verdict", _("Verdict d’éligibilité")),
    ("anomalies", _("Anomalies")),
]


HISTORIQUE_VERIFICATIONS = 20


def _fiche_queryset():
    return Candidature.objects.select_related(
        "candidat",
        "poste_concours__concours__organisation",
        "offre__organisation",
        "details_spontanee",
    ).prefetch_related(
        Prefetch("pieces", queryset=Piece.objects.order_by("type", "id")),
        Prefetch(
            "anomalies",
            queryset=Anomalie.objects.select_related(
                "piece",
                "resolue_par",
                "candidature_liee__candidat",
                "candidature_liee__poste_concours",
                "candidature_liee__offre",
            ).order_by("resolue", "-created_at", "-id"),
        ),
        Prefetch(
            "decisions",
            queryset=Decision.objects.select_related("auteur").order_by("-created_at", "-id"),
        ),
    )


AUTRES_CANDIDATURES_MAX = 20


def _autres_candidatures(candidature: Candidature) -> tuple[list[Candidature], int]:
    """Autres candidatures du même candidat (KAN-39, règle 27) : 20 au plus, total à part.

    Deux requêtes au plus, quel que soit le nombre de dossiers.
    """
    qs = (
        Candidature.objects.filter(candidat_id=candidature.candidat_id)
        .exclude(pk=candidature.pk)
        .select_related("poste_concours", "offre")
        .order_by("-date_candidature", "-id")
    )
    autres = list(qs[: AUTRES_CANDIDATURES_MAX + 1])
    total = qs.count() if len(autres) > AUTRES_CANDIDATURES_MAX else len(autres)
    return autres[:AUTRES_CANDIDATURES_MAX], total


def _spontanee_context(candidature: Candidature) -> dict:
    details = getattr(candidature, "details_spontanee", None)
    autres, total = _autres_candidatures(candidature)
    return {
        "details": details,
        "autres_candidatures": autres,
        "autres_candidatures_total": total,
        "fin_conservation": spontanees_service.fin_conservation(candidature),
        "duree_conservation": spontanees_service.duree_conservation_mois(),
    }


def _fiche_context(
    candidature: Candidature, form: dict | None = None, anomalie_form: dict | None = None
) -> dict:
    # Historique borné : la dernière vérification + les 20 précédentes (la page ne grossit pas
    # indéfiniment avec les réévaluations), et le nombre total pour information.
    # Candidature spontanée : aucune vérification d'éligibilité (KAN-39, règle 26).
    if candidature.est_spontanee:
        verifications, total_verifications = [], 0
    else:
        verifs_qs = VerificationEligibilite.objects.filter(candidature=candidature).order_by(
            "-created_at", "-id"
        )
        verifications = list(verifs_qs[: HISTORIQUE_VERIFICATIONS + 1])
        total_verifications = (
            verifs_qs.count()
            if len(verifications) > HISTORIQUE_VERIFICATIONS
            else len(verifications)
        )
    decisions_list = list(candidature.decisions.all())
    anomalies = list(candidature.anomalies.all())
    derniere = verifications[0] if verifications else None
    donnees = candidature.donnees_declarees or {}
    return {
        "c": candidature,
        "candidat": candidature.candidat,
        "pieces": (pieces := list(candidature.pieces.all())),
        # Sans JavaScript, une pièce non affichable s'ouvre directement en téléchargement.
        "pieces_telechargement": {
            p.pk for p in pieces if pieces_service.mode_affichage(p.content_type) == "aucun"
        },
        "pieces_en_anomalie": {a.piece_id for a in anomalies if a.piece_id and not a.resolue},
        "anomalies": anomalies,
        # KAN-42, règle 30 : libellé du code, source, état de l'anomalie symétrique, issues.
        "anomalies_vues": presentation_anomalies.presenter(candidature, anomalies),
        "anomalie_form": anomalie_form or {},
        "motif_max": decision_rules.MOTIF_MAX,
        "nb_anomalies_non_resolues": sum(1 for a in anomalies if not a.resolue),
        "derniere_verification": derniere,
        "verifications_precedentes": verifications[1:],
        "verifications_masquees": max(0, total_verifications - len(verifications)),
        "sources": presenters.source_labels(derniere.sources) if derniere else [],
        "criteres": presenters.criteres(derniere.details) if derniere else [],
        "ecart": presenters.ecart_verdict_decision(
            candidature.statut,
            derniere.resultat if derniere else None,
            bool(decisions_list),
            decisions_list[0].decision if decisions_list else None,
        ),
        "decisions": decisions_list,
        "formations": presenters.formations(donnees),
        "experiences": presenters.experiences(donnees),
        "decision_form": _decision_form_context(candidature, decisions_list, form or {}),
        **(_spontanee_context(candidature) if candidature.est_spontanee else {}),
    }


def _decision_form_context(candidature, decisions_list, form: dict) -> dict:
    statut = candidature.statut
    return {
        "disponible": not decision_rules.concours_termine(candidature),
        "choix": [
            (d, Candidature.Statut(d).label)
            for d in decision_rules.decisions_permises(statut, candidature.type)
        ],
        "statut_attendu": statut,
        "derniere_decision_id": decisions_list[0].pk if decisions_list else "",
        "decision": form.get("decision", ""),
        "motif": form.get("motif", ""),
        "erreur": form.get("erreur", ""),
        "confirmation": form.get("confirmation"),
        "succes": form.get("succes", ""),
        "motif_max": decision_rules.MOTIF_MAX,
    }


@require_GET
@role_required_logged(*DIRECTION_ROLES, candidature_kwarg="pk")
def candidature_detail(request, pk: int):
    """Fiche candidature en lecture seule (spec KAN-11) ; formulaire de décision (KAN-13).

    Consultation journalisée (KAN-14) ; un refus d'accès est tracé en ``access_denied``.
    """
    candidature = get_object_or_404(_fiche_queryset(), pk=pk)
    # Journalisé avant le rendu : si l'écriture échoue, rien n'est servi (KAN-14, règle 6).
    log_consultation(request, candidature, "fiche")
    return render(request, "recruitment/candidature_detail.html", _fiche_context(candidature))


def _journaliser_refus(request, candidature_pk: int, refus: str, **metadata) -> None:
    """Trace un refus anormal (Q6) : sans nom ni motif saisi."""
    log(
        AuditAction.UPDATE,
        object_repr=f"Candidature #{candidature_pk}",
        request=request,
        metadata={"refus": refus, "candidature_id": candidature_pk, **metadata},
    )


@require_POST
def decision_enregistrer(request, pk: int):
    """Enregistre une décision humaine depuis la fiche (spec KAN-13)."""
    if not (request.user.is_authenticated and request.user.has_role(*DIRECTION_ROLES)):
        # Réponse 403 renvoyée (et non levée) pour que la trace survive à ATOMIC_REQUESTS.
        _journaliser_refus(request, pk, "droits")
        return permission_denied(request, PermissionDenied())
    candidature = get_object_or_404(Candidature, pk=pk)
    htmx = request.headers.get("HX-Request") == "true"
    data = request.POST
    derniere_raw = data.get("derniere_decision_id", "")
    form = {"decision": data.get("decision", ""), "motif": data.get("motif", "")}
    status = 200
    try:
        record_decision(
            candidature,
            request.user,
            form["decision"],
            form["motif"],
            request,
            statut_attendu=data.get("statut_attendu") or None,
            derniere_decision_attendue=(
                int(derniere_raw) if derniere_raw.isascii() and derniere_raw.isdigit() else None
            ),
            confirmer_revision=data.get("confirmer_revision") == "1",
            confirmer_avertissements=data.get("confirmer_avertissements") == "1",
        )
    except ConfirmationRequise as exc:
        form["confirmation"] = {
            "revision": exc.revision,
            "avertissements": [
                _(decision_rules.AVERTISSEMENT_LIBELLES[a]) for a in exc.avertissements
            ],
            "derniere_date": candidature.decisions.order_by("-created_at", "-id")
            .values_list("created_at", flat=True)
            .first(),
        }
    except DecisionError as exc:
        if exc.code in decision_rules.CODES_REFUS_JOURNALISES:
            _journaliser_refus(
                request,
                pk,
                exc.code,
                statut=candidature.statut,
                decision=form["decision"][:50],
            )
        form["erreur"] = exc.message
        if exc.code == "concurrence":
            form = {"erreur": exc.message}  # formulaire réinitialisé sur l'état à jour
        status = 409 if exc.code == "concurrence" else 400
    else:
        if not htmx:
            messages.success(request, _("Décision enregistrée."))
            return redirect("recruitment:candidature_detail", pk=pk)
        form = {"succes": _("Décision enregistrée.")}

    candidature = _fiche_queryset().get(pk=pk)
    context = _fiche_context(candidature, form)
    if htmx:
        return render(request, "recruitment/_decisions.html", context, status=status)
    # Sans JavaScript, la fiche complète est réaffichée : c'est une consultation (§5).
    log_consultation(request, candidature, "fiche_apres_decision")
    return render(request, "recruitment/candidature_detail.html", context, status=status)


# --------------------------------------------------------------------------------------
# Visionneuse des pièces (spec KAN-12)
# --------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

PIECE_FIELDS = (
    "id",
    "candidature_id",
    "type",
    "nom_fichier",
    "storage_key",
    "content_type",
    "taille",
    "sha256",
    "recue_le",
)

# CSP propre à la vue de contenu (règle 11) : rien n'est chargé, seul le cadrage par la fiche
# (même origine) est permis. « sandbox » pour les images uniquement : Chromium refuse
# d'afficher un PDF dans un contexte sandbox.
CSP_CONTENU = "default-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
CSP_CONTENU_IMAGE = f"{CSP_CONTENU}; sandbox"
# Page d'erreur affichée dans l'iframe (règle 18) : aucun script, feuille de style locale.
CSP_ERREUR = (
    "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; "
    "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
)

MESSAGES_PIECE = {
    "source_indisponible": _(
        "La source des pièces est momentanément indisponible. Réessayez dans quelques instants."
    ),
    "introuvable": _("Fichier introuvable à la source."),
    "vide": _("Le fichier reçu est vide."),
    "type_non_affichable": _(
        "Aperçu impossible : le contenu ne correspond pas à un type affichable. "
        "Utilisez « Télécharger »."
    ),
    "empreinte_differente": _(
        "Le fichier a été modifié à la source depuis sa réception (empreinte différente). "
        "Une anomalie a été enregistrée. Vous pouvez le télécharger pour l’examiner."
    ),
    "piece_inconnue": _("Pièce introuvable."),
}

STATUTS_PIECE = {
    "trop_volumineuse": 422,
    "source_indisponible": 503,
    "introuvable": 404,
    "vide": 422,
    "type_non_affichable": 415,
    "empreinte_differente": 409,
    "piece_inconnue": 404,
}


def _journaliser_piece(
    request,
    candidature_pk: int,
    piece_pk: int,
    *,
    piece: Piece | None = None,
    **metadata,
) -> None:
    """Point d'accroche unique de la journalisation des pièces, branché sur KAN-14.

    - refus (``metadata.refus`` : droits, pièce hors candidature) → ``access_denied`` ;
    - toute autre réponse de la vue de contenu → consultation ``view`` (``vue = piece``).

    Libellé sans nom : ni ``nom_fichier`` ni ``storage_key`` ne sont journalisés.
    """
    object_repr = f"Pièce #{piece_pk} (Candidature #{candidature_pk})"
    if "refus" in metadata:
        log_access_denied(
            request,
            candidature_id=candidature_pk,
            object_repr=object_repr,
            piece_id=piece_pk,
            **metadata,
        )
        return
    log_consultation(
        request,
        piece,
        "piece",
        object_repr=object_repr,
        candidature_id=candidature_pk,
        piece_id=piece_pk,
        **metadata,
    )


def _taille_mo(octets: int) -> str:
    valeur = octets / (1024 * 1024)
    texte = f"{valeur:.1f}" if valeur >= 0.1 else f"{valeur:.3f}"
    return texte.rstrip("0").rstrip(".").replace(".", ",") or "0"


def _page_erreur_piece(request, code: str, message: str | None = None):
    """Petite page HTML sans script, lisible dans l'iframe de la visionneuse (règle 18)."""
    response = render(
        request,
        "recruitment/piece_erreur.html",
        {"message": message or MESSAGES_PIECE[code]},
        status=STATUTS_PIECE[code],
    )
    response["Content-Security-Policy"] = CSP_ERREUR
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


@require_GET
def piece_contenu(request, pk: int, piece_pk: int):
    """Contenu d'une pièce, lu à la demande via ``SiteSource`` (spec KAN-12, règles 4 à 18).

    Toutes les réponses sont renvoyées (jamais levées) pour que l'entrée de journal survive à
    ``ATOMIC_REQUESTS``. Le contenu n'est jamais écrit sur disque, en cache ni en base.
    """
    if not (request.user.is_authenticated and request.user.has_role(*DIRECTION_ROLES)):
        _journaliser_piece(request, pk, piece_pk, refus="droits")
        return permission_denied(request, PermissionDenied())
    piece = Piece.objects.filter(pk=piece_pk).only(*PIECE_FIELDS).first()
    if piece is None or not Candidature.objects.filter(pk=pk).exists():
        return _page_erreur_piece(request, "piece_inconnue")
    if piece.candidature_id != pk:
        _journaliser_piece(request, pk, piece_pk, refus="piece_hors_candidature")
        return _page_erreur_piece(request, "piece_inconnue")

    mode = "telechargement" if request.GET.get("telechargement") == "1" else "apercu"
    metadata = {"type_piece": piece.type, "mode": mode}

    def refus(resultat: str, message: str | None = None, **extra):
        _journaliser_piece(
            request, pk, piece.pk, piece=piece, resultat=resultat, **metadata, **extra
        )
        return _page_erreur_piece(request, resultat, message)

    max_bytes = settings.PIECE_VIEWER_MAX_BYTES

    def trop_volumineuse(octets: int, **extra):
        message = _(
            "Pièce trop volumineuse pour la visionneuse (taille : %(taille)s Mo ; "
            "maximum : %(max)s Mo)."
        ) % {"taille": _taille_mo(octets), "max": _taille_mo(max_bytes)}
        return refus("trop_volumineuse", message, **extra)

    if piece.taille is not None and piece.taille > max_bytes:
        return trop_volumineuse(piece.taille)

    try:
        content = get_source().read_piece(
            piece.storage_key, max_bytes=max_bytes, timeout=settings.PIECE_SOURCE_TIMEOUT
        )
    except FileNotFoundError:
        return refus("introuvable")
    except Exception as exc:  # noqa: BLE001 — toute erreur de la source : 503, jamais 500
        # Journal applicatif : classe de l'erreur et identifiant interne uniquement.
        logger.warning("Lecture de la pièce #%s impossible : %s", piece.pk, type(exc).__name__)
        return refus("source_indisponible")

    taille = len(content)
    if taille == 0:
        return refus("vide", taille=0)
    if taille > max_bytes:
        del content
        return trop_volumineuse(taille, taille=taille)

    type_detecte = pieces_service.detect_type(content)
    controle = pieces_service.controler_empreinte(piece, content)
    metadata.update(
        empreinte=controle.statut,
        type_detecte=type_detecte or pieces_service.OCTET_STREAM,
        taille=taille,
    )
    if controle.statut == "differente":
        pieces_service.signaler_empreinte_differente(piece, controle, request.user)
        if mode == "apercu":
            return refus("empreinte_differente")
    if mode == "apercu" and type_detecte is None:
        return refus("type_non_affichable")

    nom = pieces_service.nom_assaini(piece.nom_fichier, piece.pk, type_detecte)
    response = HttpResponse(content, content_type=type_detecte or pieces_service.OCTET_STREAM)
    response["Content-Disposition"] = pieces_service.content_disposition(
        "inline" if mode == "apercu" else "attachment", nom
    )
    response["Content-Length"] = str(taille)
    response["Accept-Ranges"] = "none"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["Content-Security-Policy"] = (
        # ``sandbox`` partout sauf pour le PDF (le lecteur de Chromium le refuse).
        CSP_CONTENU if type_detecte == pieces_service.PDF else CSP_CONTENU_IMAGE
    )
    _journaliser_piece(request, pk, piece.pk, piece=piece, resultat="servie", **metadata)
    return response


@require_GET
@role_required(*DIRECTION_ROLES)
def piece_visionneuse(request, pk: int, piece_pk: int):
    """Fragment HTMX de la visionneuse (règles 9, 19 à 21) : métadonnées seulement, aucun
    appel à la source, non journalisé (Q6)."""
    pieces = list(
        Piece.objects.filter(candidature_id=pk).order_by("type", "id").only(*PIECE_FIELDS)
    )
    index = next((i for i, p in enumerate(pieces) if p.pk == piece_pk), None)
    if index is None:
        return render(
            request,
            "recruitment/_visionneuse_erreur.html",
            {"message": MESSAGES_PIECE["piece_inconnue"]},
            status=404,
        )
    piece = pieces[index]
    anomalies = set(
        Anomalie.objects.filter(piece_id=piece.pk, resolue=False).values_list("type", flat=True)
    )
    contenu = reverse("recruitment:piece_contenu", args=[pk, piece.pk])
    return render(
        request,
        "recruitment/_visionneuse.html",
        {
            "piece": piece,
            "position": index + 1,
            "total": len(pieces),
            "precedente": pieces[index - 1] if index > 0 else None,
            "suivante": pieces[index + 1] if index + 1 < len(pieces) else None,
            "mode": pieces_service.mode_affichage(piece.content_type),
            "url_contenu": contenu,
            "url_telechargement": f"{contenu}?telechargement=1",
            "en_anomalie": bool(anomalies),
            "empreinte_modifiee": Anomalie.Type.EMPREINTE_DIFFERENTE in anomalies,
        },
    )
