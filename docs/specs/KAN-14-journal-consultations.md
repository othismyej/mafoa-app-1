# KAN-14 — Journaliser chaque consultation de dossier et écran « Journal d'audit »

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-14
- **Epic** : à confirmer dans Jira (journal d'audit, cas d'usage 4 du MVP)
- **Statut** : validée le 2026-11-23 par le Product Owner (réponses par défaut retenues pour toutes les questions ouvertes ; à revoir avec la story de purge KAN-31 et avant toute mise en production avec des données réelles)

## Contexte

Les e-Concours publics exposent Mafoa à des recours : il faut pouvoir dire **qui a ouvert quel
dossier, quand et depuis quelle adresse**. Le brief l'exige (`CLAUDE.md` §5 : « journal de chaque
connexion et de chaque consultation de dossier » ; §7 écran 6 : « Journal d'audit (admin) ») et
la définition de « Terminé » de `docs/organisation.md` en fait une condition de chaque story.

Existant dans le code (à réutiliser, pas à re-spécifier) :

- `apps/audit/models.py` — `JournalAudit` : `timestamp` (indexé), `actor` (FK `PROTECT`, indexée),
  `actor_repr`, `action` (indexée, choix `AuditAction`), `content_type` + `object_id` (index
  composite), `object_repr`, `before`, `after`, `ip_address`, `user_agent`, `metadata` (JSON).
  **Ajout seul** à trois niveaux : `save`/`delete` du modèle et du queryset, admin Django en
  lecture seule, trigger PostgreSQL qui refuse `UPDATE` et `DELETE` (migration 0002).
- `AuditAction` : `login`, `login_failed`, `logout`, `mfa_activated`, `mfa_deactivated`,
  `invitation_created`, `invitation_accepted`, `view`, `create`, `update`, `delete`, `decision`,
  `sync`, `export`, `ai_call`.
- `apps/audit/services.py` — `log(action, actor, obj, before, after, request, actor_repr,
  object_repr, metadata)` (point d'entrée unique), `client_ip(request)` (ne lit
  `X-Forwarded-For` que derrière `TRUSTED_PROXY_COUNT` proxys de confiance), `snapshot()`.
- `apps/audit/signals.py` — **connexions déjà journalisées** : connexion, déconnexion, échec de
  connexion (identifiant saisi seulement), activation / désactivation du TOTP. Rien à ajouter.
- `apps/recruitment/views.py` :
  - `candidature_detail` journalise déjà `view` avec `object_repr = "Candidature #<pk>"` (sans
    nom), **sans métadonnées** ;
  - `decision_enregistrer` journalise `view` lorsqu'il réaffiche la fiche complète (envoi sans
    JavaScript), mais pas pour le fragment HTMX (la décision est déjà tracée par `decision`) ;
    les refus anormaux sont tracés en `update` avec `metadata.refus` et
    `metadata.candidature_id` (KAN-13) ;
  - `poste_candidatures` journalise `view` sur le poste avec filtres, **terme de recherche (un
    nom)**, page et nombre de résultats (KAN-10, Q4 et Q5) ;
  - `concours_detail` journalise `view` sur le concours.
- `apps/accounts/permissions.py` : `role_required`, `DIRECTION_ROLES`, `ADMIN_ROLES`.
  `User.is_staff` n'est vrai que pour le rôle `admin` : seul l'admin accède à l'administration
  Django.
- `config/settings/base.py` : `ATOMIC_REQUESTS = True` (une exception annule toutes les
  écritures de la requête, y compris le journal), `TIME_ZONE = "Africa/Casablanca"`.
- Administration Django : `CandidatAdmin`, `CandidatureAdmin`, `PieceAdmin`, `ExtractionAdmin`,
  `VerificationEligibiliteAdmin`, `ScoreAdmin`, `AnomalieAdmin`, `DecisionAdmin` affichent des
  données de candidats **sans aucune journalisation** des consultations. `JournalAuditAdmin`
  existe en lecture seule.
- Aucun écran applicatif « Journal d'audit » n'existe ; `apps/exports/` est vide.

Coordination : la vue qui sert une pièce est spécifiée dans **KAN-12** (en parallèle). Elle
applique la règle commune définie ici (règles 1 à 9) ; KAN-14 n'en livre pas la vue.

## User story

En tant qu'administrateur, je veux que chaque ouverture d'une fiche candidature ou d'une pièce
soit inscrite au journal d'audit (qui, quoi, quand, IP), et pouvoir consulter ce journal depuis un
écran réservé, afin de répondre aux exigences de traçabilité et aux éventuels recours.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Écran « Journal d'audit » (liste, filtres, détail, export) ; panneau « Consultations de ce dossier » sur la fiche ; ses propres consultations sont journalisées comme celles des autres. |
| direction | Ses consultations sont journalisées. **Aucun accès** au journal : écran, détail, export et panneau répondent 403 ; le lien de navigation et le bouton du panneau ne sont pas affichés. |
| consultant, client (vague 2, non attribuables) | 403 partout ; la tentative sur un dossier ou sur le journal est tracée (règle 8). |
| non connecté | Redirection vers la connexion (`LoginRequiredMiddleware`) ; rien n'est journalisé. |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP (`MFARequiredMiddleware`). |

