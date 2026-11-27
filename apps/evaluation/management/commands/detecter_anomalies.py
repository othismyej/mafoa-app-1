"""Détection des anomalies en masse (spec KAN-42, règle 22)."""

from django.core.management.base import BaseCommand, CommandError

from apps.evaluation.anomalies.service import detecter_anomalies
from apps.recruitment.models import Candidature


class Command(BaseCommand):
    help = (
        "Détecte les anomalies (doublons, pièces obligatoires, incohérences) sur un périmètre "
        "de candidatures. Ne modifie ni statut, ni verdict, ni décision."
    )

    def add_arguments(self, parser):
        groupe = parser.add_mutually_exclusive_group(required=True)
        groupe.add_argument("--concours", type=int, help="Identifiant d'un concours")
        groupe.add_argument("--poste", type=int, help="Identifiant d'un poste de concours")
        groupe.add_argument("--offre", type=int, help="Identifiant d'une offre d'emploi")
        groupe.add_argument(
            "--spontanees", action="store_true", help="Toutes les candidatures spontanées"
        )
        groupe.add_argument("--tout", action="store_true", help="Toutes les candidatures")

    def handle(self, *args, **options):
        qs = Candidature.objects.all()
        if options["concours"] is not None:
            qs = qs.filter(concours_id=options["concours"])
        elif options["poste"] is not None:
            qs = qs.filter(poste_concours_id=options["poste"])
        elif options["offre"] is not None:
            qs = qs.filter(offre_id=options["offre"])
        elif options["spontanees"]:
            qs = qs.filter(type=Candidature.Type.SPONTANEE)
        elif not options["tout"]:
            raise CommandError("Périmètre obligatoire.")
        ids = list(qs.order_by("pk").values_list("pk", flat=True))
        # --tout : chaque candidature est examinée en entier, la symétrie est assurée sans
        # réexaminer les comptes rapprochés.
        rapport = detecter_anomalies(ids, declencheur="commande", etendre=not options["tout"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Détection terminée : {rapport.examinees} candidature(s) examinée(s) en "
                f"{rapport.duree_s:.1f} s."
            )
        )
        self.stdout.write(f"  Anomalies créées : {rapport.total_creees}")
        for type_, par_gravite in sorted(rapport.creees.items()):
            detail = ", ".join(f"{g} : {n}" for g, n in sorted(par_gravite.items()))
            self.stdout.write(f"    {type_} — {detail}")
        self.stdout.write(
            f"  Marquées « cause disparue » : {rapport.cause_disparue} · marques retirées : "
            f"{rapport.marques_retirees}"
        )
        non_exec = ", ".join(f"{k} : {v}" for k, v in rapport.non_executes.items() if v)
        if non_exec:
            self.stdout.write(f"  Contrôles non exécutés — {non_exec}")
        if rapport.erreurs:
            self.stdout.write(
                self.style.WARNING(f"  Erreurs techniques : {len(rapport.erreurs)} candidature(s)")
            )
