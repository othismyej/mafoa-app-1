# KAN-37 — Export Excel des candidatures par poste et par concours (données, résultat et raisons)

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-37
- **Epic** : à confirmer dans Jira (listes et exports, cas d'usage 4 du MVP)
- **Statut** : validée le 2026-11-23, choix du PO sur données et résultat (identité complète avec
  date de naissance, formation et expériences déclarées ; résultat = décision humaine en vigueur
  **et** verdict du moteur avec la raison critère par critère). Les autres questions ouvertes
  sont tranchées par la proposition par défaut.

## Contexte

Les clients publics (SRM) et la direction de Mafoa travaillent sur des listes Excel : contrôle
croisé, préparation des listes de présélection, réponse à un recours. Le besoin du PO (verbatim) :

> « possibilité d'exporter un fichier Excel par poste, avec toutes les candidatures, et les
> données de chaque candidat, et aussi le résultat de la sélection et la raison derrière ce
> résultat, il faut donner pourquoi ; possibilité d'exporter aussi un fichier par concours, avec
> pour chaque onglet tous les postes et toutes les data avec aussi le résultat, c'est le 1 mais
> pour tous les postes du concours »

Existant dans le code (à réutiliser, ne pas recréer) :

- **Liste des candidatures d'un poste** (KAN-10) : vue `poste_candidatures` (sa règle 29,
  « ni export », est levée par la présente story pour un simple lien GET), filtrage unique
  `apps/recruitment/filters.py` — `filter_candidatures(poste, params)` renvoie
  `(queryset, CandidatureFilter)` annoté (`dernier_verdict`, `nb_anomalies`, `gravite_max`) ;
  `CandidatureFilter.querystring()` reconstruit les paramètres validés.
- **Fiche concours** (KAN-9) : vue `concours_detail`, `selectors.concours_stats`.
- **Mise en forme** (KAN-11) : `apps/recruitment/presenters.py` — `formations`, `experiences`,
  `criteres` (lignes `critere / libelle / verdict / attendu / constate / raison`), robustes à des
  données mal formées.
- **Moteur d'éligibilité** : `VerificationEligibilite` (résultat, `details` critère par critère,
  version du moteur) ; « dernière » = plus récente par `created_at` puis `id` ;
  `apps/evaluation/services.profil_from_declared` et `criteres_from_cible` ;
  `eligibility/experience.compute_experience` (calcul déterministe des années).
- **Décisions humaines** (KAN-13) : `Decision` (auteur, décision, motif, date), jamais modifiée,
  la plus récente est « en vigueur ».
- **Audit** (KAN-14) : action `export`, règle 9 (périmètre, filtres, nombre de lignes, sans liste
  des candidatures) ; `role_required_logged` / `deny` pour journaliser les refus
  (`access_denied`) ; `apps/audit/exports.py` neutralise les formules et les caractères de
  contrôle.
- `apps/exports/` : application vide, prévue pour les exports métier.

## User story