Contrôle de rôle côté serveur sur chaque vue **et** chaque fragment HTMX du journal.

## Règles métier

### A. Règle commune de journalisation des consultations

1. **Qu'est-ce qu'une consultation de dossier ?** Toute réponse servie avec succès (statut 2xx)
   qui affiche ou transmet des données personnelles d'un candidat identifié :
   - la fiche candidature (page complète, y compris son réaffichage après une décision sans
     JavaScript) ;
   - l'ouverture d'une pièce, en aperçu ou en téléchargement (vue livrée par KAN-12) ;
   - tout fragment HTMX chargé en `GET` qui affiche des données du dossier (à venir : historique
     des vérifications, extractions, scores) ;
   - les pages de détail et les listes de l'administration Django pour les modèles portant des
     données de candidats : `Candidat`, `Candidature`, `Piece`, `Extraction`,
     `VerificationEligibilite`, `Score`, `Anomalie`, `Decision` (et tout modèle futur rattaché à
     un candidat) ;
   - les exports nominatifs (listes PDF/Excel, epic exports) : action `export` (règle 9).
   Les listes de candidatures d'un poste restent journalisées selon KAN-10 (inchangé).
2. **Format d'une entrée de consultation** (action `view`) :
   - `actor` = utilisateur connecté, `actor_repr` = son email ;
   - objet (`content_type`, `object_id`) = l'objet consulté (candidature, pièce, candidat…) ;
   - `object_repr` **sans nom** : `Candidature #<id>`, `Pièce #<id> (Candidature #<id>)`,
     `Candidat #<id>`, `<Type> #<id>` pour les autres modèles ;
   - `ip_address` via `client_ip(request)`, `user_agent` (255 caractères max) ;
   - `before` et `after` vides (lecture seule) ;
   - `metadata` normalisées :
     - `vue` (obligatoire) : `fiche`, `fiche_apres_decision`, `piece`, `fragment`,
       `admin_detail`, `admin_liste` ;
     - `candidature_id` (entier) dès que la consultation concerne une candidature identifiable
       (fiche, pièce, fragment, objets d'évaluation) ; `candidat_id` (entier) si connu ;
     - `piece_id` et `mode` (`apercu` ou `telechargement`) pour une pièce ;
     - `fragment` (nom du fragment) et `htmx: true` pour un fragment HTMX.
   Ni `object_repr` ni `metadata` d'une entrée de consultation ne contiennent nom, prénom, CIN,
   email, téléphone ou date de naissance du candidat. Exception héritée : le terme de recherche
   de la liste (KAN-10 Q4) et la recherche de l'administration Django (règle 7).
3. **Un seul point d'entrée** : une fonction de service dédiée (par exemple
   `log_consultation(request, obj, vue, **metadata)` dans `apps/audit/services.py`) construit
   `object_repr` et `metadata` selon la règle 2 et appelle `log()`. Les appels existants de
   `candidature_detail` et `decision_enregistrer` l'utilisent ; KAN-12 l'utilise pour les pièces
   (si KAN-12 est fusionné avant, son appel est migré par KAN-14 ; dans tous les cas l'entrée
   d'une pièce porte `metadata.candidature_id`).
4. **Pas de dédoublonnage** : chaque réponse servie produit une entrée, y compris un
   rechargement, un retour arrière ou une restauration d'historique HTMX. Exceptions
   techniques : les sous-requêtes d'une même ouverture de pièce (requêtes partielles HTTP
   `Range` au-delà du premier octet, miniatures) ne créent pas d'entrée supplémentaire (détail
   dans KAN-12). Aucun lien vers une fiche ou une pièce ne déclenche de préchargement
   (`hx-trigger` au survol, `prefetch`) qui créerait une consultation non voulue.
5. **Pas de doublon avec une action** : le fragment HTMX renvoyé après une décision acceptée ou
   refusée n'est pas une consultation (l'entrée `decision` ou le refus suffit). Le réaffichage
   complet de la fiche sans JavaScript est journalisé avec `vue = fiche_apres_decision`.
6. **Journalisation obligatoire et solidaire** : si l'écriture de l'entrée échoue, la réponse
   n'est pas servie (erreur 500, aucune donnée du dossier dans la réponse). Une réponse 403, 404,
   405 ou une redirection ne crée **aucune** entrée `view`.
