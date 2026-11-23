from django.urls import path

from . import views

app_name = "recruitment"

urlpatterns = [
    path("concours/<int:pk>/", views.concours_detail, name="concours_detail"),
    path(
        "concours/<int:concours_pk>/postes/<int:poste_pk>/candidatures/",
        views.poste_candidatures,
        name="poste_candidatures",
    ),
    path("candidatures/<int:pk>/", views.candidature_detail, name="candidature_detail"),
]
