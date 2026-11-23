"""Journalisation des consultations dans l'administration Django (spec KAN-14, règle 7).

À ajouter en tête des bases de chaque ``ModelAdmin`` d'un modèle portant des données de
candidats (``Candidat``, ``Candidature``, ``Piece``, ``Extraction``,
``VerificationEligibilite``, ``Score``, ``Anomalie``, ``Decision`` et tout modèle futur
rattaché à un candidat).
"""

from __future__ import annotations

from django.contrib.admin.views.main import IGNORED_PARAMS, PAGE_VAR, SEARCH_VAR

from .services import log_consultation

# Paramètres d'affichage ou techniques non retenus comme filtres.
_NON_FILTRES = set(IGNORED_PARAMS) | {PAGE_VAR, SEARCH_VAR}


class ConsultationLoggingAdminMixin:
    """Journalise chaque page de détail, d'historique et de liste servie en ``GET``."""

    consultation_vue_detail = "admin_detail"
    consultation_vue_liste = "admin_liste"

    def _servie(self, request, response) -> bool:
        return request.method == "GET" and response.status_code == 200

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        response = super().changeform_view(request, object_id, form_url, extra_context)
        if object_id is not None and self._servie(request, response):
            obj = (getattr(response, "context_data", None) or {}).get("original")
            if obj is not None:
                log_consultation(request, obj, self.consultation_vue_detail)
        return response

    def history_view(self, request, object_id, extra_context=None):
        response = super().history_view(request, object_id, extra_context)
        if self._servie(request, response):
            obj = (getattr(response, "context_data", None) or {}).get("object")
            if obj is not None:
                log_consultation(
                    request, obj, self.consultation_vue_detail, page_admin="historique"
                )
        return response

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        cl = (getattr(response, "context_data", None) or {}).get("cl")
        if cl is not None and self._servie(request, response):
            filtres = {
                key: request.GET.getlist(key)
                if len(request.GET.getlist(key)) > 1
                else request.GET.get(key)
                for key in request.GET
                if key not in _NON_FILTRES
            }
            log_consultation(
                request,
                None,
                self.consultation_vue_liste,
                object_repr=f"Administration : {self.model._meta.verbose_name_plural}",
                modele=self.model._meta.label_lower,
                recherche=cl.query,
                filtres=filtres,
                page=cl.page_num,
                nb_resultats=cl.result_count,
            )
        return response