7. **Administration Django** (admin seulement) : un mixin commun aux `ModelAdmin` de la règle 1
   journalise :
   - chaque affichage `GET` d'une page de détail ou d'historique d'un objet : `vue =
     admin_detail`, objet = l'objet, `candidature_id` / `candidat_id` si dérivables ;
   - chaque affichage d'une liste : une entrée par page servie, sans objet, `object_repr =
     "Administration : <libellé pluriel du modèle>"`, `vue = admin_liste`, métadonnées :
     recherche saisie, filtres, page, nombre de résultats.
   Les modifications faites dans l'administration restent régies par leurs stories respectives.
8. **Accès refusés tracés** : une nouvelle valeur `AuditAction.ACCESS_DENIED` (« Accès refusé »)
   est ajoutée. Elle est écrite pour toute réponse 403 sur la fiche, un fragment de dossier, une
   pièce (KAN-12), l'écran du journal, son détail, son export et le panneau des consultations :
   acteur, IP, navigateur, `object_repr` sans nom, `metadata.chemin` (chemin de l'URL sans
   paramètres) et `metadata.candidature_id` si l'URL vise un dossier. Cette entrée **survit** à
   l'annulation de la transaction de requête (`ATOMIC_REQUESTS`), par exemple en renvoyant la
   réponse 403 au lieu de lever l'exception, comme dans KAN-13. Les refus de KAN-13 (`update` +
   `metadata.refus`) restent inchangés.
9. **Exports nominatifs** (règle pour les stories d'export, et appliquée ici à l'export du
   journal) : action `export`, sans objet ou avec l'objet périmètre (poste, concours),
   `metadata` = type de liste ou `vue`, format (`csv`, `xlsx`, `pdf`), filtres appliqués, nombre
   de lignes. Les identifiants des candidatures exportées ne sont pas listés (Q10).

### B. Écran « Journal d'audit » (admin)

10. **Accès** : URL dédiée (par exemple `/audit/`), `GET` uniquement, rôle `admin`. Un lien
    « Journal d'audit » figure dans la navigation principale pour l'admin uniquement.
11. **Liste** : entrées triées de la plus récente à la plus ancienne (`-timestamp`, `-id`),
    50 par page, pagination « Précédente / Suivante » avec numéro de page et total. Colonnes :
    - Horodatage (`JJ/MM/AAAA HH:MM:SS`, fuseau `Africa/Casablanca`) ;
    - Acteur (`actor_repr` ; mention « (désactivé) » si le compte est inactif ; « système » ou
      « anonyme » si pas d'utilisateur) ;
    - Action (libellé français de `AuditAction`) ;
    - Objet (`object_repr` ; lien vers la fiche candidature si l'objet est une candidature
      existante, ou vers la fiche de la candidature de la pièce) ;
    - Adresse IP ;
    - lien « Détail ».
12. **Filtres** (combinés en ET, portés par l'URL, appliqués en HTMX avec repli sans
    JavaScript, bouton « Réinitialiser ») :
    - **Période** : date de début et date de fin incluses (dates locales). Par défaut : les
      30 derniers jours. Étendue maximale : 366 jours ;
    - **Acteur** : liste de tous les utilisateurs (actifs et désactivés, triés par email) plus
      « Sans utilisateur (système, anonyme) » ;
    - **Action** : choix multiple parmi `AuditAction` ;
    - **Type d'objet** : candidature, pièce, candidat, concours, poste, offre, décision,
      utilisateur, invitation, entrée du journal, « aucun objet » ;
    - **Dossier (candidature n°)** : entier ; retient les entrées dont l'objet est cette
      candidature **ou** dont `metadata.candidature_id` vaut ce numéro (fiche, pièces,
      décisions, refus, objets d'évaluation) ;
    - **Adresse IP** : égalité exacte ;
    - case « Inclure les consultations du journal » (décochée par défaut, voir règle 15).
    Une valeur invalide est ignorée avec un message ; une date de fin antérieure à la date de
    début ou une étendue supérieure à 366 jours affiche un message et aucune entrée.
13. **Détail d'une entrée** (par exemple `/audit/<id>/`) : tous les champs (horodatage, acteur,
    action, type et identifiant d'objet, `object_repr`, IP, navigateur), les métadonnées en
    tableau clé / valeur, et **avant / après** côte à côte : une ligne par clé, les clés dont la
    valeur diffère portent la mention texte « modifié » (la couleur ne porte jamais seule
    l'information). Valeurs JSON affichées échappées, sans troncature silencieuse. Lien vers
    l'objet s'il existe encore (fiche pour une candidature ou une pièce). Identifiant
    inexistant : 404.
14. **Aucune modification possible** : aucun formulaire, bouton ou URL de modification ou de
    suppression ; toute méthode autre que `GET` (et `HEAD`) sur les URL du journal répond 405.
    Les protections existantes (modèle, queryset, admin, trigger) sont conservées.
15. **La consultation du journal est journalisée** (action `view`) : liste (`vue =
    journal_liste`, filtres et page en métadonnées), détail (`vue = journal_detail`, objet =
    l'entrée consultée), panneau d'un dossier (`vue = journal_dossier`, `candidature_id`).
    Ces entrées sont masquées par défaut dans la liste (case de la règle 12) pour ne pas noyer
    les consultations de dossiers.
16. **Panneau « Consultations de ce dossier »** sur la fiche candidature, admin seulement :
    un bouton « Voir qui a consulté ce dossier » charge à la demande un fragment HTMX (par
    exemple `/candidatures/<id>/consultations/`) qui affiche :
    - le nombre total de consultations (`view` avec `candidature_id` = ce dossier, hors
      consultations du journal) et le nombre de personnes distinctes ;
    - les 20 plus récentes : horodatage, acteur, nature (« Fiche », « Pièce #id (aperçu /
      téléchargement) », « Administration »), adresse IP ;
    - un lien vers l'écran du journal filtré sur ce dossier.
    Pour `direction`, le bouton est absent du HTML et l'URL du fragment répond 403.
17. **Export du journal** : depuis la liste, boutons « Exporter en CSV » et « Exporter en
    Excel » appliquant exactement les filtres courants (même logique serveur que la liste).
    - colonnes : horodatage (ISO 8601 avec fuseau), acteur, action (libellé), type d'objet,
      identifiant d'objet, objet, adresse IP, navigateur, métadonnées (JSON compact) ; **avant /
      après ne sont pas exportés**, ni le terme de recherche `metadata.recherche` (minimisation) ;
      les caractères de contrôle sont remplacés par `�` ;
    - CSV en UTF-8 avec BOM, séparateur `;` ; Excel via `openpyxl` ;
    - toute cellule commençant (espaces de tête ignorés) par `=`, `+`, `-`, `@`, tabulation ou retour chariot est préfixée
      d'une apostrophe (protection contre l'injection de formules) ;
    - nom de fichier `journal-audit-AAAAMMJJ-HHMM.csv` / `.xlsx` ;
    - plafond de 50 000 lignes : au-delà, aucun fichier n'est produit et un message invite à
      restreindre les filtres ;
    - chaque export produit est journalisé (règle 9, `vue = journal_export`).
18. **Performances** : nombre de requêtes SQL de la liste, du panneau et du détail borné et
    indépendant du nombre d'entrées ; filtrage, comptage et tri en base. Index à ajouter par
    migration (création d'index uniquement, aucune réécriture de lignes, compatible avec le
    trigger) : index d'expression sur `(metadata->>'candidature_id')` ; index composite
    `(actor, timestamp)`. Les index existants (`timestamp`, `action`, `actor`,
    `content_type + object_id`) sont conservés. L'export est produit en flux (CSV) ou par lots
    (Excel) sans charger toutes les entrées en mémoire.
19. **Conservation** : KAN-14 ne supprime rien. Les entrées du journal sont soumises à une durée
    de conservation configurable (proposition : 5 ans, Q9), appliquée exclusivement par la
    procédure de purge de **KAN-31** (procédure explicite, elle-même journalisée, seule exception
    au trigger d'ajout seul). Le format sans nom (règle 2) permet de conserver la preuve des
    consultations après suppression d'un dossier au titre du droit de suppression (loi 09-08).
20. **Interface** : charte `docs/design/charte.md` (tableau `.table-data`, pastilles `.chip`
    pour les actions, `.btn-secondary` pour l'export, aucun rouge pour les refus d'accès),
    libellés via `{% translate %}`, propriétés logiques uniquement (RTL), horodatages en
    `tabular-nums`.

## Critères d'acceptation

Données fictives de référence : `admin1@mafoa.test` (admin), `dir1@mafoa.test` (direction),
`dir2@mafoa.test` (direction, désactivé), `cons1@mafoa.test` (consultant) ; candidat fictif
« Yasmine BENALI » (CIN `ZZ999001`, email `y.benali@exemple.test`) ; candidatures n° 42, 4 et 420 ;
pièce n° 7 de la candidature 42 ; IP client `203.0.113.10`.

**Règle commune**

- **CA1** — Étant donné `dir1` et la candidature 42, quand il ouvre la fiche depuis
  `203.0.113.10`, alors exactement une entrée est créée avec `action = view`, `actor = dir1`,
  objet = candidature 42, `object_repr = "Candidature #42"`, `ip_address = 203.0.113.10`, le
  navigateur de la requête, `before` et `after` vides, et `metadata` contenant `vue = "fiche"`,
  `candidature_id = 42` et `candidat_id` du candidat.
- **CA2** — Étant donné les consultations de CA1, CA6 et CA8, alors aucun des champs
  `object_repr` et `metadata` des entrées `view` créées ne contient « BENALI », « Yasmine »,
  « ZZ999001 » ni « y.benali@exemple.test ».
- **CA3** — Étant donné `dir1`, quand il ouvre trois fois la fiche 42 (dont un rechargement et
  une requête avec `HX-History-Restore-Request: true`), alors trois entrées `view` sont créées.
- **CA4** — Étant donné une demande de fiche par un utilisateur non connecté (redirection), sur
  une candidature inexistante (404) ou en `POST` (405), alors aucune entrée `view` n'est créée.
- **CA5** — Étant donné `cons1`, quand il demande la fiche 42, alors la réponse est 403 sans
  donnée du candidat, aucune entrée `view` n'est créée et exactement une entrée
  `access_denied` persiste après la requête, avec `actor = cons1`, `metadata.chemin =
  "/candidatures/42/"` et `metadata.candidature_id = 42`.
- **CA6** — Étant donné un envoi de décision **sans** `HX-Request` refusé (motif trop court) sur
  la candidature 42, alors une entrée `view` avec `metadata.vue = "fiche_apres_decision"` et
  `candidature_id = 42` est créée ; étant donné un envoi HTMX accepté, alors une entrée
  `decision` est créée et aucune entrée `view`.
- **CA7** — Étant donné une erreur simulée lors de l'écriture du journal, quand `dir1` ouvre la
  fiche 42, alors la réponse est une erreur 500 et son contenu ne contient ni « BENALI » ni
  « ZZ999001 ».
- **CA8** — *(dépend de KAN-12)* Étant donné l'ouverture de la pièce 7 en aperçu par `dir1`,
  alors une entrée `view` est créée avec objet = pièce 7, `object_repr = "Pièce #7 (Candidature
  #42)"`, `metadata.vue = "piece"`, `candidature_id = 42`, `piece_id = 7`, `mode = "apercu"` ;
  une requête partielle `Range: bytes=1000-` de la même ouverture ne crée pas d'entrée.
