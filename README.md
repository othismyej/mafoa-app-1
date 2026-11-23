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
- doublons de candidats et CV identique partagé.

### IA

Aucun appel réel n'est fait pour l'instant. Les tests utilisent un transport simulé.
Avec `AI_PSEUDONYMIZE=true` (valeur par défaut), nom, CIN, téléphone et email sont remplacés par
des jetons avant l'envoi. Chaque appel est enregistré dans `AppelIA` : modèle, version du prompt,
entrée pseudonymisée, sortie et durée.

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
