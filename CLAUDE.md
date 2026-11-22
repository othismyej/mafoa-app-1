# Mafoa AI — Brief projet pour Claude Code

> À placer à la racine du dépôt sous le nom `CLAUDE.md` : Claude Code le lit automatiquement à chaque session.

## 1. Contexte

**Mafoa Management** est un cabinet RH basé à Marrakech (site : https://mafoamanagement.com). Trois métiers :

- **Conseil en management** : audit RH, études organisationnelles, stratégie RH.
- **Formation professionnelle** : développement des compétences, formation sur mesure, e-learning.
- **Recrutement** : adéquation poste / profil, évaluation des compétences, chasse de tête.

Activité différenciante : **e-Concours pour des entités publiques** (ex. Sociétés Régionales Multiservices — SRM Laâyoune–Sakia El Hamra, Souss-Massa, Tanger-Tétouan-Al Hoceima). Caractéristiques : gros volumes de candidatures, critères d'éligibilité stricts (diplôme Bac+5, spécialité, années d'expérience), plusieurs étapes publiées (présélection, liste des convoqués à l'oral, résultats), exigence forte de traçabilité en cas de recours.

## 2. Objectif de l'application

Une application web **interne**, réservée au **top management de Mafoa**, qui :

1. se connecte en temps réel aux données du site (offres, concours, candidatures, pièces jointes) ;
2. utilise l'IA pour accélérer le traitement des dossiers et évaluer l'adéquation profil / besoin ;
3. garde un **humain dans la boucle** pour toute décision et une **trace complète** de chaque action.

### Périmètre du MVP (3 mois)

| # | Cas d'usage | Description |
|---|---|---|
| 1 | **Éligibilité automatique** (concours) | Comparer diplôme, spécialité et années d'expérience aux critères du poste. Résultat : éligible / non éligible / à vérifier, avec la raison critère par critère. |
| 2 | **Lecture des pièces, doublons, anomalies** | Extraire le contenu des diplômes, attestations et CV (PDF texte et scans). Signaler pièces manquantes, doublons de candidats, incohérences (dates, noms). |
| 3 | **Score sur grille** | Grille de notation configurable par concours (critères, pondérations). Chaque note est justifiée par un extrait du dossier. |
| 4 | **Listes, courriers, journal d'audit** | Générer listes (éligibles, convoqués, résultats) en PDF/Excel. Journaliser chaque action (qui, quoi, quand, avant/après). |
| 5 | **Analyse CV + score d'adéquation** (recrutement privé) | Structurer un CV, calculer un score d'adéquation avec une fiche de poste, lister points forts, écarts et questions d'entretien. |

### Hors MVP (vagues suivantes, ne pas développer maintenant)

- Vague 2 : recherche sémantique dans le vivier, assistant WhatsApp candidats (FR/AR/Darija), portail client en lecture seule, ouverture aux consultants.
- Vague 3 : copilote d'entretien (transcription + synthèse), veille appels d'offres et rédaction de réponses, données de marché (salaires, profils par région), ventes croisées formation.

Concevoir le modèle de données pour ne pas bloquer ces vagues (ex. embeddings ajoutables plus tard).

## 3. Ce que l'on sait du site existant

Analyse externe (à confirmer avec le développeur du site) :

- **Framework : très probablement Django (Python)**. Indices : URLs `/accounts/login/` et `/accounts/signup/` (pattern django-allauth), répertoires `/static/` et `/media/`.
- **Stockage des fichiers : DigitalOcean Spaces** (S3-compatible), bucket `mafoamanagement.fra1.digitaloceanspaces.com` (région Francfort). Les CV et pièces y sont probablement stockés (ex. `/media/concour_bundles/...`).
- **Base de données : inconnue**, probablement PostgreSQL ou MySQL hébergée chez DigitalOcean.
- **Pas WordPress** (aucune trace de `wp-content`).

Objets métier visibles sur le site :

- **Offre d'emploi** (`/job/<slug>-<id>/`) : intitulé, entreprise (parfois « Confidential Company »), ville, type de contrat (CDI…), mode de travail (présentiel…), expérience requise, niveau d'études (Bac+2, Bac+3, Bac+5), secteur, fonction, nombre de postes, salaire.
- **Concours** (`/e-concours/<slug>/`) : entité organisatrice, nombre total de postes, liste de postes.
- **Poste de concours** (`/concour/<slug>-<id>/`) : intitulé, secteur (ex. « Service public / Administration »), fonction, niveau d'études, nombre de postes, mode de travail, date de publication, date limite, description, missions, étapes (ex. « Liste des candidats convoqués pour l'oral » avec date), statut (« Postulation terminée »). Règle observée : **un candidat ne peut postuler qu'à un seul poste par concours**.
- **Filtres de fonction** : Directeur, Chef de département, Chef de division, Chef de service, Responsable, Ingénieur, Architecte, Technicien, Agent, Ouvrier, Ingénieur / Cadre.
- **Comptes candidats** : inscription et connexion sur le site.