- **CA9** — Pour chacun des modèles `Candidat`, `Candidature`, `Piece`, `Extraction`,
  `VerificationEligibilite`, `Score`, `Anomalie`, `Decision` (test paramétré), étant donné
  `admin1` sur l'administration Django, quand il affiche la page de détail d'un objet, alors une
  entrée `view` est créée avec objet = cet objet, `object_repr = "<Type> #<id>"`,
  `metadata.vue = "admin_detail"` et `candidature_id` lorsque l'objet est rattaché à une
  candidature.
- **CA10** — Étant donné `admin1`, quand il affiche la liste d'administration des candidats avec
  la recherche « benali », alors une entrée `view` est créée sans objet, avec
  `object_repr = "Administration : candidats"`, `metadata.vue = "admin_liste"`, la recherche
  saisie et le nombre de résultats.

**Écran du journal : accès**

- **CA11** — Étant donné `admin1`, quand il ouvre l'écran du journal sans paramètre, alors la
  réponse est 200 et liste les entrées des 30 derniers jours, de la plus récente à la plus
  ancienne, 50 par page, avec horodatage, acteur, action en libellé français, objet, IP et lien
  « Détail ».
- **CA12** — Étant donné `dir1` (ou `cons1`), quand il demande la liste, le détail d'une entrée,
  l'export ou le panneau des consultations, alors chaque réponse est 403, ne contient aucune
  entrée du journal, et une entrée `access_denied` persiste pour chaque tentative.
