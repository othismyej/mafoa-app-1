from django.contrib import admin

from apps.audit.admin_mixins import ConsultationLoggingAdminMixin

from .models import Extraction


@admin.register(Extraction)
class ExtractionAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    list_display = ("piece", "methode", "statut", "modele", "confiance", "created_at")
    list_filter = ("methode", "statut")
    raw_id_fields = ("piece", "appel_ia")