## 4. Architecture cible

### Stack

- **Backend** : Python 3.12, Django 5.x (même écosystème que le site).
- **Base de l'app** : PostgreSQL 16 (avec l'extension `pgvector` prévue pour la vague 2).
- **Tâches asynchrones** : Celery + Redis (lecture de documents et appels IA en arrière-plan).
- **Front** : rendu serveur Django + HTMX + Tailwind CSS. Pas de SPA pour le MVP : outil interne, simplicité de maintenance.
- **IA** : API Claude (SDK `anthropic`) pour extraction structurée, éligibilité assistée et scoring. Sorties en JSON validées par des schémas (Pydantic). Modèle configurable par variable d'environnement.
- **Lecture de documents** : `pypdf` / `pdfplumber` pour les PDF texte ; vision Claude pour les scans et images.
- **Fichiers** : lecture des pièces sur DigitalOcean Spaces via `boto3` (clé en **lecture seule**).
- **Exports** : `openpyxl` (Excel), WeasyPrint (PDF).
- **Qualité** : `pytest` + `pytest-django`, `ruff` (lint + format), GitHub Actions.
- **Dev local** : `docker compose` (app, PostgreSQL, Redis, worker Celery).

### Intégration avec le site (temps réel)

Couche d'abstraction obligatoire : `integrations/site_source/` avec une interface commune (`SiteSource`) et plusieurs implémentations, pour que le reste de l'app ne dépende pas de la manière dont on lit le site.

