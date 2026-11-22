from django.contrib import admin

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
class VerificationEligibiliteAdmin(admin.ModelAdmin):
    list_display = ("candidature", "resultat", "moteur_version", "created_at")
    list_filter = ("resultat",)
    raw_id_fields = ("candidature",)

    def has_change_permission(self, request, obj=None):
        return False


class ScoreCritereInline(admin.TabularInline):
    model = ScoreCritere
    extra = 0
    raw_id_fields = ("piece",)


@admin.register(Score)
class ScoreAdmin(admin.ModelAdmin):
    list_display = ("candidature", "grille", "note_globale", "origine", "created_at")
    list_filter = ("origine",)
    raw_id_fields = ("candidature",)
    inlines = [ScoreCritereInline]


@admin.register(Anomalie)
class AnomalieAdmin(admin.ModelAdmin):
    list_display = ("candidature", "type", "gravite", "resolue", "created_at")
    list_filter = ("type", "gravite", "resolue")
    raw_id_fields = ("candidature", "candidature_liee", "piece")
    readonly_fields = ("resolue_par", "resolue_le")


@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ("candidature", "decision", "auteur", "created_at")
    list_filter = ("decision",)
    raw_id_fields = ("candidature",)
    fields = ("candidature", "decision", "motif")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from .services import record_decision

        saved = record_decision(obj.candidature, request.user, obj.decision, obj.motif, request)
        obj.pk, obj.auteur, obj.created_at = saved.pk, saved.auteur, saved.created_at