- **CA13** — Étant donné un utilisateur non connecté, quand il demande l'écran du journal, alors
  il est redirigé vers la connexion avec le paramètre `next`.
- **CA14** — Étant donné `admin1`, alors le lien « Journal d'audit » figure dans la navigation ;
  étant donné `dir1`, alors il est absent du HTML de toutes ses pages.

**Écran du journal : filtres et liste**

- **CA15** — Étant donné des entrées datées du 01/11/2026, du 15/11/2026 et du 20/11/2026 (heure
  de Casablanca), quand le filtre période vaut du 15/11/2026 au 20/11/2026, alors seules les
  deux dernières sont listées (bornes incluses, y compris une entrée à 23:59:59 le 20/11).
- **CA16** — Étant donné des entrées de `dir1`, de `dir2` (désactivé) et du système, quand le
  filtre acteur vaut `dir2`, alors seules ses entrées sont listées avec la mention
  « (désactivé) » ; quand il vaut « Sans utilisateur », alors seules les entrées sans acteur le
  sont.
- **CA17** — Étant donné des entrées `view`, `decision` et `login`, quand le filtre action vaut
  `view` et `decision`, alors les entrées `login` sont exclues.
- **CA18** — Étant donné des consultations de candidatures et de concours, quand le filtre type
  d'objet vaut « candidature », alors seules les entrées dont l'objet est une candidature sont
  listées.
- **CA19** — Étant donné pour le dossier 42 : une consultation de fiche, une consultation de la
  pièce 7, une décision et un refus KAN-13 (`metadata.candidature_id = 42`, sans objet), et des
  entrées pour les dossiers 4 et 420, quand le filtre dossier vaut 42, alors exactement les
  quatre entrées du dossier 42 sont listées.
