from django.apps import AppConfig


class RecruitmentConfig(AppConfig):
    name = "apps.recruitment"
    label = "recruitment"
    verbose_name = "Recrutement"

    def ready(self) -> None:
        from . import checks  # noqa: F401  (enregistre les contrôles système)
