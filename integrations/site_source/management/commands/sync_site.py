from django.core.management.base import BaseCommand

from apps.evaluation.services import run_eligibility
from apps.recruitment.models import Candidature
from integrations.site_source.base import get_source
from integrations.site_source.sync import sync_from_source


class Command(BaseCommand):
    help = "Synchronise les données du site (par défaut : source fictive)."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None, help="fixture | database | api")
        parser.add_argument(
            "--evaluate",
            action="store_true",
            help="Lance le moteur d'éligibilité sur toutes les candidatures après l'import.",
        )

    def handle(self, *args, **options):
        source = get_source(options["source"])
        report = sync_from_source(source)
        self.stdout.write(self.style.SUCCESS(f"Synchronisation « {source.name} » terminée."))
        for label, counts in (("Créés", report.created), ("Mis à jour", report.updated)):
            if counts:
                detail = ", ".join(f"{k} : {v}" for k, v in sorted(counts.items()))
                self.stdout.write(f"  {label} — {detail}")
        if options["evaluate"]:
            qs = Candidature.objects.select_related("poste_concours__concours", "offre", "candidat")
            verdicts: dict[str, int] = {}
            for candidature in qs:
                v = run_eligibility(candidature)
                verdicts[v.resultat] = verdicts.get(v.resultat, 0) + 1
            self.stdout.write(self.style.SUCCESS(f"Éligibilité calculée : {verdicts}"))
