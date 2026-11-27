from django.contrib import admin

from apps.audit.admin_mixins import ConsultationLoggingAdminMixin

from .models import (
    Anomalie,
    Critere,
    Decision,
    GrilleEvaluation,
    Score,
    ScoreCritere,
    VerificationEligibilite,
)


class CritereInline(admin.TabularInline):
    model = Critere
    fields = ("ordre", "libelle", "ponderation", "bareme_max", "consignes_ia")
    extra = 1


@admin.register(GrilleEvaluation)
class GrilleEvaluationAdmin(admin.ModelAdmin):
    list_display = ("nom", "version", "concours", "poste_concours", "offre", "active")
    list_filter = ("active",)
    inlines = [CritereInline]


@admin.register(VerificationEligibilite)
class VerificationEligibiliteAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    list_display = ("candidature", "resultat", "moteur_version", "created_at")
    list_filter = ("resultat",)
    raw_id_fields = ("candidature",)

    # Produites uniquement par le moteur d'éligibilité, jamais saisies à la main.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ScoreCritereInline(admin.TabularInline):
    model = ScoreCritere
    extra = 0
    raw_id_fields = ("piece",)


@admin.register(Score)
class ScoreAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    list_display = ("candidature", "grille", "note_globale", "origine", "created_at")
    list_filter = ("origine",)
    raw_id_fields = ("candidature",)
    inlines = [ScoreCritereInline]


@admin.register(Anomalie)
class AnomalieAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    """Consultation seule (KAN-42, règle 32) : la fiche candidature est le seul point de
    résolution, motivée et journalisée."""

    list_display = ("candidature", "type", "code", "gravite", "resolue", "created_at")
    list_filter = ("type", "gravite", "resolue")
    raw_id_fields = ("candidature", "candidature_liee", "piece")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Decision)
class DecisionAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    """Consultation seule : la fiche candidature est le seul point d'entrée des décisions."""

    list_display = ("candidature", "decision", "auteur", "created_at")
    list_filter = ("decision",)
    raw_id_fields = ("candidature",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
