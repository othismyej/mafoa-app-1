# Modèles privés du site relevés dans l'admin Django (KAN-25)

Source : formulaires **vides** « Ajouter » de l'admin du site (`/mad1009/`), transmis par le PO le
2026-11-27. Aucune donnée personnelle. Les noms de champs techniques sont déduits des libellés et
des URL ; à confirmer avec le code ou la base (`inspectdb`).

## `concours.ConcourApplication` — « Application » (candidature à un poste de concours)

URL admin : `/mad1009/concours/concourapplication/add/`

| Libellé admin | Type probable | Correspondance chez nous | Remarque |
|---|---|---|---|
| Candidat | FK (User ou CandidateProfile) | `Candidature.candidat` | à confirmer : FK vers `User` ou vers `CandidateProfile` |
| Concour | FK vers le **poste** de concours (`/concour/<slug>/`) | `Candidature.poste_concours` | le « concours » englobant semble être le *bundle* (`Concour bundles`) |
| Diplôme actuel | texte libre (aide : « DUT, Licence, Master, Ingénieur, Doctorat… ») | `donnees_declarees.formation[].intitule` / niveau | niveau à déduire du texte libre → table de correspondance |
| Autre diplôme préciser | texte | formation complémentaire | |
| Date de diplôme | date | `annee_obtention` | |
| Spécialité | FK vers un référentiel de spécialités | `formation[].specialite` | référentiel à récupérer (liste fermée) |
| Autre spécialité préciser | texte | spécialité libre | |
| Mention | liste de choix | — (nouveau) | à ne pas utiliser comme critère sauf si l'avis l'exige |
| Établissement | texte | `formation[].etablissement` | école/université, pas l'établissement organisateur |
| Ville, Pays | texte | — | **ville/pays du diplôme ou de résidence ?** à clarifier ; jamais un critère |
| Documents | **un seul** `FileField` | `Piece` | un fichier par candidature : PDF combiné ou archive ? à confirmer |
| URL signée | texte (« URL signée pour le téléchargement du document ») | — | ⚠ une URL signée stockée en base est réutilisable tant qu'elle n'expire pas : durée de validité à vérifier |
| Snapshot du profil | champ en lecture seule (probablement JSON), aide : « Snapshot du profil du candidat au moment de la candidature » | **source prioritaire** de `donnees_declarees` | confirmé par le PO (2026-11-27) : copie figée du profil au dépôt ; affiché « - » quand vide ; structure exacte à lire en base |
| Date de création / mise à jour | dates auto | `date_candidature`, `SourceRef.synced_at` | |

**Absents de la candidature** : aucun **statut** (présélection, convocation, résultat), aucune
**expérience détaillée** (employeur, dates). Les statuts et décisions vivent donc uniquement dans
notre application ; les années d'expérience datées ne peuvent venir que des **pièces** (CV,
attestations) → lecture par l'IA (KAN-41) indispensable pour le moteur d'éligibilité, ou du
niveau d'expérience déclaré du profil (tranche).

## `condidat.CandidateProfile` — « Candidate profile » (compte candidat / CVthèque)

URL admin : `/mad1009/condidat/candidateprofile/add/`

| Libellé admin | Type probable | Correspondance chez nous | Remarque |
|---|---|---|---|
| User | FK / 1-1 vers le compte allauth | `SourceRef` du `Candidat` | |
| Civilité | choix | — | ⚠ **donnée sensible (sexe)** : à ne **jamais** importer ni utiliser comme critère ; seulement pour le tableau de contrôle des écarts (vague 2), à part |
| Nom complet | texte unique | `Candidat.nom` / `prenom` | **un seul champ** : découpage nom/prénom non fiable → stocker tel quel |
| Adresse email | email | `Candidat.email` | clé de rapprochement |
| Numéro de téléphone | texte | `Candidat.telephone` | clé de rapprochement (normalisation) |
| CIN | texte | `Candidat.cin` | clé de rapprochement la plus forte (à proposer pour KAN-42) |
| Date de naissance | date | `Candidat.date_naissance` | jamais un critère (âge) |
| Profil LinkedIn | URL | — | non importé (minimisation) |
| Adresse | texte long | — | non importée (minimisation) |
| Ville, Pays | texte | `Candidat.ville` | jamais un critère |
| Région | texte | — | ⚠ jamais un critère ; tableau d'écarts vague 2 seulement |
| Poste actuel | texte | — | utile pour l'analyse d'adéquation (KAN-43) |
| Fonction actuelle | FK référentiel fonctions | — | |
| Secteur d'activité | FK référentiel secteurs | — | |
| Niveau d'expérience | choix (tranches) | expérience déclarée (tranche) | à convertir en années min/max (KAN-44) |
| Salaire actuel | choix (tranches) | — | non importé (minimisation), sauf besoin explicite |
| Pays de recherche d'emploi | texte | souhaits spontanée | |
| Niveau d'études | choix (codes du site) | niveau déclaré | table de correspondance (KAN-44) |
| Domaine d'études | texte | spécialité déclarée | |
| CV | `FileField` | `Piece` type CV (niveau profil) | CV du profil, distinct des documents de candidature |
| Photo de profil | image | — | ⚠ **jamais importée** (biais) |
| À propos de moi | texte long | message libre | peut contenir des données sensibles (KAN-40) |
| Profil complété | booléen | — | |

**Candidatures spontanées** : il n'y a pas de modèle dédié. La « CVthèque » correspond aux
`CandidateProfile` (avec CV). Le mapping de KAN-39 (`type = spontanee`, `DetailsSpontanee`) est
à adapter : une candidature spontanée = un profil complété avec CV, indépendamment de ses
candidatures à des postes ? → **décision PO à prendre**.

## Questions restantes (pour le PO ou le développeur)

1. « Documents » d'une candidature : un PDF unique qui regroupe CV, diplôme, attestations, ou une
   archive ? (impact : découpage et classement des pages par l'IA, KAN-41).
2. Structure exacte du « Snapshot du profil » (JSON) : à lire en base (`DatabaseSource`). Son existence est confirmée ; il garantit qu'on évalue le dossier tel qu'il était au moment du dépôt, même si le candidat modifie ensuite son profil.
3. Durée de validité de l'« URL signée » et politique de listing du bucket Spaces.
4. Référentiels : spécialités, mentions, niveaux d'études, niveaux d'expérience, fonctions,
   secteurs (listes fermées à récupérer).
5. Définition d'une candidature spontanée (profil CVthèque).
