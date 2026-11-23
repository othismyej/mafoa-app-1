from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("audit/", views.journal_liste, name="journal_liste"),
    path("audit/<int:pk>/", views.journal_detail, name="journal_detail"),
    path("audit/export/<str:fmt>/", views.journal_export, name="journal_export"),
    path(
        "candidatures/<int:pk>/consultations/",
        views.consultations_dossier,
        name="consultations_dossier",
    ),
]
