from django.urls import path

from . import views

app_name = "recruitment"

urlpatterns = [
    path("concours/<int:pk>/", views.concours_detail, name="concours_detail"),
]
