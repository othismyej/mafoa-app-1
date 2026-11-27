from django.contrib import admin

from apps.audit.admin_mixins import ConsultationLoggingAdminMixin
from apps.audit.models import AuditAction
from apps.audit.services import log

from .models import (
    Candidat,
    Candidature,
    Concours,
    DetailsSpontanee,
    OffreEmploi,
    Organisation,
    Piece,
    PosteConcours,
)

SOURCE_FIELDS = ("source_id", "source_synced_at")


class PiecesObligatoiresAuditMixin:
    """Journalise toute modification des pièces obligatoires (KAN-42, règle 14) : ``update``
    avec la valeur avant et après. La détection n'est pas relancée (règle 16)."""

    def save_model(self, request, obj, form, change):
        avant = None
        if change and obj.pk:
            avant = (
                type(obj)
                .objects.filter(pk=obj.pk)
                .values_list("pieces_obligatoires", flat=True)
                .first()
            )
        super().save_model(request, obj, form, change)
        apres = obj.pieces_obligatoires
        if (change and avant != apres) or (not change and apres is not None):
            log(
                AuditAction.UPDATE,
                obj=obj,
                request=request,
                before={"pieces_obligatoires": avant},
                after={"pieces_obligatoires": apres},
                metadata={"operation": "pieces_obligatoires"},
            )


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ("nom", "type", "ville")
    list_filter = ("type",)
    search_fields = ("nom",)


class PosteConcoursInline(admin.TabularInline):
    model = PosteConcours
    fields = ("reference", "intitule", "fonction", "nombre_postes", "niveau_etudes_min")
    extra = 0
    show_change_link = True


@admin.register(Concours)
class ConcoursAdmin(PiecesObligatoiresAuditMixin, admin.ModelAdmin):
    list_display = ("intitule", "organisation", "date_publication", "date_limite", "statut")
    list_filter = ("statut", "organisation")
    search_fields = ("intitule",)
    readonly_fields = SOURCE_FIELDS
    inlines = [PosteConcoursInline]


@admin.register(PosteConcours)
class PosteConcoursAdmin(PiecesObligatoiresAuditMixin, admin.ModelAdmin):
    list_display = (
        "reference",
        "intitule",
        "concours",
        "fonction",
        "nombre_postes",
        "niveau_etudes_min",
        "experience_min_annees",
    )
    list_filter = ("concours", "fonction", "niveau_etudes_min")
    search_fields = ("intitule", "reference")
    readonly_fields = SOURCE_FIELDS
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "concours",
                    "reference",
                    "intitule",
                    "slug",
                    "secteur",
                    "fonction",
                    "nombre_postes",
                    "mode_travail",
                    "ville",
                    "date_publication",
                    "date_limite",
                )
            },
        ),
        ("Contenu", {"fields": ("description", "missions")}),
        (
            "Critères d'éligibilité",
            {
                "fields": (
                    "niveau_etudes_min",
                    "specialites_acceptees",
                    "experience_min_annees",
                    "experience_max_annees",
                    "autres_criteres",
                )
            },
        ),
        ("Pièces obligatoires (KAN-42)", {"fields": ("pieces_obligatoires",)}),
        ("Synchronisation", {"fields": SOURCE_FIELDS}),
    )


@admin.register(OffreEmploi)
class OffreEmploiAdmin(PiecesObligatoiresAuditMixin, admin.ModelAdmin):
    list_display = ("intitule", "organisation", "ville", "type_contrat", "statut")
    list_filter = ("statut", "type_contrat", "fonction")
    search_fields = ("intitule",)
    readonly_fields = SOURCE_FIELDS


@admin.register(Candidat)
class CandidatAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    list_display = ("nom", "prenom", "email", "telephone", "ville")
    search_fields = ("nom", "prenom", "email", "telephone", "cin")
    readonly_fields = (
        "email_normalise",
        "telephone_normalise",
        "nom_normalise",
        "cle_identite",
        *SOURCE_FIELDS,
    )


class PieceInline(admin.TabularInline):
    model = Piece
    fields = ("type", "nom_fichier", "taille", "sha256")
    readonly_fields = ("sha256",)
    extra = 0


class DetailsSpontaneeInline(admin.StackedInline):
    """Souhaits d'une candidature spontanée (KAN-39), importés du site : lecture seule."""

    model = DetailsSpontanee
    extra = 0
    max_num = 0
    can_delete = False
    readonly_fields = (
        "fonction_souhaitee",
        "fonction_souhaitee_brute",
        "secteur_souhaite",
        "lieu_souhaite",
        "disponibilite",
        "message",
        "message_tronque",
        "consentement_vivier",
        "consentement_le",
        "mise_a_jour_source_le",
    )
    fields = readonly_fields


@admin.register(Candidature)
class CandidatureAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    list_display = ("candidat", "type", "cible_display", "date_candidature", "statut")
    list_filter = ("type", "statut", "concours")
    search_fields = ("candidat__nom", "candidat__prenom", "candidat__email")
    raw_id_fields = ("candidat", "poste_concours", "offre")
    # Le type est fixé à la création et ne change jamais (KAN-39, règles 3 et 14).
    readonly_fields = ("type", "concours", *SOURCE_FIELDS)
    inlines = [DetailsSpontaneeInline, PieceInline]

    @admin.display(description="Poste / offre")
    def cible_display(self, obj):
        return obj.cible_libelle


@admin.register(Piece)
class PieceAdmin(ConsultationLoggingAdminMixin, admin.ModelAdmin):
    list_display = ("nom_fichier", "type", "candidature", "taille")
    list_filter = ("type",)
    search_fields = ("nom_fichier", "sha256")
    raw_id_fields = ("candidature",)
    readonly_fields = SOURCE_FIELDS
