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
            help=(
                "Lance le moteur d'éligibilité sur les candidatures de concours et d'offre après "
                "l'import (les candidatures spontanées, sans critères, sont ignorées)."
            ),
        )

    def handle(self, *args, **options):
        source = get_source(options["source"])
        report = sync_from_source(source)
        self.stdout.write(self.style.SUCCESS(f"Synchronisation « {source.name} » terminée."))
        for label, counts in (("Créés", report.created), ("Mis à jour", report.updated)):
            if counts:
                detail = ", ".join(f"{k} : {v}" for k, v in sorted(counts.items()))
                self.stdout.write(f"  {label} — {detail}")
        par_type = ", ".join(
            f"{t} : {v['created']} créée(s) / {v['updated']} mise(s) à jour"
            for t, v in report.candidatures_par_type.items()
        )
        self.stdout.write(f"  Candidatures par type — {par_type}")
        if report.refusees or report.conflits_type or report.souhaits_ignores:
            self.stdout.write(
                self.style.WARNING(
                    f"  Candidatures refusées : {len(report.refusees)} · conflits de type : "
                    f"{len(report.conflits_type)} · souhaits ignorés : "
                    f"{len(report.souhaits_ignores)}"
                )
            )
        detection = report.detection or {}
        if detection.get("rapport"):
            r = detection["rapport"]
            self.stdout.write(
                f"  Anomalies — créées : {r['creees_total']} · marquées « cause disparue » : "
                f"{r['cause_disparue']} · marques retirées : {r['marques_retirees']}"
            )
        elif detection.get("detection") == "non_lancee":
            self.stdout.write(
                self.style.WARNING(
                    "  Détection des anomalies non lancée (file de tâches indisponible) : "
                    "lancer « detecter_anomalies --tout »."
                )
            )
        elif detection.get("detection") == "lancee":
            self.stdout.write("  Détection des anomalies mise en file.")
        if options["evaluate"]:
            # KAN-39, règle 20 : seules les candidatures de concours et d'offre sont évaluées.
            qs = Candidature.objects.exclude(type=Candidature.Type.SPONTANEE).select_related(
                "poste_concours__concours", "offre", "candidat"
            )
            ignorees = Candidature.objects.filter(type=Candidature.Type.SPONTANEE).count()
            verdicts: dict[str, int] = {}
            for candidature in qs:
                v = run_eligibility(candidature)
                verdicts[v.resultat] = verdicts.get(v.resultat, 0) + 1
            self.stdout.write(self.style.SUCCESS(f"Éligibilité calculée : {verdicts}"))
            self.stdout.write(
                f"  Candidatures spontanées ignorées (aucun critère d'éligibilité) : {ignorees}"
            )
