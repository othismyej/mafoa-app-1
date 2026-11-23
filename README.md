# Mafoa AI

Application web **interne** de Mafoa Management (top management uniquement) pour accélérer le
traitement des concours publics et des recrutements privés : éligibilité, lecture des pièces,
scoring, listes et journal d'audit. L'IA classe, justifie et signale ; **toute décision reste
humaine** et chaque action est tracée.

Le brief complet du projet est dans [`CLAUDE.md`](CLAUDE.md).

## État actuel (bootstrap)

| Élément | État |
|---|---|
| Projet Django 5.2, settings `dev` / `test` / `prod`, ruff, pytest | ✅ |
| Docker compose : app, PostgreSQL 16 (+ pgvector), Redis, worker Celery | ✅ |
| Comptes : pas d'inscription publique, invitation par un admin, TOTP obligatoire, rôles `admin` et `direction` | ✅ |
| Modèles du §4 (concours, postes, offres, candidats, candidatures, pièces, extractions, grilles, scores, anomalies, décisions, audit) + admin | ✅ |
| `SiteSource` + `FixtureSource` (2 concours, 10 postes, 2 offres, 52 candidats, 200 pièces PDF fictives) | ✅ |
| Moteur d'éligibilité déterministe (niveau d'études, spécialité, expérience) + tests | ✅ |
| Journal d'audit en ajout seul (modèle + admin + trigger PostgreSQL) | ✅ |
| Wrapper IA (sortie Pydantic, pseudonymisation, traçabilité), simulé dans les tests | ✅ squelette |
| Écrans métier (concours, fiche candidature, grilles, exports) | ⏳ prochaines étapes |
| `DatabaseSource`, `ApiSource`, webhooks | ⏳ en attente des accès au site |

## Installation

### Avec Docker (recommandé)

Prérequis : Docker et Docker Compose.

```bash
cp .env.example .env
```

Renseignez au minimum `DJANGO_SECRET_KEY` dans `.env` :

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Puis lancez la stack. Les migrations sont appliquées automatiquement au démarrage de `web` :

```bash
docker compose up --build
```

L'application est disponible sur <http://localhost:8000>.

Créez le premier administrateur :

```bash
docker compose exec web python manage.py createsuperuser
```

Chargez les données fictives et calculez l'éligibilité :

```bash
docker compose exec web python manage.py sync_site --evaluate
```

Lancez les tests :

```bash
docker compose exec web pytest
```

### Sans Docker

Prérequis : Python 3.12+, [uv](https://docs.astral.sh/uv/), PostgreSQL 16 et Redis.

```bash
cp .env.example .env
```

```bash
uv sync
```

```bash
uv run python manage.py migrate
```

```bash
uv run python manage.py runserver
```

Dans un second terminal, le worker Celery :

```bash
uv run celery -A config worker --loglevel=info
```

## Première connexion

1. Connectez-vous avec le compte créé par `createsuperuser`.
2. Activez la double authentification. En production, elle est toujours exigée avant toute autre
   page. En local, elle est facultative par défaut (`MFA_REQUIRED=false` dans `.env`) : mettez
   `MFA_REQUIRED=true` pour retrouver le comportement de production.
   Scannez le QR code avec une application TOTP (Google Authenticator, Microsoft Authenticator,
   1Password…). Conservez les codes de récupération.
3. Pour inviter un associé ou un directeur : **Administration → Invitations → Ajouter**. Le lien
   d'invitation (valable 72 h, usage unique) s'affiche après l'enregistrement : transmettez-le à la
   personne invitée. Elle choisit son mot de passe, puis configure son TOTP à la première connexion.

Il n'existe aucune inscription publique. Un compte ne se supprime pas, il se désactive.

## Rôles

| Rôle | Accès |
|---|---|
| `admin` | Direction générale : tout, y compris l'administration Django, les comptes et le journal d'audit. |
| `direction` | Associés et directeurs : concours, offres, candidatures, décisions, exports. |
| `consultant`, `client` | Déclarés pour la vague 2, non attribuables. |

Les permissions sont vérifiées côté serveur (`apps/accounts/permissions.py`).

## Architecture

```
config/                 settings (base, dev, test, prod), urls, celery
apps/
  accounts/             utilisateur, rôles, invitations, middlewares (connexion + TOTP obligatoires)
  recruitment/          organisations, concours, postes, offres, candidats, candidatures, pièces
  evaluation/           moteur d'éligibilité, grilles, scores, anomalies, décisions
  documents/            extractions (texte et données structurées)
  exports/              listes PDF/Excel (à venir)
  audit/                journal d'audit en ajout seul
integrations/site_source/
  base.py               interface SiteSource
  fixture_source.py     données fictives déterministes + PDF générés
  sync.py               import idempotent vers les modèles (clé : source_id)
  database_source.py    lecture seule de la base du site (à venir)
  api_source.py         API REST du site (à venir)
ai/
  client.py             wrapper API Claude (sortie Pydantic, pseudonymisation, journal AppelIA)
  schemas.py            schémas des sorties IA
  prompts/              prompts versionnés (<nom>.v<N>.md)
templates/  static/  tests/
```

### Moteur d'éligibilité

`apps/evaluation/eligibility/` contient du code pur, sans IA ni base de données :

- **Niveau d'études** : libellés normalisés (« Master », « Ingénieur d'État », « Bac+5 »…) puis
  comparés à un ordre Bac < Bac+2 < Bac+3 < Bac+4 < Bac+5 < Doctorat.