- **CA20** — Étant donné des entrées depuis `203.0.113.10` et `198.51.100.5`, quand le filtre IP
  vaut `203.0.113.10`, alors seules les premières sont listées.
- **CA21** — Étant donné plusieurs filtres combinés, alors le résultat est leur intersection ;
  l'URL porte tous les filtres ; une requête HTMX renvoie le fragment « tableau + pagination »
  et une requête classique la page complète, avec les mêmes entrées.
- **CA22** — Étant donné des paramètres invalides (`du=2026-13-45`, `dossier=abc`,
  `action=pirate`, `acteur=999999`), alors la réponse est 200, les valeurs invalides sont
  ignorées et un message le signale ; étant donné une date de fin antérieure à la date de
  début, ou une étendue de plus de 366 jours, alors un message explicite s'affiche et aucune
  entrée n'est listée.
- **CA23** — Étant donné des filtres sans résultat, alors le message « Aucune entrée pour ces
  critères. » s'affiche.
- **CA24** — Étant donné `admin1`, quand il affiche la liste filtrée sur le dossier 42, alors une
  entrée `view` est créée avec `metadata.vue = "journal_liste"` et les filtres appliqués ; par
  défaut, les entrées dont `metadata.vue` commence par `journal_` ne sont pas listées ; avec la
  case « Inclure les consultations du journal », elles le sont.
- **CA25** — Étant donné une entrée dont `object_repr` ou une métadonnée contient
  `<script>alert(1)</script>`, alors la liste et le détail l'affichent échappée.
- **CA26** — Étant donné une entrée sur la candidature 42, alors l'objet est un lien vers la fiche
  42 ; étant donné une entrée dont l'objet n'existe plus, alors `object_repr` est affiché sans
  lien et la page est 200.

**Détail, non-modification**

- **CA27** — Étant donné l'entrée `decision` d'une candidature passée de « éligible » à
  « présélectionnée », quand `admin1` ouvre son détail, alors la page affiche tous les champs de
  l'entrée, les métadonnées en clé / valeur et, pour la clé `statut`, l'avant « eligible »,
  l'après « preselectionnee » et la mention « modifié », les clés inchangées sans cette mention ;
  une entrée `view` avec `metadata.vue = "journal_detail"` et objet = l'entrée consultée est
  créée.
- **CA28** — Étant donné un identifiant d'entrée inexistant, alors le détail répond 404.
- **CA29** — Étant donné `admin1`, quand il envoie `POST`, `PUT`, `PATCH` ou `DELETE` sur la
  liste, le détail ou l'export, alors la réponse est 405 et aucune entrée existante n'est
  modifiée ; le HTML de la liste et du détail ne contient aucun formulaire ni bouton de
  modification ou de suppression.

**Panneau « Consultations de ce dossier »**

- **CA30** — Étant donné 25 consultations du dossier 42 par `dir1` et `admin1` (fiche et pièce
  7), quand `admin1` charge le panneau, alors il affiche « 25 consultations par 2 personnes »,
  les 20 plus récentes avec horodatage, acteur, nature et IP, et un lien vers le journal filtré
  sur le dossier 42 ; une entrée `view` avec `metadata.vue = "journal_dossier"` et
  `candidature_id = 42` est créée, et elle n'est pas comptée dans le total affiché.
- **CA31** — Étant donné `dir1` sur la fiche 42, alors le bouton « Voir qui a consulté ce
  dossier » est absent du HTML ; quand il appelle directement l'URL du fragment, alors la
  réponse est 403.
- **CA32** — Étant donné un dossier sans autre consultation que celle en cours, alors le panneau
  affiche « 1 consultation par 1 personne » (la consultation courante de la fiche).

**Export**

- **CA33** — Étant donné `admin1` et le filtre dossier 42, quand il exporte en CSV, alors le
  fichier `journal-audit-AAAAMMJJ-HHMM.csv` est en UTF-8 avec BOM, séparateur `;`, contient
  exactement les entrées de la liste filtrée (toutes pages) avec les colonnes de la règle 17 et
  sans colonnes avant / après ; une entrée `export` est créée avec `metadata.vue =
  "journal_export"`, `format = "csv"`, les filtres et le nombre de lignes.
- **CA34** — Étant donné la même action en Excel, alors un fichier `.xlsx` lisible par
  `openpyxl` contient les mêmes lignes et l'entrée `export` porte `format = "xlsx"`.
- **CA35** — Étant donné une entrée dont `actor_repr` ou une métadonnée commence par `=`, `+`,
  `-` ou `@`, alors la cellule exportée (CSV et Excel) commence par une apostrophe.
- **CA36** — Étant donné des filtres retenant plus de 50 000 entrées (plafond abaissé par
  configuration dans le test), alors aucun fichier n'est produit, un message invite à restreindre
  les filtres et aucune entrée `export` n'est créée.

**Performance, index, interface**

