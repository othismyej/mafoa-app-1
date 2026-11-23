from django.contrib import admin

from .admin_mixins import ConsultationLoggingAdminMixin
from .models import JournalAudit


@admin.register(JournalAudit)
class JournalAuditAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    """Lecture seule ; ses consultations sont journalisées comme celles de l'écran du journal
    (KAN-14, Q7)."""

    consultation_vue_detail = "journal_detail"
    consultation_vue_liste = "journal_liste"
    list_display = ("timestamp", "actor_repr", "action", "object_repr", "ip_address")
    list_filter = ("action", "content_type")
    search_fields = ("actor_repr", "object_repr", "object_id")
    date_hierarchy = "timestamp"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