- **Spécialité** : correspondance exacte, table d'équivalences ou inclusion des mots.
  Sans correspondance, le résultat est « à vérifier », jamais « non éligible ».
- **Expérience** : périodes fusionnées (aucun double comptage), poste en cours arrêté à la date
  limite, jours inclus / 365,25.
- **Verdict** : « non éligible » seulement si un critère est certainement non rempli avec des
  données complètes et cohérentes. Toute donnée manquante, illisible ou incohérente donne
  « à vérifier ».

Le moteur classe les candidatures (éligible / non éligible / à vérifier). Il ne remplace jamais une
décision humaine : `Decision` exige un auteur et un motif, et n'est jamais modifiée.

### Données fictives

`FixtureSource` génère toujours les mêmes données pour une graine donnée (`SITE_SOURCE_FIXTURE_SEED`).
Les données sont entièrement inventées : organisations « Région Fictive », emails `example.com`,
téléphones `0600…`, CIN `ZZ…`. Elles contiennent volontairement des cas piégés :

- niveau ou expérience insuffisants ;
- spécialités hors liste ;
- dates manquantes ou incohérentes ;
- pièces manquantes ;
- doublons de candidats et CV identique partagé ;
- 6 candidatures spontanées (`SP-…`, KAN-39) : souhaits variés ou absents, fonction hors
  référentiel, consentement oui / non / inconnu, CV seul, lettre seule, un candidat ayant aussi
  une candidature de concours et un candidat avec deux dépôts. Elles sont ajoutées après les
  données existantes, sans tirage aléatoire : les autres données et empreintes sont inchangées.

### Visionneuse des pièces (KAN-12)

La fiche candidature affiche les pièces à la demande (`/candidatures/<id>/pieces/<id>/` pour le
fragment HTMX, `.../contenu/` pour les octets). Le contenu est lu via `SiteSource.read_piece`,
gardé en mémoire le temps de la requête et jamais copié (ni disque, ni cache, ni base) ; aucune
URL Spaces n'est transmise au navigateur. Type réel détecté sur les octets (PDF, PNG, JPEG
affichables ; le reste en téléchargement seulement), empreinte SHA-256 contrôlée (anomalie
`empreinte_differente` si elle diffère), chaque lecture journalisée sans nom ni clé de stockage.
Seule la vue de contenu déroge au cadrage global (`X-Frame-Options: SAMEORIGIN`,
`frame-ancestors 'self'`) pour s'afficher dans l'`<iframe>` de la fiche.

Réglages : `PIECE_VIEWER_MAX_BYTES` (défaut 20 Mo) et `PIECE_SOURCE_TIMEOUT` (défaut 10 s).

### Actions en lot (KAN-15)

Sur la liste des candidatures d'un poste, la direction peut sélectionner des candidatures
(cases de la page, ou « tous les résultats du filtre ») et leur appliquer **une** action :
relancer le calcul d'éligibilité, ou enregistrer une même décision d'avancement (éligible,
présélectionnée, convoquée) avec un motif commun. « Non éligible », « rejetée », « retenue » et
les révisions restent individuelles (fiche). Un récapitulatif signé (30 minutes) fige la
sélection avant exécution ; chaque candidature reçoit sa propre `Decision` (champ `lot_id`), dans
sa propre transaction, et le journal porte deux entrées `lot` (lancement, fin) sans donnée
personnelle. Paramètres : `LOT_TAILLE_MAX` (500) et `LOT_ELIGIBILITE_SYNC_MAX` (50 ; au-delà,
la relance passe par la tâche Celery `evaluation.relancer_eligibilite_lot`). Code :
`apps/evaluation/lots.py`, `apps/recruitment/lot_views.py`.