- **CA37** — Étant donné 5 entrées puis 5 000 entrées dans la période, quand la première page de
  la liste, le panneau du dossier 42 et le détail sont affichés, alors le nombre de requêtes SQL
  de chaque écran est identique dans les deux cas (à une constante près fixée par le test).
- **CA38** — Étant donné la base migrée, alors la table du journal possède un index sur
  l'expression `(metadata->>'candidature_id')` et un index composite `(actor_id, timestamp)`
  (vérifié par introspection) ; la migration s'applique sur une table contenant des entrées
  sans déclencher le trigger d'ajout seul.
- **CA39** — Étant donné une entrée créée à 23:30 UTC le 20/11/2026, alors la liste affiche son
  horodatage converti dans le fuseau `Africa/Casablanca` au format `JJ/MM/AAAA HH:MM:SS`.
- **CA40** — Étant donné la langue active `ar`, quand la liste et le détail sont affichés, alors
  `<html>` porte `dir="rtl"`, la page est 200 et les gabarits du journal n'utilisent aucune
  classe directionnelle figée (`ml-`, `mr-`, `pl-`, `pr-`, `text-left`, `text-right`, `left-`,
  `right-`).

## Cas limites et erreurs

- **Journal vide ou période sans entrée** : message CA23 ; l'export reste possible et produit
  un fichier avec les seuls en-têtes, journalisé avec `nb_lignes = 0`.
- **Acteur supprimé** : impossible (`PROTECT`) ; acteur désactivé affiché avec la mention
  « (désactivé) » (CA16).
- **Objet supprimé** (candidature purgée par KAN-31) : `object_repr` « Candidature #42 » reste
  lisible, sans lien (CA26) ; le filtre dossier fonctionne toujours (identifiant conservé).
- **Métadonnées anciennes sans `vue` ni `candidature_id`** (entrées antérieures à KAN-14, ex.
  la consultation de fiche actuelle) : affichées telles quelles ; elles ne remontent dans le
  filtre dossier que si leur objet est la candidature. Aucune reprise de l'historique (ajout
  seul).
- **IP absente** (requête sans `REMOTE_ADDR` valide, proxy mal configuré) : colonne « — » ; à
  surveiller lors de la mise en production (`TRUSTED_PROXY_COUNT`).
- **Volumes importants** : plusieurs millions d'entrées attendues (une par page de liste,
  fragment, fiche et pièce) ; période obligatoire bornée à 366 jours, index de la règle 18,
  export plafonné (CA36). Le comptage total de la pagination reste borné par la période.
- **Accès concurrent** : l'écriture est en ajout seul, sans verrou ; des entrées créées entre deux
  pages peuvent décaler la pagination (accepté, la page est cohérente à l'instant de la
  requête). Deux consultations simultanées du même dossier créent deux entrées.
- **Session expirée** pendant la navigation dans le journal : redirection vers la connexion,
  aucune entrée.
- **Préchargement par le navigateur ou un antivirus** d'une URL de fiche : produit une entrée
  réelle (le serveur ne peut pas le distinguer) ; l'interface n'en déclenche aucun (règle 4).
- **Valeurs très longues** (navigateur, métadonnées) : affichées en entier dans le détail, avec
  retour à la ligne ; tronquées visuellement dans la liste avec la valeur complète au survol.
- **Droits insuffisants** : CA5, CA12, CA31 ; l'administration Django n'est accessible qu'au
  rôle `admin` (`is_staff`).

## Traçabilité et conformité

- **Journal d'audit** : consultations de fiche, de pièce (KAN-12), de fragments de dossier et des
  pages d'administration portant des données de candidats (`view`, sans avant/après,
  métadonnées normalisées de la règle 2) ; accès refusés (`access_denied`, nouvelle action) ;
  consultations du journal lui-même (`view`, `vue = journal_*`) ; exports du journal
  (`export`). Connexions et TOTP : déjà journalisés (inchangé). Le journal reste en ajout seul ;
  aucune interface ne permet de le modifier.
- **Données personnelles** : les entrées de consultation ne portent aucune donnée nominative du
  candidat (identifiants internes seulement). Exceptions connues : terme de recherche de la
  liste (KAN-10 Q4), recherche de l'administration Django, instantanés avant/après des
  décisions (KAN-13) et motifs. Le journal n'est visible que par `admin` ; l'export exclut
  avant/après. Durée de conservation et purge : KAN-31 (règle 19). L'IP et le navigateur des
  utilisateurs internes sont des données personnelles des salariés de Mafoa : finalité
  sécurité et traçabilité, à mentionner dans l'information interne.
- **IA** : aucune. Le journal ne sert jamais d'entrée à un appel IA.
- **Humain dans la boucle** : sans objet (aucune décision sur un dossier dans cette story).
- **Biais** : aucun filtre, colonne ni export du journal n'utilise le sexe, l'âge, la région, la
  photo ou la situation familiale des candidats.

