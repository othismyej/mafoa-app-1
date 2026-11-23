# KAN-9 — Vue d'un concours : postes et répartition des candidatures

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-9
- **Epic** : E2 — Écrans concours et éligibilité
- **Statut** : brouillon (rédigée le 2026-11-23, en attente de validation par le Product Owner)

## Contexte

Les concours publics (ex. SRM) comptent plusieurs postes et, pour chacun, jusqu'à plusieurs
milliers de candidatures. La direction doit voir d'un coup d'œil, pour un concours donné, quels
postes concentrent les dossiers « à vérifier » afin de répartir le travail. C'est la première
étape de l'écran 2 du brief (`CLAUDE.md` §7 : « Concours → postes → liste des candidatures »).

Existant dans l'application (à réutiliser, ne pas recréer) :

- **Tableau de bord** (`apps/accounts/views.py::dashboard`, `templates/dashboard/index.html`) :
  affiche les compteurs globaux et les 10 concours les plus récents (intitulé, organisation,
  date limite, statut), **sans lien de navigation**.
- **Modèles** (`apps/recruitment/models.py`) :
  - `Concours` : `organisation`, `intitule`, `date_publication`, `date_limite`, `statut`
    (brouillon, ouvert, clôturé, en évaluation, terminé), propriété `nombre_postes_total`.
  - `PosteConcours` : `concours`, `reference`, `intitule`, `nombre_postes`, `date_limite`
    (facultative), propriété `date_reference` (date limite du poste, sinon du concours),
    tri par défaut `reference`, `intitule`.
  - `Candidature` : `poste_concours`, `concours` (dénormalisé), `statut` (9 valeurs, de « reçue »
    à « rejetée »).
- **Éligibilité** (`apps/evaluation/models.py`) : `VerificationEligibilite` (une ligne par
  exécution du moteur, champ `resultat` ∈ éligible / non éligible / à vérifier, `created_at`).
  Le service `run_eligibility` (`apps/evaluation/services.py`) met à jour le statut de la
  candidature tant qu'elle n'a pas dépassé le stade « à vérifier » ; au-delà (présélectionnée,
  convoquée…), seul l'historique des vérifications garde le verdict.
- **Permissions** : `role_required(*DIRECTION_ROLES)` (`apps/accounts/permissions.py`),
  `LoginRequiredMiddleware` et `MFARequiredMiddleware` (`apps/accounts/middleware.py`).
