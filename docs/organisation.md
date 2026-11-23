# Organisation du projet Mafoa AI

Ce document décrit comment on travaille : qui fait quoi, comment un besoin devient du code
fusionné dans `main`, et quand un travail est considéré comme terminé. Il s'applique à toutes
les sessions Claude Code et aux agents définis dans `.claude/agents/`.

## Outils

| Outil | Rôle | Lien |
|---|---|---|
| Jira | Backlog, priorités, suivi | <https://othmane-smyej.atlassian.net>, projet **Mafoa AI** (clé `KAN`) |
| Jira Product Discovery | Idées des vagues 2 et 3, avant spécification | projet `MDP` |
| GitHub | Code, PR, CI, revue automatique Codex | `othismyej/mafoa-app-1` |
| `docs/specs/` | Spécifications validées (une par ticket) | ce dépôt |

## Rôles

| Rôle | Qui | Responsabilités |
|---|---|---|
| **Product Owner** | Othmane | Priorise le backlog, valide les spécifications, répond aux questions ouvertes, décide des fusions dans `main`. |
| **Spécificateur** | agent `specificateur` | Transforme un ticket en user story testable : critères d'acceptation, règles métier, cas limites, impacts sécurité et conformité. Liste les questions ouvertes au lieu de supposer. |
| **Développeur** | session Claude Code principale | Plan technique, code, migrations, mise à jour du ticket Jira et de la PR. |
| **Testeur** | agent `testeur` | Écrit et exécute les tests d'acceptation à partir de la spécification (pas à partir du code), y compris les tests de permissions et d'écrans. |
| **Relecteur sécurité & conformité** | agent `relecteur-conformite` | Relit chaque PR contre les §5 et §6 de `CLAUDE.md` : données personnelles, CNDP, permissions, audit, secrets, biais, IA. Ne modifie pas le code. |

Rôles prévus plus tard, quand ils deviendront utiles :

- **Spécialiste IA** (prompts, schémas, jeux d'évaluation sur données fictives, coûts), à partir de l'epic E3.
- **Intégration site** (correspondance avec les modèles du site, `DatabaseSource`), à partir de l'epic E7.

## Statuts Jira

| Statut | Signification |
|---|---|
| **Idea** | Besoin identifié, pas encore spécifié. |
| **To Do** | Spécification rédigée et **validée par le Product Owner** (« Prêt »). |
| **In Progress** | En développement sur une branche. |
| **In Review** | PR ouverte : tests, relecture, CI, revue Codex. |
| **Done** | Fusionné dans `main`. |

## Parcours d'un ticket

1. **Idea → To Do** : le spécificateur rédige `docs/specs/KAN-XX-<slug>.md` à partir du ticket
   et de `CLAUDE.md`, en suivant `docs/specs/_modele.md`. Les questions ouvertes sont posées au
   Product Owner. Une fois la spec validée, les critères d'acceptation sont recopiés dans le
   ticket et celui-ci passe en **To Do**.
2. **To Do → In Progress** : le développeur crée la branche (voir conventions), passe le ticket
   en **In Progress**, implémente.
3. **Tests d'acceptation** : le testeur écrit les tests à partir de la spec, sans s'appuyer sur
   l'implémentation, puis les exécute. Tout critère non couvert ou en échec renvoie au développeur.
4. **In Review** : PR vers `main` (lien vers le ticket dans la description), ticket en
   **In Review**. Le relecteur sécurité & conformité rend son avis ; la CI et la revue Codex
   doivent être traitées (correction ou réponse argumentée sur chaque remarque).
5. **Done** : le Product Owner fusionne (ou le demande explicitement) ; le ticket passe en **Done**.

Petits correctifs techniques (typo, dépendance, CI) : on peut sauter l'étape 1, mais jamais les
étapes 3 à 5.

## Définition de « Prêt » (To Do)

- La story suit le modèle : contexte, user story, règles métier, critères d'acceptation numérotés.
- Chaque critère est vérifiable par un test automatisé ou une vérification explicite.
- Les rôles autorisés et les impacts audit / données personnelles sont précisés.
- Aucune question ouverte bloquante.
- Validée par le Product Owner.

## Définition de « Terminé » (Done)

- Tous les critères d'acceptation sont couverts par des tests qui passent.
- CI verte (`ruff check`, `ruff format --check`, migrations à jour, `pytest`).
- Avis favorable du relecteur sécurité & conformité ; remarques Codex traitées.
- Permissions vérifiées côté serveur et testées pour chaque nouvelle vue.
- Toute action métier ou consultation de dossier est inscrite au journal d'audit.
- Aucune donnée personnelle réelle ni aucun secret dans le dépôt, les tests ou les fixtures ;
  appels IA simulés dans les tests.
- Interface en français ; README et docs mis à jour si le comportement change.
- PR fusionnée dans `main`.

## Conventions

- **Branches** : `feat/KAN-12-fiche-candidature`, `fix/KAN-30-...`, `docs/KAN-16-...`,
  `chore/KAN-19-...`. Une branche par ticket.
- **Commits** : Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), avec la clé
  du ticket dans le corps du message (`Refs: KAN-12`).
- **PR** : titre au format Conventional Commits, lien vers le ticket Jira, résumé des
  changements, vérifications effectuées, points d'attention.
- **Specs** : `docs/specs/KAN-XX-<slug>.md`, versionnées avec le code qu'elles décrivent.

## Utiliser les agents

Depuis une session Claude Code, demander par exemple :

- « Utilise l'agent specificateur pour le ticket KAN-11. »
- « Utilise l'agent testeur sur la spec docs/specs/KAN-11-fiche-candidature.md. »
- « Utilise l'agent relecteur-conformite sur la branche courante. »

Les définitions des agents sont dans `.claude/agents/`. Les modifier comme du code : par PR.
