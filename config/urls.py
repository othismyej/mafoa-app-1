from allauth.account.decorators import secure_admin_login
from django.contrib import admin
from django.urls import include, path

from apps.accounts import views as account_views

admin.site.site_header = "Mafoa AI — Administration"
admin.site.site_title = "Mafoa AI"
admin.site.index_title = "Administration"
# La connexion à l'admin passe par allauth : double authentification obligatoire.
admin.site.login = secure_admin_login(admin.site.login)

urlpatterns = [
    path("", account_views.dashboard, name="dashboard"),
    path("invitation/<str:token>/", account_views.accept_invitation, name="accept_invitation"),
    path("", include("apps.recruitment.urls")),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
]
