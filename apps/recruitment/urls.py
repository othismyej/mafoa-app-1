from django.urls import path

from . import anomalie_views, lot_views, offre_views, spontanee_views, views

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
    path("offres/", offre_views.offres_liste, name="offres_liste"),
    path("offres/<int:pk>/", offre_views.offre_detail, name="offre_detail"),
    path("a-verifier/", offre_views.a_verifier, name="a_verifier"),
    path(
        "candidatures-spontanees/",
        spontanee_views.spontanees_liste,
        name="spontanees_liste",
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
    path(
        "candidatures/<int:pk>/anomalies/detecter/",
        anomalie_views.anomalies_detecter,
        name="anomalies_detecter",
    ),
    path(
        "candidatures/<int:pk>/anomalies/<int:anomalie_pk>/resoudre/",
        anomalie_views.anomalie_resoudre,
        name="anomalie_resoudre",
    ),
    path(
        "candidatures/<int:pk>/anomalies/<int:anomalie_pk>/rouvrir/",
        anomalie_views.anomalie_rouvrir,
        name="anomalie_rouvrir",
    ),
]