### Candidatures spontanées — vivier (KAN-39)

Une candidature porte un **type explicite** (`concours`, `offre`, `spontanee`). Une candidature
spontanée n'a ni poste ni offre ; elle porte les souhaits du candidat (`DetailsSpontanee` :
fonction, secteur, lieu de travail souhaité, disponibilité, message, consentement au vivier).

- Écran `/candidatures-spontanees/` (entrée « Candidatures spontanées » du menu, admin et
  direction) : filtres par statut, fonction, secteur et lieu souhaités, période, présence de CV,
  anomalies ; recherche par nom ; aucune colonne ni filtre sur le sexe, l'âge, la région ou la
  ville de résidence. Logique serveur unique : `apps/recruitment/spontanees.py`.
- Fiche candidature : bloc « Souhaits du candidat », « Autres candidatures de ce candidat », date
  de fin de conservation ; pas de bloc éligibilité (`run_eligibility` refuse une candidature
  spontanée, `sync_site --evaluate` les ignore).
- Décisions humaines : reçue / en analyse → conservée au vivier ou rejetée ; conservée au vivier
  → rejetée ; rejetée → conservée au vivier (révision confirmée). Statut `vivier` réservé aux
  candidatures spontanées (contrainte de base).
- Conservation : `RETENTION_CANDIDATURE_SPONTANEE_MOIS` (défaut 24 mois). La purge effective
  relève de KAN-31 ; `spontanees.conservation_depassee()` liste les dossiers concernés.
- Contrat `SiteSource` : `CandidatureDTO.type`, `souhaits` (`SouhaitsDTO`),
  `date_mise_a_jour`. La correspondance avec le site est isolée dans
  `integrations/site_source/spontanees.py` et **reste à confirmer avec le code source du site**
  (questions QS1 à QS7 de la spec). Un DTO incohérent est refusé et compté ; un changement de
  type à la source est compté comme conflit sans modifier le dossier ; l'entrée d'audit `sync`
  porte les compteurs par type.

**Note de version (migration `recruitment.0003`)** : la contrainte `candidature_poste_xor_offre`
est remplacée par `candidature_type_cible` et `candidature_statut_selon_type` ; le type des
candidatures existantes est renseigné par la migration (aucune perte). La migration inverse est
refusée tant qu'une candidature spontanée existe. Le code qui crée une `Candidature` sans poste
ni offre doit désormais fournir `type="spontanee"` explicitement.

### IA

Aucun appel réel n'est fait pour l'instant. Les tests utilisent un transport simulé.
Avec `AI_PSEUDONYMIZE=true` (valeur par défaut), nom, CIN, téléphone et email sont remplacés par
des jetons avant l'envoi. Chaque appel est enregistré dans `AppelIA` : modèle, version du prompt,
entrée pseudonymisée, sortie et durée.

## Interface et CSS

La charte graphique (palette, typographie, composants, accessibilité, RTL) est dans
[`docs/design/charte.md`](docs/design/charte.md). Les fragments réutilisables sont dans
`templates/components/`.

Le CSS est compilé localement avec Tailwind 3.4 et **commité** (`static/css/app.css`) : Docker,
la CI et `collectstatic` n'ont pas besoin de Node. Après avoir modifié un gabarit (nouvelles
classes), `frontend/app.css` ou `tailwind.config.js`, recompilez puis commitez le résultat :

```bash
npm install
npm run build:css   # ou npm run watch:css pendant le développement
```

Les polices Poppins (OFL 1.1) et Roboto (Apache 2.0) sont auto-hébergées dans `static/fonts/`
(woff2, sous-ensemble latin, licences incluses) ; `npm run fonts` les recopie depuis
`@fontsource`. Aucune ressource externe n'est chargée (CSP `'self'`).

## Qualité

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

```bash
uv run pytest
```

La CI GitHub Actions (`.github/workflows/ci.yml`) exécute ruff, la vérification des migrations
manquantes et pytest sur PostgreSQL 16.

## Règles

- Aucun secret dans le dépôt : tout passe par les variables d'environnement (`.env.example`).
- Aucune donnée personnelle réelle dans le dépôt, les tests ou les fixtures.
- Commits au format Conventional Commits ; une branche par fonctionnalité ; CI verte obligatoire.
