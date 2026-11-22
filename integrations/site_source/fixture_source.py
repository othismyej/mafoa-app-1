"""Source fictive : données réalistes mais entièrement inventées, générées de façon
déterministe (même graine = mêmes données, mêmes PDF, mêmes empreintes).

Aucune donnée réelle : organisations « Région Fictive », emails en example.com /
example.org (domaines réservés), téléphones en 0600…, CIN préfixées « ZZ ».

Cas volontairement piégés pour tester le traitement :
- candidats non éligibles (niveau, expérience), spécialités à apprécier ;
- dates manquantes ou incohérentes ;
- pièces manquantes (pas de diplôme, pas d'attestation) ;
- doublons de candidats (même personne, deux comptes) et pièce identique partagée.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cached_property

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from .base import SiteSource
from .dto import (
    CandidatDTO,
    CandidatureDTO,
    ConcoursDTO,
    OffreEmploiDTO,
    OrganisationDTO,
    PieceDTO,
    PosteConcoursDTO,
)

PRENOMS_F = [
    "Salma",
    "Imane",
    "Khadija",
    "Sara",
    "Meryem",
    "Hajar",
    "Nadia",
    "Loubna",
    "Asmae",
    "Ghizlane",
    "Houda",
    "Samira",
    "Yasmine",
    "Fatima-Zahra",
    "Kawtar",
    "Rim",
]
PRENOMS_M = [
    "Youssef",
    "Mehdi",
    "Omar",
    "Anas",
    "Hamza",
    "Rachid",
    "Karim",
    "Amine",
    "Ayoub",
    "Soufiane",
    "Hicham",
    "Adil",
    "Reda",
    "Othmane",
    "Ilyas",
    "Zakaria",
]
NOMS = [
    "Alaoui",
    "Bennani",
    "El Idrissi",
    "Tazi",
    "Berrada",
    "Chraibi",
    "El Amrani",
    "Lahlou",
    "Benjelloun",
    "Fassi",
    "Ouazzani",
    "Squalli",
    "Kettani",
    "Bouzidi",
    "Naciri",
    "Rami",
    "El Mansouri",
    "Haddad",
    "Zerouali",
    "Amrani",
    "Belkadi",
    "Sabri",
    "Ziani",
    "Tahiri",
]
VILLES = [
    ("Laâyoune", "Laâyoune-Sakia El Hamra"),
    ("Agadir", "Souss-Massa"),
    ("Tanger", "Tanger-Tétouan-Al Hoceima"),
    ("Marrakech", "Marrakech-Safi"),
    ("Casablanca", "Casablanca-Settat"),
    ("Rabat", "Rabat-Salé-Kénitra"),
    ("Fès", "Fès-Meknès"),
    ("Oujda", "Oriental"),
]
ECOLES = [
    "École Nationale Fictive d'Ingénieurs",
    "Université Fictive Hassan",
    "Institut Fictif de Gestion",
    "École Supérieure Fictive de Technologie",
    "Faculté Fictive de Droit",
]
EMPLOYEURS = [
    "Régie Fictive de Distribution",
    "Bureau d'études Atlas Fictif",
    "Groupe Fictif Industries",
    "Commune Fictive de Sidi Exemple",
    "Société Fictive des Eaux",
    "Cabinet Fictif Conseil",
    "Office Fictif de l'Électricité",
    "Banque Fictive Populaire",
]

LIBELLE_NIVEAU = {
    0: "Baccalauréat",
    2: "DUT",
    3: "Licence",
    4: "Maîtrise",
    5: "Master",
    8: "Doctorat",
}


@dataclass
class _Poste:
    ref: str
    intitule: str
    fonction: str
    secteur: str
    niveau: int | None
    specialites: list[str]
    exp_min: str | None
    exp_max: str | None = None
    autres: str = ""
    nombre: int = 1
    hors_specialite: list[str] = field(default_factory=lambda: ["Biologie", "Lettres modernes"])


CONCOURS_DEF = [
    {
        "id": "C-NORD-2026",
        "org": ("ORG-SRM-NORD", "Société Régionale Multiservices Région Fictive Nord", "Tanger"),
        "intitule": "Concours de recrutement 2026 — SRM Région Fictive Nord",
        "publication": date(2026, 9, 1),
        "limite": date(2026, 10, 15),
        "statut": "cloture",
        "postes": [
            _Poste(
                "N-01",
                "Directeur des systèmes d'information",
                "directeur",
                "Service public / Administration",
                5,
                ["Informatique", "Systèmes d'information"],
                "10",
            ),
            _Poste(
                "N-02",
                "Chef de division Eau potable",
                "chef_division",
                "Eau et assainissement",
                5,
                ["Hydraulique", "Génie civil"],
                "8",
            ),
            _Poste(
                "N-03",
                "Ingénieur distribution électrique",
                "ingenieur",
                "Électricité",
                5,
                ["Génie électrique"],
                "3",
                "10",
                nombre=3,
            ),
            _Poste(
                "N-04",
                "Chef de service Comptabilité",
                "chef_service",
                "Finance / Comptabilité",
                3,
                ["Finance", "Comptabilité"],
                "5",
            ),
            _Poste(
                "N-05",
                "Technicien maintenance réseaux",
                "technicien",
                "Électricité",
                2,
                ["Électrotechnique", "Électricité"],
                "2",
                nombre=4,
            ),
        ],
    },
    {
        "id": "C-SUD-2026",
        "org": ("ORG-SRM-SUD", "Société Régionale Multiservices Région Fictive Sud", "Agadir"),
        "intitule": "Concours de recrutement 2026 — SRM Région Fictive Sud",
        "publication": date(2026, 11, 2),
        "limite": date(2026, 12, 20),
        "statut": "ouvert",
        "postes": [
            _Poste(
                "S-01",
                "Chef de département Ressources humaines",
                "chef_departement",
                "Service public / Administration",
                5,
                ["Ressources humaines", "Gestion"],
                "8",
                autres="Maîtrise de l'arabe et du français (écrit et oral).",
            ),
            _Poste(
                "S-02",
                "Ingénieur génie civil",
                "ingenieur",
                "BTP",
                5,
                ["Génie civil"],
                "2",
                nombre=2,
            ),
            _Poste(
                "S-03",
                "Responsable des affaires juridiques",
                "responsable",
                "Service public / Administration",
                5,
                ["Droit"],
                "5",
            ),
            _Poste("S-04", "Architecte", "architecte", "BTP", 5, ["Architecture"], "3"),
            _Poste(
                "S-05",
                "Agent administratif",
                "agent",
                "Service public / Administration",
                2,
                [],
                None,
                nombre=5,
            ),
        ],
    },
]

OFFRES_DEF = [
    {
        "id": "J-1001",
        "intitule": "Responsable Ressources Humaines",
        "confidentiel": True,
        "ville": "Marrakech",
        "contrat": "CDI",
        "secteur": "Tourisme / Hôtellerie",
        "fonction": "responsable",
        "niveau": 5,
        "specialites": ["Ressources humaines"],
        "exp_min": "5",
        "salaire": "Selon profil",
        "competences": ["Paie", "Droit du travail marocain", "Recrutement", "GPEC"],
    },
    {
        "id": "J-1002",
        "intitule": "Développeur Python / Django",
        "confidentiel": False,
        "ville": "Casablanca",
        "contrat": "CDI",
        "secteur": "Informatique / Télécoms",
        "fonction": "ingenieur_cadre",
        "niveau": 5,
        "specialites": ["Informatique"],
        "exp_min": "2",
        "salaire": "15 000 – 20 000 MAD",
        "competences": ["Python", "Django", "PostgreSQL", "Docker"],
    },
]

# Répartition des scénarios de candidats (le reste est éligible).
SCENARIOS = [
    ("eligible", 0.55),
    ("niveau_insuffisant", 0.10),
    ("experience_insuffisante", 0.12),
    ("specialite_a_apprecier", 0.10),
    ("dates_manquantes", 0.08),
    ("dates_incoherentes", 0.05),
]


def _slug(text: str) -> str:
    from django.utils.text import slugify

    return slugify(text)[:200]


class FixtureSource(SiteSource):
    name = "fixture"

    def __init__(self, seed: int = 20260101, nb_candidats: int = 50):
        self.seed = seed
        self.nb_candidats = nb_candidats

    # --- Référentiel -----------------------------------------------------
    def list_organisations(self):
        orgs = [
            OrganisationDTO(c["org"][0], c["org"][1], "public", c["org"][2]) for c in CONCOURS_DEF
        ]
        orgs.append(OrganisationDTO("ORG-PRIVE-1", "Groupe Hôtelier Fictif", "prive", "Marrakech"))
        orgs.append(
            OrganisationDTO("ORG-PRIVE-2", "Éditeur Logiciel Fictif", "prive", "Casablanca")
        )
        return orgs

    def list_concours(self):
        return [
            ConcoursDTO(
                source_id=c["id"],
                organisation_source_id=c["org"][0],
                intitule=c["intitule"],
                slug=_slug(c["intitule"]),
                description=f"Recrutement de {sum(p.nombre for p in c['postes'])} cadres et agents "
                "(données fictives).",
                date_publication=c["publication"],
                date_limite=c["limite"],
                statut=c["statut"],
            )
            for c in CONCOURS_DEF
        ]

    def list_postes(self):
        result = []
        for c in CONCOURS_DEF:
            for p in c["postes"]:
                result.append(
                    PosteConcoursDTO(
                        source_id=f"P-{p.ref}",
                        concours_source_id=c["id"],
                        reference=p.ref,
                        intitule=p.intitule,
                        slug=_slug(f"{p.intitule}-{p.ref}"),
                        secteur=p.secteur,
                        fonction=p.fonction,
                        nombre_postes=p.nombre,
                        mode_travail="presentiel",
                        ville=c["org"][2],
                        description=f"Poste de {p.intitule.lower()} au sein de la SRM (fictif).",
                        missions="- Piloter les activités du périmètre\n- Assurer le reporting\n"
                        "- Veiller à la qualité du service",
                        date_publication=c["publication"],
                        date_limite=c["limite"],
                        niveau_etudes_min=p.niveau,
                        specialites_acceptees=list(p.specialites),
                        experience_min_annees=Decimal(p.exp_min) if p.exp_min else None,
                        experience_max_annees=Decimal(p.exp_max) if p.exp_max else None,
                        autres_criteres=p.autres,
                    )
                )
        return result

    def list_offres(self):
        return [
            OffreEmploiDTO(
                source_id=o["id"],
                organisation_source_id="ORG-PRIVE-1" if i == 0 else "ORG-PRIVE-2",
                intitule=o["intitule"],
                slug=_slug(f"{o['intitule']}-{o['id']}"),
                entreprise_confidentielle=o["confidentiel"],
                ville=o["ville"],
                type_contrat=o["contrat"],
                mode_travail="presentiel",
                secteur=o["secteur"],
                fonction=o["fonction"],
                nombre_postes=1,
                salaire=o["salaire"],
                fiche_de_poste=f"Nous recherchons un(e) {o['intitule']} (offre fictive).",
                competences=o["competences"],
                date_publication=date(2026, 11, 1),
                date_limite=date(2026, 12, 31),
                statut="ouverte",
                niveau_etudes_min=o["niveau"],
                specialites_acceptees=o["specialites"],
                experience_min_annees=Decimal(o["exp_min"]),
            )
            for i, o in enumerate(OFFRES_DEF)
        ]

    # --- Candidats et candidatures ----------------------------------------
    @cached_property
    def _dataset(self):
        rng = random.Random(self.seed)
        postes = [(c, p) for c in CONCOURS_DEF for p in c["postes"]]
        candidats: list[CandidatDTO] = []
        candidatures: list[CandidatureDTO] = []
        pieces: list[PieceDTO] = []
        self._pdf_specs: dict[str, dict] = {}

        def add_candidat(
            idx: int,
            nom: str,
            prenom: str,
            sexe: str,
            naissance: date,
            email: str,
            tel: str,
            ville: tuple[str, str],
        ) -> CandidatDTO:
            dto = CandidatDTO(
                source_id=f"U-{idx:04d}",
                nom=nom,
                prenom=prenom,
                email=email,
                telephone=tel,
                date_naissance=naissance,
                cin=f"ZZ{100000 + idx * 37:06d}",
                ville=ville[0],
                region=ville[1],
                sexe=sexe,
            )
            candidats.append(dto)
            return dto

        for i in range(1, self.nb_candidats + 1):
            sexe = rng.choice("FM")
            prenom = rng.choice(PRENOMS_F if sexe == "F" else PRENOMS_M)
            nom = rng.choice(NOMS)
            naissance = date(rng.randint(1972, 2000), rng.randint(1, 12), rng.randint(1, 28))
            email = f"{_slug(prenom)}.{_slug(nom)}{i}@example.com".replace("-", "")
            tel = f"06 00 {rng.randint(10, 99)} {rng.randint(10, 99)} {i:02d}"[:17]
            cand = add_candidat(i, nom, prenom, sexe, naissance, email, tel, rng.choice(VILLES))

            if i % 9 == 0:
                cible = ("offre", OFFRES_DEF[(i // 9) % 2])
            else:
                cible = ("poste", postes[(i * 7) % len(postes)])
            scenario = self._pick_scenario(rng)
            self._add_application(rng, cand, cible, scenario, candidatures, pieces, i)

        # Doublon 1 : même personne, second compte (email différent, téléphone formaté autrement),
        # postule à un autre poste du même concours.
        orig = candidats[2]
        dup = add_candidat(
            self.nb_candidats + 1,
            orig.nom.upper(),
            orig.prenom,
            orig.sexe,
            orig.date_naissance,
            f"{_slug(orig.prenom)}.pro@example.org",
            "+212 6" + orig.telephone[2:].replace(" ", ""),
            (orig.ville, orig.region),
        )
        orig_app = next(a for a in candidatures if a.candidat_source_id == orig.source_id)
        if orig_app.poste_source_id:
            same_concours = [
                (c, p)
                for c, p in postes
                if f"P-{p.ref}" != orig_app.poste_source_id
                and any(f"P-{q.ref}" == orig_app.poste_source_id for q in c["postes"])
            ]
            dup_cible = ("poste", same_concours[0])
        else:
            dup_cible = ("poste", postes[0])
        self._add_application(
            rng,
            dup,
            dup_cible,
            "eligible",
            candidatures,
            pieces,
            self.nb_candidats + 1,
            share_cv_with=orig_app.source_id,
        )

        # Doublon 2 : même email à la casse près, autre concours (inscription en double).
        orig2 = candidats[10]
        dup2 = add_candidat(
            self.nb_candidats + 2,
            orig2.nom,
            orig2.prenom,
            orig2.sexe,
            orig2.date_naissance,
            orig2.email.upper(),
            orig2.telephone,
            (orig2.ville, orig2.region),
        )
        self._add_application(
            rng,
            dup2,
            ("poste", postes[-1]),
            "eligible",
            candidatures,
            pieces,
            self.nb_candidats + 2,
        )
        return candidats, candidatures, pieces

    @staticmethod
    def _pick_scenario(rng: random.Random) -> str:
        x = rng.random()
        acc = 0.0
        for name, weight in SCENARIOS:
            acc += weight
            if x < acc:
                return name
        return "eligible"

    def _add_application(
        self,
        rng,
        cand,
        cible,
        scenario,
        candidatures,
        pieces,
        idx,
        share_cv_with: str | None = None,
    ):
        kind, obj = cible
        if kind == "poste":
            concours, poste = obj
            niveau_min, specialites = poste.niveau, poste.specialites
            exp_min = Decimal(poste.exp_min) if poste.exp_min else Decimal(0)
            exp_max = Decimal(poste.exp_max) if poste.exp_max else None
            reference = concours["limite"]
            hors_specialite = poste.hors_specialite
        else:
            niveau_min, specialites = obj["niveau"], obj["specialites"]
            exp_min, exp_max = Decimal(obj["exp_min"]), None
            reference = date(2026, 12, 31)
            hors_specialite = ["Biologie"]

        niveau = niveau_min if niveau_min is not None else rng.choice([2, 3])
        if scenario == "niveau_insuffisant" and niveau_min:
            niveau = {5: 3, 3: 2, 2: 0}.get(niveau_min, 0)
        specialite = rng.choice(specialites) if specialites else rng.choice(["Gestion", "Droit"])
        if scenario == "specialite_a_apprecier":
            specialite = rng.choice(hors_specialite)

        # Années d'expérience visées.
        if scenario == "experience_insuffisante" and exp_min > 0:
            years = float(exp_min) * rng.uniform(0.3, 0.8)
        else:
            top = float(exp_max) if exp_max else float(exp_min) + 8
            years = rng.uniform(float(exp_min) + 0.5, max(float(exp_min) + 1, top - 0.2))
        experiences = self._build_experiences(rng, reference, years)
        if scenario == "dates_manquantes" and experiences:
            experiences[0]["debut"] = None
        if scenario == "dates_incoherentes" and experiences:
            e = experiences[-1]
            if e["fin"]:
                e["debut"], e["fin"] = e["fin"], e["debut"]

        annee_diplome = reference.year - int(years) - rng.randint(1, 3)
        intitule = f"{LIBELLE_NIVEAU[niveau]} en {specialite}" if niveau else "Baccalauréat"
        declared = {
            "formation": [
                {
                    "intitule": intitule,
                    "niveau": {0: "Bac", 8: "Bac+8"}.get(niveau, f"Bac+{niveau}"),
                    "specialite": specialite if niveau else "",
                    "etablissement": rng.choice(ECOLES),
                    "annee_obtention": annee_diplome,
                }
            ],
            "experiences": experiences,
        }
        app_id = f"A-{idx:04d}"
        submitted = datetime.combine(
            reference - timedelta(days=rng.randint(2, 40)), datetime.min.time(), tzinfo=UTC
        ) + timedelta(hours=rng.randint(8, 22), minutes=rng.randint(0, 59))
        candidatures.append(
            CandidatureDTO(
                source_id=app_id,
                candidat_source_id=cand.source_id,
                date_candidature=submitted,
                donnees_declarees=declared,
                poste_source_id=f"P-{obj[1].ref}" if kind == "poste" else None,
                offre_source_id=obj["id"] if kind == "offre" else None,
            )
        )

        # Pièces. Quelques dossiers incomplets (déterministes).
        types = ["cv", "lettre"]
        if idx % 11 != 0:
            types.append("diplome")
        if experiences and idx % 13 != 0:
            types.append("attestation_travail")
        for t in types:
            key = f"fixture://candidatures/{app_id}/{t}.pdf"
            spec_key = key
            if t == "cv" and share_cv_with:
                spec_key = f"fixture://candidatures/{share_cv_with}/cv.pdf"
            self._pdf_specs[key] = {
                "type": t,
                "candidat": cand if spec_key == key else None,
                "same_as": spec_key if spec_key != key else None,
                "declared": declared,
                "cible": obj[1].intitule if kind == "poste" else obj["intitule"],
            }
            pieces.append(
                PieceDTO(
                    source_id=f"F-{app_id}-{t}",
                    candidature_source_id=app_id,
                    type=t,
                    nom_fichier=f"{t}_{_slug(cand.nom)}.pdf",
                    storage_key=key,
                    content_type="application/pdf",
                    recue_le=submitted,
                )
            )

    @staticmethod
    def _build_experiences(rng: random.Random, reference: date, years: float) -> list[dict]:
        """Découpe ``years`` années en 1 à 3 postes successifs se terminant à ``reference``."""
        if years <= 0.05:
            return []
        n = 1 if years < 3 else rng.randint(2, 3)
        total_days = int(years * 365.25)
        cuts = sorted(rng.sample(range(60, total_days - 60), n - 1)) if n > 1 else []
        bounds = [0, *cuts, total_days]
        start = reference - timedelta(days=total_days)
        experiences = []
        for k in range(n):
            debut = start + timedelta(days=bounds[k])
            fin = start + timedelta(days=bounds[k + 1] - 1)
            current = k == n - 1 and rng.random() < 0.5
            experiences.append(
                {
                    "employeur": rng.choice(EMPLOYEURS),
                    "poste": rng.choice(
                        [
                            "Chargé d'études",
                            "Ingénieur",
                            "Responsable",
                            "Cadre",
                            "Technicien",
                            "Assistant",
                        ]
                    ),
                    "debut": debut.isoformat(),
                    "fin": None if current else fin.isoformat(),
                }
            )
        return experiences

    def list_candidats(self, since=None):
        return list(self._dataset[0])

    def list_candidatures(self, since=None):
        return list(self._dataset[1])

    def list_pieces(self, since=None):
        return list(self._dataset[2])

    # --- Pièces PDF ------------------------------------------------------
    def read_piece(self, storage_key: str) -> bytes:
        self._dataset  # noqa: B018  (initialise self._pdf_specs)
        spec = self._pdf_specs.get(storage_key)
        if spec is None:
            raise FileNotFoundError(storage_key)
        if spec["same_as"]:
            return self.read_piece(spec["same_as"])
        return _render_pdf(spec)


def _render_pdf(spec: dict) -> bytes:
    """PDF texte déterministe (``invariant=1`` : pas d'horodatage ni d'identifiant aléatoire)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    cand: CandidatDTO = spec["candidat"]
    declared = spec["declared"]
    width, height = A4
    y = height - 2.5 * cm

    def line(text: str, size: int = 11, gap: float = 0.6):
        nonlocal y
        c.setFont("Helvetica", size)
        c.drawString(2 * cm, y, text)
        y -= gap * cm

    c.setTitle(f"{spec['type']} - document fictif")
    line("DOCUMENT FICTIF — GÉNÉRÉ POUR LES TESTS", 8)
    t = spec["type"]
    nom_complet = f"{cand.prenom} {cand.nom}"
    if t == "cv":
        line("CURRICULUM VITAE", 16, 1)
        line(nom_complet, 13)
        line(f"{cand.email} — {cand.telephone} — {cand.ville}")
        line("")
        line("FORMATION", 12)
        for f in declared["formation"]:
            line(f"{f['annee_obtention']} : {f['intitule']} — {f['etablissement']}")
        line("")
        line("EXPÉRIENCE PROFESSIONNELLE", 12)
        for e in declared["experiences"]:
            debut = e["debut"] or "date non précisée"
            fin = e["fin"] or "aujourd'hui"
            line(f"{debut} → {fin} : {e['poste']} — {e['employeur']}")
    elif t == "diplome":
        f = declared["formation"][0]
        line(f["etablissement"].upper(), 14, 1.2)
        line("DIPLÔME", 18, 1.2)
        line(f"Décerné à {nom_complet}, né(e) le {cand.date_naissance:%d/%m/%Y}")
        line(f"Intitulé : {f['intitule']}")
        line(f"Niveau : {f['niveau']} — Spécialité : {f['specialite'] or 'générale'}")
        line(f"Année d'obtention : {f['annee_obtention']}")
    elif t == "attestation_travail":
        line("ATTESTATION DE TRAVAIL", 16, 1.2)
        for e in declared["experiences"]:
            debut = e["debut"] or "(date non précisée)"
            fin = e["fin"] or "ce jour"
            line(f"{e['employeur']} atteste que {nom_complet}, CIN {cand.cin},")
            line(f"a occupé le poste de {e['poste']} du {debut} au {fin}.", gap=0.9)
    else:
        line("LETTRE DE MOTIVATION", 16, 1.2)
        line(f"{nom_complet}")
        line(f"Objet : candidature au poste « {spec['cible']} »")
        line("Madame, Monsieur, je vous prie de trouver ci-joint mon dossier de candidature.")
    c.showPage()
    c.save()
    return buf.getvalue()