1. **`FixtureSource`** (dès maintenant) : données fictives réalistes (concours SRM fictifs, offres, candidats, pièces PDF générées). Aucune donnée réelle dans le dépôt.
2. **`DatabaseSource`** (dès que l'accès est obtenu) : connexion **en lecture seule** à la base du site, déclarée comme seconde base Django (`DATABASES["site"]`), modèles non gérés (`managed = False`) générés via `inspectdb`, router qui interdit toute écriture et toute migration sur `site`.
3. **`ApiSource`** (cible) : API REST exposée par le site (Django REST Framework), authentifiée par token, en lecture seule.
4. **Webhooks** (cible, temps réel) : le site envoie un POST signé HMAC-SHA256 à `/integrations/webhooks/site/` à chaque nouvelle candidature ou pièce. L'app vérifie la signature, l'horodatage (anti-rejeu) et déclenche le traitement Celery. Synchronisation périodique de rattrapage (toutes les 15 min) au cas où un webhook est perdu.

Règles : la base du site n'est **jamais** accessible depuis le navigateur ; l'app n'écrit **jamais** dans la base du site ; les fichiers restent dans Spaces et sont lus à la demande (pas de copie permanente, sauf texte extrait).

### Modèle de données de l'app (première version)

- `Organisation` (entité cliente : SRM, entreprise privée)
- `Concours` → `PosteConcours` (critères d'éligibilité structurés : niveau d'études min., spécialités acceptées, expérience min./max. en années, autres)
- `OffreEmploi` (recrutement privé, fiche de poste)
- `GrilleEvaluation` → `Critere` (libellé, pondération, barème, consignes pour l'IA)
- `Candidat` (identité, contacts ; clé de rapprochement pour les doublons : email, téléphone, nom + date de naissance normalisés)
- `Candidature` (candidat × poste ; statut : reçue, en analyse, éligible, non éligible, à vérifier, présélectionnée, convoquée, retenue, rejetée)
- `Piece` (type : CV, diplôme, attestation de travail, lettre, autre ; référence vers le fichier Spaces ; empreinte SHA-256)
- `Extraction` (texte extrait, données structurées JSON, modèle et version de prompt utilisés, confiance)
- `VerificationEligibilite` (résultat global + détail par critère + justification + sources)
- `Score` (note par critère + justification + extrait cité ; note globale)
- `Anomalie` (type, gravité, description, résolue ou non)
- `Decision` (**toujours humaine** : auteur, décision, motif, date)
- `JournalAudit` (acteur, action, objet, avant/après, horodatage, IP) — en ajout seul, jamais modifié ni supprimé
- `SourceRef` sur les objets importés (identifiant côté site + date de dernière synchronisation)

Le calcul des **années d'expérience** et la comparaison des **niveaux d'études** doivent être **déterministes** (code Python testé), l'IA servant à extraire les dates et intitulés depuis les documents. L'IA ne décide jamais seule d'une non-éligibilité : tout cas incertain passe en « à vérifier ».

## 5. Accès et sécurité

- **Aucune inscription publique.** Comptes créés uniquement sur invitation par un administrateur.
- **Double authentification obligatoire** (TOTP, ex. `django-allauth` MFA).
- **Rôles** : `admin` (direction générale : tout, y compris gestion des comptes) ; `direction` (associés et directeurs : concours, offres, candidatures, décisions, exports). Prévoir `consultant` et `client` pour la vague 2 sans les activer.
- Permissions vérifiées côté serveur sur chaque vue ; tests de permissions obligatoires.
- Sessions courtes, cookies sécurisés, CSRF, en-têtes de sécurité (HSTS, CSP), limitation des tentatives de connexion.
- Secrets uniquement en variables d'environnement (`.env` jamais commité, `.env.example` fourni).
- Journal de chaque connexion et de chaque consultation de dossier.

## 6. Conformité et IA responsable

- **Loi marocaine 09-08 / CNDP** : données personnelles de candidats. Finalité limitée au recrutement, durée de conservation configurable avec purge automatique, droit d'accès et de suppression.
- **Point à valider avant la mise en production avec des données réelles** : transfert de données hors du Maroc (hébergement à Francfort, appels à l'API Claude). Prévoir une option de **pseudonymisation** avant envoi à l'IA (remplacer nom, CIN, téléphone, email par des jetons) et la rendre activable par configuration.
- **Humain dans la boucle** : aucune élimination automatique ; l'IA classe, justifie et signale.
- **Explicabilité** : chaque score et chaque verdict affiche ses raisons et les extraits sources.
- **Biais** : ne jamais utiliser sexe, âge, région, photo, situation familiale comme critères ; tableau de bord de contrôle des écarts par sexe et région (vague 2, prévoir les données).
- **Traçabilité IA** : enregistrer pour chaque appel le modèle, la version du prompt, l'entrée (pseudonymisée), la sortie et la durée. Prompts versionnés dans le dépôt (`ai/prompts/`).

## 7. Interface (français, prête pour l'arabe)

Écrans du MVP :

1. **Tableau de bord** : concours et offres en cours, volumes, dossiers à vérifier, échéances.
2. **Concours** → postes → liste des candidatures avec filtres (statut, éligibilité, score, anomalies) et actions en lot.
3. **Fiche candidature** : pièces (visionneuse), données extraites, verdict d'éligibilité critère par critère, score détaillé, anomalies, historique, bouton de décision avec motif obligatoire.
4. **Grille d'évaluation** : création et édition des critères et pondérations.
5. **Exports** : listes PDF/Excel aux formats attendus par les clients publics.
6. **Journal d'audit** (admin).
7. **Administration des comptes** (admin).

Interface sobre et professionnelle, i18n Django activée (`fr` par défaut, `ar` prévu avec support RTL).

## 8. Structure du dépôt (proposée)

```
mafoa-ai/
├── CLAUDE.md
├── README.md
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── .github/workflows/ci.yml
├── config/                 # settings (base, dev, prod), urls, celery
├── apps/
│   ├── accounts/           # invitations, rôles, MFA
│   ├── recruitment/        # concours, postes, offres, candidats, candidatures, pièces
│   ├── evaluation/         # grilles, éligibilité, scores, décisions
│   ├── documents/          # extraction de texte, lecture de pièces
│   ├── exports/            # listes PDF/Excel
│   └── audit/              # journal d'audit
├── integrations/
│   └── site_source/        # SiteSource, FixtureSource, DatabaseSource, ApiSource, webhooks
├── ai/
│   ├── client.py           # wrapper API Claude (retries, journalisation, pseudonymisation)
│   ├── schemas.py          # schémas Pydantic des sorties
│   └── prompts/            # prompts versionnés
├── templates/
├── static/
└── tests/
```

## 9. Conventions

- Code et noms techniques en anglais ; interface, libellés et documentation fonctionnelle en français.
- Commits au format Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Une branche par fonctionnalité, PR vers `main`, CI verte obligatoire.
- Tests pour toute règle métier (éligibilité, calcul d'expérience, doublons, permissions).
- Aucune donnée personnelle réelle dans le dépôt, les tests ou les fixtures.
- Les appels IA sont simulés (mock) dans les tests.

## 10. Informations à obtenir du développeur du site

- Confirmation du framework (Django ?) et de la version.
- Type et hébergement de la base (PostgreSQL / MySQL, DigitalOcean Managed Database ?).
- Fichiers `models.py` des candidatures, concours, offres et comptes (ou accès au dépôt Git du site).
- Organisation des fichiers dans le bucket Spaces (chemins des CV et pièces).
- Possibilité de créer un utilisateur base de données en lecture seule et une clé Spaces en lecture seule.
- Accord pour ajouter au site une API en lecture seule et un webhook à la création d'une candidature.