## Hors périmètre

- Vue qui sert une pièce et visionneuse (KAN-12) : seule la règle de journalisation est définie
  ici.
- Purge et durée de conservation effective, droit d'accès et de suppression (KAN-31).
- Exports métier (listes d'éligibles, convoqués, résultats) : epic exports ; ils appliqueront la
  règle 9.
- Journalisation des connexions (déjà en place) et de la gestion des comptes (écran 7).
- Alertes automatiques (volume de consultations anormal, accès hors heures), tableau de bord de
  supervision, signature ou chaînage cryptographique des entrées.
- Rattachement des exports de listes à chaque dossier exporté (Q10).
- Accès des rôles `consultant` et `client` (vague 2).

## Questions ouvertes

Toutes tranchées le 2026-11-23 : la proposition par défaut est retenue pour chaque question.

- [x] **Q1 — Accès de la direction au journal** : *Proposition par défaut* : aucun (403), le
  journal est réservé à l'admin (brief §7 écran 6, cohérent avec KAN-10 Q4). *Alternative* :
  la direction voit ses propres entrées ; impose un filtrage par acteur forcé et des tests
  supplémentaires.
- [x] **Q2 — Panneau « qui a consulté ce dossier »** : *Proposition par défaut* : admin
  seulement, chargé à la demande (évite une entrée `journal_dossier` à chaque ouverture de
  fiche). *Alternative* : visible par la direction, ce qui expose qui consulte quoi entre
  associés.
- [x] **Q3 — Dédoublonnage des rechargements** : *Proposition par défaut* : aucun, une entrée par
  réponse servie (preuve complète, règle simple, cohérent avec KAN-10 Q5). *Alternative* : une
  entrée par utilisateur et dossier par fenêtre de 60 s ; réduit le volume mais masque des
  accès répétés.
- [x] **Q4 — Journaliser la consultation du journal** : *Proposition par défaut* : oui, entrées
  masquées par défaut dans la liste. *Conséquence* : volume accru, mais contrôle de l'usage du
  journal lui-même (qui a regardé qui a consulté).
- [x] **Q5 — Export du journal** : *Proposition par défaut* : CSV et Excel, filtres courants,
  plafond 50 000 lignes, sans avant/après, journalisé. *Alternative* : CSV seul, ou export
  complet avec avant/après pour un recours (à demander au cas par cas).
- [x] **Q6 — Nouvelle action `access_denied`** : *Proposition par défaut* : oui, pour les 403 sur
  dossiers, pièces et journal (filtrable, distinct d'une consultation réussie). Les refus KAN-13
  restent en `update` + `metadata.refus`. *Conséquence* : migration de choix sur
  `JournalAudit.action`.
- [x] **Q7 — Administration Django** : *Proposition par défaut* : journaliser les pages de détail
  et de liste des modèles portant des données de candidats ; conserver `JournalAuditAdmin` en
  lecture seule, ses consultations étant journalisées comme `journal_liste` /
  `journal_detail`. *Alternative* : retirer le journal de l'administration Django pour n'avoir
  qu'un point d'accès (modifie le test existant `test_admin_journal_en_lecture_seule`).
- [x] **Q8 — Index du filtre dossier** : *Proposition par défaut* : index d'expression sur
  `metadata->>'candidature_id'` (aucun changement de modèle). *Alternative* : colonne dédiée
  `candidature_id` nullable sur `JournalAudit` (plus explicite, mais les entrées existantes ne
  peuvent pas être complétées à cause de l'ajout seul).
- [x] **Q9 — Durée de conservation du journal** : *Proposition par défaut* : 5 ans à compter de
  l'entrée, configurable, appliquée par KAN-31 ; à valider avec un juriste au regard de la loi
  09-08 et des délais de recours des concours publics. *Conséquence* : jusqu'à KAN-31, rien
  n'est purgé.
- [x] **Q10 — Exports métier et « qui a consulté »** : *Proposition par défaut* : l'entrée
  `export` porte le périmètre, les filtres et le nombre de lignes, sans liste des
  candidatures ; le panneau d'un dossier ne montre donc pas les exports. *Alternative* :
  stocker les identifiants exportés (jusqu'à plusieurs milliers par entrée) pour les relier
  aux dossiers.
- [x] **Q11 — Période par défaut et étendue maximale** : *Proposition par défaut* : 30 derniers
  jours, 366 jours maximum. *Conséquence* : une recherche sur plusieurs années se fait en
  plusieurs requêtes ou exports.
- [x] **Q12 — Terme de recherche nominatif (KAN-10) et recherche de l'administration** :
  *Proposition par défaut* : conservés tels quels (traçabilité de « qui a cherché qui »),
  visibles de l'admin seulement. *Conséquence* : ces entrées contiennent des noms ; KAN-31 devra
  prévoir leur traitement lors d'une demande de suppression (le journal étant en ajout seul).