En tant que membre de la direction, je veux télécharger un fichier Excel des candidatures d'un
poste (avec les filtres de la liste) ou de tout un concours (un onglet par poste), contenant les
données de chaque candidat, la décision humaine en vigueur et le verdict du moteur avec ses
raisons critère par critère, afin de partager et contrôler les résultats de la sélection et de
justifier chaque résultat en cas de recours.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Export par poste et par concours. |
| direction | Export par poste et par concours. |
| `consultant`, `client` (vague 2) | Refusé : 403, refus journalisé (`access_denied`), aucun fichier ; boutons absents (ces rôles n'accèdent déjà pas aux écrans). |
| non connecté | Redirection vers la connexion (`LoginRequiredMiddleware`), rien n'est journalisé. |

## Règles métier

### A. Deux exports

1. **Export par poste** : bouton secondaire « Exporter en Excel (filtres appliqués) » au-dessus
   du tableau de la liste des candidatures d'un poste. Il exporte **toutes les candidatures
   retenues par les filtres courants** (statut, verdict, anomalies, recherche), **toutes pages
   confondues**, dans l'ordre de tri courant. La pagination (`page`, `taille`) est ignorée. Le
   lien est recalculé à chaque changement de filtre (il fait partie de la zone rafraîchie par
   HTMX). Sans filtre, l'export contient toutes les candidatures du poste.
2. **Export par concours** : bouton secondaire « Exporter le concours en Excel » dans l'en-tête de
   la fiche concours. Il exporte **toutes les candidatures de tous les postes**, sans filtre, dans
   un classeur contenant :
   - un onglet **« Synthèse »** en tête : une ligne par poste (référence, intitulé, nombre de
     postes à pourvoir, date limite, nombre de candidatures, répartition par dernier verdict —
     éligibles, non éligibles, à vérifier, non évaluées — et par statut), puis une ligne
     « Total » ;
   - **un onglet par poste**, dans l'ordre des postes de la fiche concours, avec exactement les
     colonnes de l'export par poste. Un poste sans candidature a son onglet avec la seule ligne
     d'en-têtes.
3. Ordre des lignes : export par poste = tri courant de la liste (par défaut date de candidature
   croissante, puis identifiant) ; export par concours = date de candidature croissante, puis
   identifiant, dans chaque onglet.

### B. Colonnes (une ligne par candidature)

4. **Repères** : N° de dossier (identifiant interne de la candidature), Poste (référence —
   intitulé), Date de candidature.
5. **Identité** (choix du PO) : Nom, Prénom, CIN, Date de naissance, Email, Téléphone, Ville.
   **Jamais** sexe, région, photo ni situation familiale (ni âge calculé).
6. **Formation et expériences déclarées** : une colonne texte lisible chacune, une entrée par
   ligne dans la cellule :
   - Formations : « intitulé — niveau — spécialité — établissement — année » (éléments vides
     omis) ; « Aucune formation déclarée » si la liste est vide ou absente ;
   - Expériences : « poste chez employeur (début → fin) », dates au format JJ/MM/AAAA,
     « en cours » sans date de fin, « date manquante » / valeur brute si illisible ;
     « Non renseignées » si l'information n'a pas été fournie, « Aucune expérience déclarée » si
     la liste est vide.
7. **Expérience calculée (années)** : nombre décimal (2 décimales) calculé par le code
   déterministe du moteur (`compute_experience`) à la date de référence du poste (date limite du
   poste, sinon du concours) ; cellule vide si les expériences ne sont pas renseignées ou si
   leur structure est inexploitable ; une période à date illisible n'est pas comptée, comme dans
   le moteur. Aucune nouvelle règle de calcul.
8. **Résultat — décision humaine en vigueur** (choix du PO) : Statut actuel de la candidature,
   Décision humaine (libellé), Motif, Auteur (email de l'utilisateur), Date de la décision. Sans
   décision : « Aucune décision humaine » dans la colonne Décision, les trois autres vides.
9. **Résultat — verdict du moteur** (choix du PO) : Verdict du moteur (Éligible / Non éligible /
   À vérifier / Non évaluée), Date du verdict, Version du moteur, puis, pour chacun des critères
   **Niveau d'études**, **Spécialité** et **Expérience** : quatre colonnes « attendu »,
   « constaté », « verdict », « raison », lues dans le `details` de la **dernière** vérification ;
   enfin « Autres critères (vérification humaine) » (attendu et raison du critère `autres` s'il
   existe). Un critère absent des `details` (données anciennes ou mal formées) laisse ses cellules
   vides ; une candidature non évaluée a toutes ces cellules vides et le verdict « Non évaluée ».
10. **Anomalies non résolues** : nombre, puis une colonne texte (une par ligne :
    « [gravité] type : description »). Les anomalies résolues ne sont pas exportées.
11. Le verdict du moteur n'est **pas** une décision : la ligne d'en-tête du fichier le rappelle
    dans le libellé (« Verdict du moteur (pas une décision) »).

### C. Mise en forme

12. Première ligne : en-têtes en gras sur fond gris clair, **figée** ; **filtres automatiques**
    sur toute la plage ; largeurs de colonnes fixées par type de donnée ; retour à la ligne dans
    les colonnes multi-lignes (formations, expériences, raisons, motif, anomalies).
13. Couleurs de statut **sobres** (fonds pâles, jamais le rouge de marque) sur les colonnes
    Statut et Verdict : vert pâle pour éligible / présélectionnée / convoquée / retenue, ambre
    pâle pour à vérifier, gris pâle pour non éligible / rejetée ; aucune couleur sinon.
14. Types natifs : dates en dates Excel (JJ/MM/AAAA, JJ/MM/AAAA HH:MM en heure de Casablanca pour
    les horodatages), nombres en nombres ; tout le reste en texte.
15. Noms d'onglets : ≤ 31 caractères, sans `[ ] : * ? / \`, uniques dans le classeur (suffixe
    « (2) », « (3) »… en cas de collision, après troncature) ; « Synthèse » est réservé. Nom
    d'onglet d'un poste = sa référence, sinon son intitulé, sinon « Poste <id> ». Le fichier par
    poste a un seul onglet nommé de même.

### D. Sécurité et volumes

16. **Neutralisation** (comme `apps/audit/exports.py`) : toute cellule texte qui commence (espaces
    de tête ignorés) par `=`, `+`, `-`, `@`, tabulation ou retour chariot est préfixée d'une
    apostrophe ; les caractères de contrôle interdits par le format (`\x00-\x08`, `\x0b`,
    `\x0c`, `\x0e-\x1f`) sont remplacés par `�`. Les cellules typées (dates, nombres) ne sont
    pas concernées.
17. **Plafond** : `EXPORT_CANDIDATURES_MAX_ROWS` (variable d'environnement, 20 000 par défaut)
    candidatures par fichier. Au-delà, aucun fichier : redirection vers la page d'origine (avec
    les filtres pour l'export par poste) et message d'avertissement « L'export est limité à N
    candidatures (M correspondent) : restreignez les filtres » ; aucune entrée `export`.
18. **Nom de fichier sans donnée personnelle** : `candidatures-poste-<id du poste>-AAAAMMJJ-HHMM.xlsx`
    et `candidatures-concours-<id du concours>-AAAAMMJJ-HHMM.xlsx` (heure locale). Ni nom de
    candidat ni terme de recherche.
19. **Requêtes** : nombre de requêtes SQL **constant**, indépendant du nombre de candidatures et
    de postes (candidatures annotées en une requête, dernières vérifications, dernières décisions
    et anomalies non résolues chargées en une requête chacune). Classeur produit en mode
    « écriture seule » d'openpyxl.
20. Vues **GET/HEAD uniquement** (405 sinon), poste qui n'appartient pas au concours de l'URL ou
    identifiant inconnu → 404. Réponse `Content-Disposition: attachment`, type
    `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `Cache-Control:
    no-store`.

## Critères d'acceptation

Jeu de données fictif : concours C (SRM fictive) avec postes P1 (référence « ING-01 ») et P2 ;
candidatures de P1 : A (éligible par le moteur, décision humaine « convoquée »), B (non éligible
par le moteur, sans décision), D (jamais évaluée) ; utilisateurs `dir1` (direction), `admin1`,
`cons1` (consultant).

**Export par poste**

- **CA1** — Étant donné `dir1`, quand il exporte P1 sans filtre, alors la réponse est un `.xlsx`
  en pièce jointe, lisible par openpyxl, avec un onglet « ING-01 », une ligne d'en-têtes et une
  ligne par candidature de P1 (A, B, D), aucune de P2.
- **CA2** — Étant donné le filtre `verdict=eligible` (ou `statut=…`, `anomalies=avec`, recherche
  `q`), quand `dir1` exporte, alors seules les candidatures retenues par le même filtre que la
  liste figurent, toutes pages confondues (`taille=25` et `page=2` ignorés), dans l'ordre du tri
  `tri` demandé.
- **CA3** — Étant donné la ligne de A, alors elle contient nom, prénom, CIN, date de naissance
  (cellule date), email, téléphone, ville, formations et expériences déclarées en texte lisible
  et l'expérience calculée (nombre).
- **CA4** — Étant donné la ligne de A, alors les colonnes de décision portent « Convoquée », le
  motif, l'email de l'auteur et la date ; la ligne de B porte « Aucune décision humaine ».
- **CA5** — Étant donné la ligne de B, alors le verdict du moteur est « Non éligible » et, pour le
  critère en échec, les colonnes attendu / constaté / verdict / raison reprennent le détail de la
  **dernière** vérification (pas d'une plus ancienne) ; la ligne de D porte « Non évaluée » et des
  colonnes de critères vides.
- **CA6** — Étant donné une anomalie non résolue et une anomalie résolue sur B, alors la colonne
  du nombre vaut 1 et la colonne texte ne cite que l'anomalie non résolue.
- **CA7** — Aucun en-tête ni aucune cellule ne contient le sexe, la région, une photo ou la
  situation familiale du candidat (vérifié sur des valeurs repères).
- **CA8** — La liste d'un poste affiche le bouton « Exporter en Excel (filtres appliqués) » dont
  le lien porte les filtres courants (et seulement les paramètres validés) ; la fiche concours
  affiche « Exporter le concours en Excel » ; boutons en `.btn-secondary`.

**Export par concours**

- **CA9** — Étant donné `admin1`, quand il exporte C, alors le classeur contient, dans l'ordre,
  « Synthèse », l'onglet de P1 puis celui de P2 ; chaque onglet de poste contient exactement les
  candidatures de ce poste, avec les colonnes de l'export par poste.
- **CA10** — L'onglet « Synthèse » contient une ligne par poste avec le nombre de candidatures et
  la répartition par verdict et par statut, et une ligne « Total » égale à la somme.
- **CA11** — Étant donné deux postes de même référence longue (> 31 caractères) ou contenant des
  caractères interdits, alors les onglets ont des noms valides, ≤ 31 caractères et distincts.
- **CA12** — Étant donné un poste sans candidature, alors son onglet existe avec la seule ligne
  d'en-têtes.

**Sécurité, audit, volumes**

- **CA13** — Étant donné `cons1`, quand il demande l'un ou l'autre export, alors la réponse est
  403, sans fichier, et une entrée `access_denied` est créée ; un utilisateur non connecté est
  redirigé vers la connexion.
- **CA14** — Chaque export réussi crée exactement une entrée `export` : acteur, objet = poste ou
  concours, `metadata` = `vue` (`export_candidatures_poste` / `export_candidatures_concours`),
  `format = "xlsx"`, filtres appliqués (export par poste ; recherche réduite à un booléen
  `recherche_appliquee`), `nb_lignes` (et `nb_postes` pour le concours) ; ni nom, ni CIN, ni
  terme de recherche, ni identifiants des candidatures dans l'entrée.
- **CA15** — Étant donné un candidat dont le nom commence par `=`, `+`, `-` ou `@` et un motif
  contenant un caractère de contrôle, alors les cellules commencent par une apostrophe et le
  fichier reste lisible.
- **CA16** — Étant donné un plafond abaissé à 2 et 3 candidatures retenues, alors aucun fichier
  n'est produit, la réponse redirige vers la page d'origine avec un avertissement et aucune entrée
  `export` n'est créée (poste et concours).
- **CA17** — Le nom du fichier suit la règle 18 et ne contient aucune donnée personnelle ni le
  terme de recherche.
- **CA18** — Le nombre de requêtes SQL d'un export est le même pour 2 et pour 12 candidatures
  (poste et concours).
- **CA19** — `POST` sur l'une ou l'autre URL répond 405 ; un poste d'un autre concours répond
  404.
- **CA20** — La première ligne est figée (`A2`), un filtre automatique couvre la plage des
  données, la colonne Statut d'une ligne « non éligible » a un fond gris pâle, jamais le rouge de
  marque.

## Cas limites et erreurs

- **Données déclarées mal formées** (liste attendue absente, entrée non dictionnaire, date
  illisible) : la cellule affiche ce qui est lisible, jamais d'erreur 500 ; l'expérience calculée
  reste vide si les expériences ne sont pas exploitables.
- **`details` mal formé** dans une vérification : colonnes de critères vides, verdict global
  toujours affiché.
- **Poste ou concours sans candidature** : fichier valide (en-têtes seuls).
- **Concours sans poste** : classeur avec le seul onglet « Synthèse » (ligne Total à zéro).
- **Filtres invalides** dans l'URL : ignorés comme dans la liste (KAN-10 règle 13).
- **Décision et statut divergents** (statut modifié par le moteur après une décision ancienne
  impossible par construction, ou données importées) : les deux sont exportés tels quels.
- **Auteur de décision désactivé** : son email est exporté (traçabilité).
- **Très gros concours** : plafond (règle 17) ; au-delà, export poste par poste avec filtres.

## Traçabilité et conformité

- **Journal d'audit** : action `export` (règle 9 de KAN-14), sans liste des candidatures
  exportées ni donnée nominative ; refus en `access_denied`. Rien d'autre n'est écrit.
- **Données personnelles (loi 09-08 / CNDP)** : le fichier est un **export de données
  nominatives** (identité complète avec CIN et date de naissance, choix explicite du PO pour les
  besoins des clients publics). Réservé à `admin` et `direction` ; chaque export est tracé ; nom
  de fichier anonyme ; `Cache-Control: no-store`. Le fichier quitte ensuite l'application : sa
  conservation et sa transmission relèvent des procédures internes de Mafoa (rappel à ajouter au
  registre des traitements, à valider avec KAN-31).
- **IA** : non utilisée. Le verdict exporté est celui du moteur déterministe ; il est présenté
  comme distinct de la décision humaine (règle 11).
- **Biais** : aucune colonne sexe, région, photo, situation familiale ni âge (règle 5, CA7).

## Hors périmètre

- Exports PDF et courriers, formats imposés par chaque client public (autre story des exports).
- Export des offres d'emploi (recrutement privé) et du tableau de bord (KAN-38).
- Scores sur grille (cas d'usage 3) : pas encore de notation, colonnes à ajouter avec la story
  correspondante.
- Pseudonymisation de l'export, export en tâche de fond Celery pour les très gros volumes.
- Historique complet des décisions ou des vérifications (seules les dernières sont exportées).

## Questions ouvertes

Toutes tranchées le 2026-11-23 (choix du PO pour Q1 et Q2, proposition par défaut pour les
autres).

- [x] **Q1 — Données du candidat** : *Choix du PO* : identité complète avec date de naissance
  (nom, prénom, CIN, date de naissance, email, téléphone, ville) + formation et expériences
  déclarées. Jamais sexe, photo, situation familiale (ni région).
- [x] **Q2 — Résultat exporté** : *Choix du PO* : les deux — décision humaine en vigueur (statut,
  motif, auteur, date) **et** verdict du moteur avec attendu / constaté / verdict / raison par
  critère.
- [x] **Q3 — L'export par poste applique-t-il les filtres de la liste ?** : *Proposition par
  défaut* : oui, bouton « Exporter en Excel (filtres appliqués) » sur la liste ; l'export complet
  se fait depuis la fiche concours (ou sans filtre). *Alternative* : export toujours complet.
- [x] **Q4 — Colonnes multi-valeurs** : *Proposition par défaut* : une colonne texte lisible
  (une entrée par ligne) pour les formations et pour les expériences, plus l'expérience calculée
  en nombre. *Alternative* : N colonnes par diplôme / expérience (largeur variable, peu lisible).
- [x] **Q5 — Onglet « Synthèse »** : *Proposition par défaut* : oui, en tête du fichier concours
  (postes, nombre de candidatures, répartition statuts / verdicts).
- [x] **Q6 — Mise en forme** : *Proposition par défaut* : en-têtes figés, filtres automatiques,
  largeurs fixées, couleurs de statut sobres ; noms d'onglets ≤ 31 caractères et uniques.
- [x] **Q7 — Anomalies** : *Proposition par défaut* : nombre et texte des anomalies **non
  résolues** seulement.
- [x] **Q8 — Plafond** : *Proposition par défaut* : 20 000 candidatures par fichier,
  configurable ; au-delà, restreindre les filtres. *Alternative* : export asynchrone (Celery) et
  lien de téléchargement, plus tard si besoin.
