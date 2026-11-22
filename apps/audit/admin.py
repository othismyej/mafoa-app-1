from django.contrib import admin

from .models import JournalAudit


@admin.register(JournalAudit)
class JournalAuditAdmin(admin.ModelAdmin):
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