- **Audit** : `apps.audit.services.log(AuditAction.VIEW, …)`.
- **Front** : rendu serveur Django + HTMX + Tailwind, `base.html` gère déjà `lang` et `dir`
  (RTL pour l'arabe).

Aucune vue « détail de concours » n'existe aujourd'hui.

## User story

En tant que membre de la direction, je veux ouvrir un concours et voir ses postes (référence,
intitulé, nombre de postes, date limite) avec, pour chacun, le nombre de candidatures et leur
répartition (éligible / non éligible / à vérifier), afin de savoir où concentrer le travail.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Lecture de la fiche de tout concours. |
| direction | Lecture de la fiche de tout concours. |
| consultant, client (vague 2, inactifs) | Refusé : 403. |
| utilisateur inactif | Refusé (session refusée par l'authentification). |
| non connecté | Redirection vers la page de connexion (`LoginRequiredMiddleware`). |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP (`MFARequiredMiddleware`). |

La vérification du rôle est faite côté serveur sur la vue (décorateur ou mixin existant).

## Règles métier

1. **Accès depuis le tableau de bord** : dans le tableau « Concours » du tableau de bord,
   l'intitulé de chaque concours devient un lien vers sa fiche. Rien d'autre ne change sur le
   tableau de bord.
2. **Adressage** : la fiche est identifiée par la clé primaire du concours dans l'URL (le `slug`
   peut être vide). Un identifiant inexistant renvoie 404.
3. **En-tête du concours** : intitulé, organisation, statut (libellé), date de publication,
   date limite, nombre total de postes (somme des `nombre_postes` des postes du concours),
   nombre total de candidatures du concours. Une date absente s'affiche « — ».
4. **Tableau des postes** : une ligne par `PosteConcours` du concours, avec les colonnes
   suivantes :
   - référence (« — » si vide) ;
   - intitulé ;
   - nombre de postes ;
   - date limite : celle du poste ; à défaut, celle du concours, signalée par la mention
     « (date du concours) » ; à défaut des deux, « — » ;
   - nombre de candidatures ;
   - éligibles ;
   - non éligibles ;
   - à vérifier ;
   - non évaluées.
5. **Verdict d'éligibilité d'une candidature** : c'est le `resultat` de sa **dernière**
   `VerificationEligibilite` (la plus récente par `created_at`, puis par identifiant le plus
   élevé en cas d'égalité). Une candidature sans aucune vérification est « non évaluée ».
   La répartition **ne dépend pas** du champ `Candidature.statut` : une candidature
   « convoquée » dont le dernier verdict est « éligible » compte parmi les éligibles.
6. **Cohérence des comptes** : pour chaque poste, éligibles + non éligibles + à vérifier + non
   évaluées = nombre de candidatures. Le total de l'en-tête (règle 3) est égal à la somme des
   candidatures des postes.
7. **Ligne de totaux** en bas du tableau : somme de chaque colonne numérique (nombre de postes,
   candidatures, éligibles, non éligibles, à vérifier, non évaluées).
8. **Ordre des postes** : par référence croissante puis intitulé (ordre par défaut du modèle).
   Pas de tri interactif dans ce ticket.
9. **Mise en avant du travail** : la colonne « à vérifier » est visuellement distinguée
   (couleur ambre déjà utilisée sur le tableau de bord) lorsqu'elle est supérieure à 0. Aucune
   couleur rouge ou formulation ne présente « non éligible » comme une décision : c'est un
   verdict du moteur, la décision reste humaine.
10. **Navigation vers la liste des candidatures (KAN-10)** : chaque ligne de poste mène à la liste
    des candidatures du poste ; chaque compteur non nul (éligibles, non éligibles, à vérifier,
    non évaluées) mène à cette même liste préfiltrée sur le verdict correspondant. Ces liens
    sont activés dès que KAN-10 est livré (dépendance ; si KAN-9 est livré avant, les compteurs
    sont affichés sans lien).
11. **Lecture seule** : la fiche ne propose aucune action (ni décision, ni relance du moteur,
    ni export, ni synchronisation).
12. **Données à jour** : les comptes sont calculés à chaque affichage à partir de la base de
    l'application (pas de cache) ; ils reflètent la dernière synchronisation avec le site.
13. **Aucune donnée personnelle** : la fiche n'affiche que des agrégats ; aucun nom, email,
    téléphone, CIN ou autre donnée de candidat.
14. **Fil d'Ariane** : « Tableau de bord › <intitulé du concours> », avec lien retour.
15. **Tous les statuts de concours** sont consultables, y compris « brouillon » et « terminé ».

## Critères d'acceptation

Jeu de données de référence (fictif) pour les CA2 à CA8 : un concours « Concours fictif SRM
Nord 2026 » (organisation « SRM Région Fictive Nord », date limite 2026-03-31) avec trois postes :

| Référence | Intitulé | Nb postes | Date limite poste | Candidatures (dernier verdict) |
|---|---|---|---|---|
| P-01 | Ingénieur réseaux | 2 | 2026-03-15 | 2 éligibles, 1 non éligible, 1 à vérifier, 1 sans vérification |
| P-02 | Technicien maintenance | 3 | — | 0 candidature |
| P-03 | Chef de service RH | 1 | 2026-03-20 | 1 à vérifier |

- **CA1** — Étant donné un utilisateur `direction` connecté avec TOTP et un concours existant,
  quand il affiche le tableau de bord, alors l'intitulé du concours est un lien dont la cible
  est l'URL de la fiche de ce concours.
- **CA2** — Étant donné le jeu de référence, quand un utilisateur `direction` ouvre la fiche du
  concours, alors la réponse est 200 et l'en-tête affiche l'intitulé, l'organisation, le libellé
  du statut, la date limite « 31/03/2026 », un total de 6 postes et un total de 6 candidatures.
- **CA3** — Étant donné le jeu de référence, quand la fiche est affichée, alors le tableau
  contient exactement 3 lignes de postes, dans l'ordre P-01, P-02, P-03, chacune avec
  référence, intitulé, nombre de postes et date limite.
- **CA4** — Étant donné le jeu de référence, quand la fiche est affichée, alors la ligne P-01
  indique 5 candidatures, 2 éligibles, 1 non éligible, 1 à vérifier, 1 non évaluée.
- **CA5** — Étant donné le poste P-02 sans date limite propre, quand la fiche est affichée, alors
  sa date limite est « 31/03/2026 » suivie de la mention « (date du concours) », et ses compteurs
  valent tous 0.
- **CA6** — Étant donné une candidature de P-03 ayant une première vérification « non éligible »
  puis une vérification plus récente « à vérifier », quand la fiche est affichée, alors cette
  candidature est comptée uniquement dans « à vérifier ».
- **CA7** — Étant donné une candidature de statut « convoquée » dont la dernière vérification
  est « éligible », quand la fiche est affichée, alors elle est comptée parmi les éligibles du
  poste (la répartition ignore `Candidature.statut`).
- **CA8** — Étant donné le jeu de référence, quand la fiche est affichée, alors la ligne de
  totaux indique 6 postes, 6 candidatures, 2 éligibles, 1 non éligible, 2 à vérifier,
  1 non évaluée, et pour chaque ligne la somme des quatre compteurs égale le nombre de
  candidatures.
- **CA9** — Étant donné un concours sans aucun poste, quand la fiche est affichée, alors la
  réponse est 200 et le message « Aucun poste pour ce concours. » remplace le tableau.
- **CA10** — Étant donné un identifiant de concours inexistant, quand un utilisateur `direction`
  demande la fiche, alors la réponse est 404 et aucune entrée d'audit n'est créée.
- **CA11** — Étant donné un utilisateur `admin` connecté avec TOTP, quand il ouvre la fiche,
  alors la réponse est 200.
- **CA12** — Étant donné un utilisateur non connecté, quand il demande la fiche, alors il est
  redirigé (302) vers la page de connexion avec l'URL demandée en paramètre `next`.
- **CA13** — Étant donné un utilisateur de rôle `consultant` ou `client`, connecté avec TOTP,
  quand il demande la fiche, alors la réponse est 403 et aucune donnée du concours n'est
  renvoyée.
- **CA14** — Étant donné un utilisateur `direction` connecté sans TOTP actif, quand il demande
  la fiche, alors il est redirigé vers l'activation du TOTP.
- **CA15** — Étant donné un concours de 3 postes × 10 candidatures et un autre de 30 postes ×
  100 candidatures (avec vérifications), quand chaque fiche est affichée, alors le nombre de
  requêtes SQL est identique pour les deux et borné par une constante fixée dans le test
  (agrégats calculés en base, aucune requête par poste ni par candidature).
- **CA16** — Étant donné un utilisateur `direction`, quand il ouvre la fiche d'un concours,
  alors une entrée `JournalAudit` est créée avec l'action « Consultation » (`view`), l'acteur,
  le type d'objet `Concours`, l'identifiant du concours et l'adresse IP de la requête.
- **CA17** — Étant donné le jeu de référence avec des candidats nommés (noms fictifs), quand la
  fiche est affichée, alors le HTML ne contient aucun nom, prénom, email, téléphone ni CIN de
  candidat.
- **CA18** — Étant donné KAN-10 livré, quand la fiche est affichée, alors la ligne P-01 contient
  un lien vers la liste des candidatures du poste P-01, et le compteur « à vérifier » de P-01
  pointe vers cette liste avec le filtre de verdict « à vérifier » dans l'URL ; un compteur nul
  n'est pas un lien.
- **CA19** — Étant donné la langue active `ar`, quand la fiche est affichée, alors la réponse est
  200, la balise `html` porte `dir="rtl"` et tous les libellés fixes de la page passent par le
  système de traduction Django (vérifiable par la présence des chaînes dans le catalogue
  `makemessages`).

## Cas limites et erreurs

- **Concours sans poste** : message dédié (CA9), en-tête affiché avec 0 poste et 0 candidature.
- **Poste sans candidature** : ligne affichée avec des compteurs à 0, sans lien sur les compteurs.
- **Poste sans référence** : « — » dans la colonne référence ; l'ordre place les postes sans
  référence en premier (ordre lexicographique de la chaîne vide), puis par intitulé.
- **Dates absentes** : « — » (en-tête et tableau), avec repli sur la date du concours pour le poste.
- **Références alphanumériques** : l'ordre est lexicographique (« P-10 » avant « P-2 ») ;
  voir question ouverte Q4.
- **Volumes importants** : un concours de plusieurs dizaines de postes et plusieurs milliers de
  candidatures par poste doit s'afficher sans dégradation liée au volume (CA15) ; cible de
  temps de réponse : moins de 1 s côté serveur pour 30 postes × 5 000 candidatures sur
  l'environnement de développement (vérification explicite, non bloquante en CI).
- **Plusieurs vérifications par candidature** : seule la dernière compte (CA6) ; en cas de
  `created_at` identiques, l'identifiant le plus élevé l'emporte.
- **Synchronisation concurrente** : si une synchronisation ou le moteur d'éligibilité tourne
  pendant l'affichage, les comptes peuvent refléter un état intermédiaire ; ils seront justes
  au rechargement suivant. Les comptes d'une même page sont issus d'une même lecture
  cohérente (règle 6 toujours respectée sur la page affichée).
- **Candidatures sur offres d'emploi** : jamais comptées (elles n'ont pas de poste de concours).
- **Rôle insuffisant / non connecté / sans TOTP** : CA12 à CA14.
- **Méthodes HTTP** : seule `GET` est acceptée ; `POST` renvoie 405.

## Traçabilité et conformité

- **Journal d'audit** : une entrée `view` par affichage de la fiche, objet = `Concours`
  (acteur, horodatage, IP, navigateur). Pas d'avant/après (lecture seule). Aucune entrée en
  cas de 403, 404 ou redirection.
- **Données personnelles** : aucune donnée de candidat affichée, uniquement des agrégats
  (CA17). Les comptes ne permettent pas de réidentifier un candidat (pas de détail en dessous
  du poste).
- **IA** : non utilisée. Les verdicts affichés proviennent du moteur déterministe déjà en place.
- **Humain dans la boucle** : « non éligible » est présenté comme un verdict du moteur, jamais
  comme une décision ; aucune action d'élimination n'est proposée.
- **Biais** : aucune répartition ni aucun filtre par sexe, âge, région, photo ou situation
  familiale. Le tableau de bord des écarts relève de la vague 2.

## Hors périmètre

- Liste des candidatures d'un poste, filtres, tri, recherche : **KAN-10**.
- Actions en lot : **KAN-15**.
- Liste complète et paginée de tous les concours (le tableau de bord n'en montre que 10) :
  voir question ouverte Q3.
- Exports PDF/Excel (epic exports), synchronisation manuelle, relance du moteur d'éligibilité.
- Statistiques par score, par anomalie ou par statut de candidature (voir Q2).
- Vue équivalente pour les offres d'emploi (recrutement privé).
- Tableau de bord des écarts par sexe et région (vague 2).

## Questions ouvertes

- [ ] **Q1 — Source du verdict** : la répartition doit-elle suivre le dernier verdict du moteur
  (`VerificationEligibilite`) ou le statut courant de la candidature, qui peut avoir été changé
  par une décision humaine ? **Proposition par défaut** : dernier verdict du moteur (règle 5),
  car le statut mêle verdict et étapes ultérieures (présélectionnée, convoquée…). Conséquence :
  une candidature « à vérifier » déjà tranchée par un humain reste comptée « à vérifier » ;
  si c'est gênant, ajouter en complément une colonne « décision humaine prise » (petit surcoût).
  À noter : le compteur « Dossiers à vérifier » du tableau de bord se base aujourd'hui sur le
  statut ; les deux chiffres peuvent différer. À harmoniser dans un ticket séparé si souhaité.
- [ ] **Q2 — Colonnes supplémentaires** : faut-il afficher aussi le nombre de candidatures avec
  anomalies non résolues par poste ? **Proposition par défaut** : non dans ce ticket (le ticket
  ne le demande pas) ; ajout simple ultérieurement, au même coût de requête.
- [ ] **Q3 — Accès aux concours au-delà des 10 du tableau de bord** : faut-il une liste complète
  des concours ? **Proposition par défaut** : hors périmètre ; créer un ticket dédié (liste
  paginée avec filtre par statut). Conséquence : tant qu'il n'existe pas, les concours anciens
  ne sont accessibles que par URL directe.
- [ ] **Q4 — Ordre des postes** : l'ordre lexicographique des références (« P-10 » avant « P-2 »)
  convient-il ? **Proposition par défaut** : oui, les références fictives et observées sont à
  longueur fixe (P-01, P-02…). Un tri « naturel » serait à spécifier si des références réelles
  l'exigent.
- [ ] **Q5 — Journalisation d'une vue sans donnée personnelle** : faut-il journaliser la
  consultation d'une fiche qui n'affiche que des agrégats ? **Proposition par défaut** : oui
  (règle simple « toute consultation est journalisée », coût négligeable).
