from django.urls import path

from . import lot_views, views

app_name = "recruitment"

urlpatterns = [
    path("concours/<int:pk>/", views.concours_detail, name="concours_detail"),
    path(
        "concours/<int:concours_pk>/postes/<int:poste_pk>/candidatures/",
        views.poste_candidatures,
        name="poste_candidatures",
    ),
    path(
        "concours/<int:concours_pk>/postes/<int:poste_pk>/candidatures/lot/",
        lot_views.lot_recapitulatif,
        name="lot_recapitulatif",
    ),
    path(
        "concours/<int:concours_pk>/postes/<int:poste_pk>/candidatures/lot/executer/",
        lot_views.lot_executer,
        name="lot_executer",
    ),
    path("candidatures/<int:pk>/", views.candidature_detail, name="candidature_detail"),
    path(
        "candidatures/<int:pk>/pieces/<int:piece_pk>/",
        views.piece_visionneuse,
        name="piece_visionneuse",
    ),
    path(
        "candidatures/<int:pk>/pieces/<int:piece_pk>/contenu/",
        views.piece_contenu,
        name="piece_contenu",
    ),
    path(
        "candidatures/<int:pk>/decision/",
        views.decision_enregistrer,
        name="decision_enregistrer",
    ),
]
