from django.contrib import admin

from .models import Candidat, Candidature, Concours, OffreEmploi, Organisation, Piece, PosteConcours

SOURCE_FIELDS = ("source_id", "source_synced_at")


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
class ConcoursAdmin(admin.ModelAdmin):
    list_display = ("intitule", "organisation", "date_publication", "date_limite", "statut")
    list_filter = ("statut", "organisation")
    search_fields = ("intitule",)
    readonly_fields = SOURCE_FIELDS
    inlines = [PosteConcoursInline]


@admin.register(PosteConcours)
class PosteConcoursAdmin(admin.ModelAdmin):
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
        ("Synchronisation", {"fields": SOURCE_FIELDS}),
    )


@admin.register(OffreEmploi)
class OffreEmploiAdmin(admin.ModelAdmin):
    list_display = ("intitule", "organisation", "ville", "type_contrat", "statut")
    list_filter = ("statut", "type_contrat", "fonction")
    search_fields = ("intitule",)
    readonly_fields = SOURCE_FIELDS


@admin.register(Candidat)
class CandidatAdmin(admin.ModelAdmin):
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


@admin.register(Candidature)
class CandidatureAdmin(admin.ModelAdmin):
    list_display = ("candidat", "cible_display", "date_candidature", "statut")
    list_filter = ("statut", "concours")
    search_fields = ("candidat__nom", "candidat__prenom", "candidat__email")
    raw_id_fields = ("candidat", "poste_concours", "offre")
    readonly_fields = ("concours", *SOURCE_FIELDS)
    inlines = [PieceInline]

    @admin.display(description="Poste / offre")
    def cible_display(self, obj):
        return obj.cible


@admin.register(Piece)
class PieceAdmin(admin.ModelAdmin):
    list_display = ("nom_fichier", "type", "candidature", "taille")
    list_filter = ("type",)
    search_fields = ("nom_fichier", "sha256")
    raw_id_fields = ("candidature",)
    readonly_fields = SOURCE_FIELDS
