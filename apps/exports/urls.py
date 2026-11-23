from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path(
        "concours/<int:concours_pk>/postes/<int:poste_pk>/candidatures/export.xlsx",
        views.export_poste,
        name="poste_candidatures_xlsx",
    ),
    path("concours/<int:pk>/export.xlsx", views.export_concours, name="concours_xlsx"),
]
