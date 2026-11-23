# KAN-10 — Liste des candidatures d'un poste : filtres, tri, recherche

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-10
- **Epic** : E2 — Écrans concours et éligibilité
- **Statut** : brouillon (rédigée le 2026-11-23, en attente de validation par le Product Owner)

## Contexte

Sur un poste de concours public, les candidatures se comptent par centaines ou par milliers.
La direction doit pouvoir isoler rapidement les dossiers à traiter (par exemple « à vérifier
avec anomalies »), retrouver un candidat par son nom et parcourir la liste sans attendre.
C'est la suite de l'écran 2 du brief (`CLAUDE.md` §7 : « Concours → postes → liste des
candidatures avec filtres (statut, éligibilité, score, anomalies) et actions en lot »).
Les actions en lot font l'objet de **KAN-15** ; la fiche d'un concours, de **KAN-9**.

Existant dans l'application (à réutiliser, ne pas recréer) :

- **Modèles** (`apps/recruitment/models.py`) :
  - `PosteConcours` (rattaché à un `Concours`).
  - `Candidature` : `candidat`, `poste_concours`, `date_candidature`, `statut`
    (reçue, en analyse, éligible, non éligible, à vérifier, présélectionnée, convoquée,
    retenue, rejetée).
  - `Candidat` : `nom`, `prenom`, et `nom_normalise` (nom + prénom sans accents ni casse,
    mots triés : « EL AMRANI Sara » = « Sara El Amrani »), indexé. Contient aussi email,
    téléphone, CIN, date de naissance, ville, région, sexe : **non affichés ici**.
- **Évaluation** (`apps/evaluation/models.py`) : `VerificationEligibilite` (historique des
  verdicts du moteur : éligible / non éligible / à vérifier), `Anomalie` (type, gravité
  info / moyenne / haute, `resolue`), `Decision` (humaine, motif obligatoire).
- **Normalisation** : `normalize_text` (`apps/evaluation/eligibility/levels.py`) et
  `normalize_name` (`apps/recruitment/normalization.py`).
- **Permissions, MFA, audit, front** : identiques à KAN-9 (`role_required`, middlewares,
  `apps.audit.services.log`, Django + HTMX + Tailwind, `base.html` RTL-ready).

Aucune vue de liste de candidatures n'existe aujourd'hui (seul l'admin Django, réservé à
`admin`, permet de les parcourir).

## User story

En tant que membre de la direction, je veux la liste des candidatures d'un poste, filtrable par
statut, verdict d'éligibilité et présence d'anomalies, triable et avec recherche par nom, afin
de traiter rapidement les dossiers.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Lecture de la liste des candidatures de tout poste. |
| direction | Lecture de la liste des candidatures de tout poste. |
| consultant, client (vague 2, inactifs) | Refusé : 403. |
| utilisateur inactif | Refusé (session refusée par l'authentification). |
| non connecté | Redirection vers la page de connexion. |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP. |

Contrôle côté serveur sur la page complète **et** sur chaque réponse partielle HTMX.

## Règles métier

### Accès et adressage

1. La liste est adressée par l'identifiant du concours et celui du poste
   (forme indicative : `/concours/<id>/postes/<id>/candidatures/`). Si le poste n'existe pas ou
   n'appartient pas au concours de l'URL : 404.
2. Accès depuis la fiche concours (KAN-9) : lien par poste et liens préfiltrés par verdict
   (paramètre de verdict dans l'URL, voir règle 9).
3. En-tête : fil d'Ariane « Tableau de bord › <concours> › <référence — intitulé du poste> »,
   nombre de postes à pourvoir, date limite (règle de repli de KAN-9), et le compteur
   « N candidatures affichées sur M » (N après filtres et recherche, M total du poste).

### Colonnes

4. Une ligne par candidature du poste, avec :
   - **Candidat** : NOM en majuscules puis prénom (format de `Candidat.__str__`) ;
   - **Date de candidature** (jj/mm/aaaa hh:mm, fuseau de l'application) ;
   - **Statut** : libellé de `Candidature.statut` ;
   - **Verdict d'éligibilité** : dernier verdict du moteur (définition de KAN-9 règle 5 :
     dernière `VerificationEligibilite` par `created_at` puis identifiant), ou « Non évaluée » ;
   - **Anomalies** : nombre d'anomalies **non résolues** et indicateur de la gravité la plus
     haute parmi elles (haute / moyenne / info) ; vide si aucune.
5. Le nom du candidat sera le lien vers la fiche candidature (KAN-11 / écran 3). Tant que
   la fiche n'existe pas, le nom est affiché sans lien.
6. Aucune autre donnée personnelle n'est affichée (ni email, téléphone, CIN, date de
   naissance, âge, ville, région, sexe, photo).

### Filtres

7. **Statut** : choix multiple parmi les 9 statuts de `Candidature.Statut`. Plusieurs valeurs
   cochées = OU.
8. **Verdict d'éligibilité** : choix multiple parmi éligible, non éligible, à vérifier,
   non évaluée. Plusieurs valeurs = OU.
9. **Anomalies** : « toutes » (défaut), « avec anomalie non résolue », « sans anomalie non
   résolue ». Les anomalies résolues sont ignorées pour ce filtre.
10. Les différents filtres et la recherche se combinent en ET.
11. L'état complet de la liste (filtres, recherche, tri, page, taille de page) est porté par
    les paramètres de l'URL (GET) : un lien copié ou mis en favori rouvre exactement la même
    vue ; les boutons précédent / suivant du navigateur fonctionnent.
12. Un bouton « Réinitialiser » supprime filtres, recherche et tri (retour aux valeurs par
    défaut, page 1).
13. Une valeur de paramètre inconnue ou invalide (statut, verdict, tri, page, taille) est
    ignorée et remplacée par la valeur par défaut, sans erreur 500 ni 400.

### Recherche par nom

14. Un champ unique recherche dans le nom et le prénom du candidat, **sans tenir compte des
    accents, de la casse ni de l'ordre des mots** : la saisie est normalisée comme
    `nom_normalise`, découpée en mots, et chaque mot doit être contenu dans `nom_normalise`.
    Exemples : « amrani » et « Sara el-amrani » et « ÉL AMRANI » trouvent « EL AMRANI Sara ».
15. Recherche déclenchée à partir de 2 caractères utiles ; en dessous, aucun filtrage par nom.
    Saisie limitée à 100 caractères.
16. La recherche ne porte que sur le nom et le prénom (pas sur l'email, le téléphone ni le CIN :
    voir question ouverte Q3).

### Tri

17. Colonnes triables, en ordre croissant ou décroissant : candidat (`nom_normalise`), date de
    candidature, statut (ordre de déclaration du parcours : reçue → rejetée), verdict
    (à vérifier, non éligible, éligible, non évaluée), anomalies (nombre non résolues).
18. Tri par défaut : **date de candidature croissante** (premier arrivé, premier traité ; voir Q1).
19. Un critère de départage stable (identifiant de la candidature) est toujours ajouté, pour
    qu'une même candidature n'apparaisse jamais sur deux pages et n'en disparaisse pas à
    données inchangées.
20. Aucun tri ni filtre sur sexe, âge, région, ville, photo ou situation familiale.

### Pagination et performance

21. Pagination côté serveur : 50 lignes par page par défaut, tailles au choix 25 / 50 / 100,
    100 au maximum. Une page au-delà de la dernière affiche la dernière page ; une page non
    numérique affiche la page 1.
22. Le nombre de requêtes SQL pour afficher une page est **constant** : il ne dépend ni du
    nombre de candidatures du poste ni de la taille de la page (verdict et anomalies calculés
    en base, pas de requête par ligne).
23. Filtrage, tri, recherche et comptage sont faits **en base**, jamais en Python sur la liste
    complète.

### Interaction (HTMX, sans SPA)

24. Modifier un filtre, le tri, la page ou la recherche remplace uniquement la zone « tableau +
    pagination + compteur » via une requête HTMX, et met à jour l'URL (règle 11). La recherche
    attend une pause de frappe d'environ 300 ms avant d'interroger le serveur.
25. Sans JavaScript, la page reste utilisable : le formulaire de filtres est un `GET` classique
    qui recharge la page complète.
26. Une requête HTMX reçoit un fragment HTML (pas la page complète) ; une requête normale reçoit
    la page complète. Les deux appliquent les mêmes contrôles d'accès et la même journalisation.

### Préparation des actions en lot (KAN-15)

27. Chaque ligne porte l'identifiant de sa candidature dans un attribut HTML stable
    (`data-candidature-id`), et la mise en page réserve une première colonne (vide dans ce
    ticket) destinée aux cases à cocher.
28. L'interprétation des paramètres de filtre / recherche en ensemble de candidatures est une
    logique serveur **unique et réutilisable**, testée indépendamment de l'affichage, afin que
    KAN-15 puisse appliquer une action à « toutes les candidatures correspondant aux filtres »
    exactement comme elles sont listées.
29. Aucune action de modification (statut, décision, relance du moteur, export) n'est proposée
    dans ce ticket ; la liste est en lecture seule.

## Critères d'acceptation

Jeu de données de référence (fictif) : concours C1, poste P1 (référence « P-01 ») avec
6 candidatures, et un poste P2 du même concours avec 1 candidature « DUPONT Paul ».

| Candidat (fictif) | Date de candidature | Statut | Dernier verdict | Anomalies non résolues |
|---|---|---|---|---|
| EL AMRANI Sara | 2026-02-01 09:00 | à vérifier | à vérifier | 1 haute |
| BENNANI Youssef | 2026-02-02 10:00 | éligible | éligible | — (1 résolue) |
| ÉTTAHIRI Imane | 2026-02-03 11:00 | non éligible | non éligible | 1 moyenne |
| ALAOUI Karim | 2026-02-04 12:00 | convoquée | éligible | — |
| ZIANI Nora | 2026-02-05 13:00 | reçue | (aucune vérification) | — |
| EL AMRANI Omar | 2026-02-06 14:00 | à vérifier | à vérifier | 2 (info, moyenne) |

**Affichage et accès**

- **CA1** — Étant donné un utilisateur `direction` connecté avec TOTP, quand il ouvre la liste de
  P1 sans paramètre, alors la réponse est 200, le compteur affiche « 6 candidatures affichées
  sur 6 » et les lignes sont dans l'ordre Sara, Youssef, Imane, Karim, Nora, Omar (date de
  candidature croissante).
- **CA2** — Étant donné la liste de P1, quand elle est affichée, alors chaque ligne présente le
  nom « NOM Prénom », la date de candidature, le libellé du statut, le verdict et les
  anomalies ; la ligne ZIANI Nora affiche « Non évaluée » ; la ligne BENNANI Youssef n'affiche
  aucune anomalie (l'anomalie résolue n'est pas comptée) ; la ligne EL AMRANI Omar affiche
  2 anomalies avec l'indicateur de gravité « moyenne ».
- **CA3** — Étant donné ALAOUI Karim (statut « convoquée », dernier verdict « éligible »), quand
  la liste est affichée, alors la colonne statut indique « Convoquée » et la colonne verdict
  « Éligible ».
- **CA4** — Étant donné une candidature ayant une vérification « non éligible » puis une plus
  récente « à vérifier », quand la liste est affichée, alors son verdict est « À vérifier ».
- **CA5** — Étant donné la liste de P1, quand elle est affichée, alors DUPONT Paul (poste P2)
  n'y figure pas.
- **CA6** — Étant donné le poste P2 et l'identifiant du concours d'un autre concours dans l'URL,
  quand la liste est demandée, alors la réponse est 404 ; de même pour un identifiant de poste
  inexistant.
- **CA7** — Étant donné le jeu de référence, quand le HTML de la liste est inspecté, alors il ne
  contient ni email, ni téléphone, ni CIN, ni date de naissance, ni ville, ni région, ni sexe
  des candidats.
- **CA8** — Étant donné un utilisateur `admin` connecté avec TOTP, quand il ouvre la liste, alors
  la réponse est 200 ; étant donné un utilisateur `consultant` ou `client`, alors la réponse
  est 403, aussi bien pour la page complète que pour une requête portant l'en-tête
  `HX-Request: true`.
- **CA9** — Étant donné un utilisateur non connecté, quand il demande la liste, alors il est
  redirigé vers la connexion avec `next` ; étant donné un utilisateur `direction` sans TOTP
  actif, alors il est redirigé vers l'activation du TOTP.

**Filtres**

- **CA10** — Étant donné le jeu de référence, quand le filtre statut = « à vérifier » est
  appliqué, alors seules Sara et Omar sont listées et le compteur indique « 2 … sur 6 ».
- **CA11** — Étant donné le jeu de référence, quand le filtre statut = « éligible » OU
  « convoquée » est appliqué, alors seuls Youssef et Karim sont listés.
- **CA12** — Étant donné le jeu de référence, quand le filtre verdict = « éligible » est
  appliqué, alors Youssef et Karim sont listés ; quand le filtre verdict = « non évaluée » est
  appliqué, alors seule Nora est listée.
- **CA13** — Étant donné le jeu de référence, quand le filtre anomalies = « avec anomalie non
  résolue » est appliqué, alors Sara, Imane et Omar sont listés ; avec « sans anomalie non
  résolue », Youssef, Karim et Nora sont listés.
- **CA14** — Étant donné le jeu de référence, quand verdict = « à vérifier » ET anomalies =
  « avec anomalie non résolue » ET recherche = « amrani » sont appliqués, alors Sara et Omar
  sont listés ; en ajoutant statut = « reçue », la liste est vide.
- **CA15** — Étant donné une URL contenant `statut=inconnu`, `verdict=xyz`, `tri=sexe`,
  `page=abc` et une taille de page de 1000, quand la liste est demandée, alors la réponse est
  200, les valeurs invalides sont ignorées (aucun filtre appliqué, tri par défaut, page 1) et
  au plus 100 lignes sont affichées.
- **CA16** — Étant donné la fiche concours KAN-9, quand l'utilisateur suit le lien du compteur
  « à vérifier » de P1, alors la liste s'ouvre avec le filtre verdict « à vérifier » déjà
  appliqué et visible comme coché dans le formulaire.
- **CA17** — Étant donné des filtres appliqués, quand l'utilisateur clique sur « Réinitialiser »,
  alors les 6 candidatures sont de nouveau listées dans l'ordre par défaut, page 1.

**Recherche**

- **CA18** — Étant donné le jeu de référence, quand la recherche vaut « amrani », alors Sara et
  Omar sont listés ; quand elle vaut « sara el-amrani » ou « EL AMRANI SARA », alors seule Sara
  est listée.
- **CA19** — Étant donné ÉTTAHIRI Imane, quand la recherche vaut « ettahiri », alors elle est
  listée (insensible aux accents et à la casse).
- **CA20** — Étant donné le jeu de référence, quand la recherche vaut « a » (1 caractère), alors
  les 6 candidatures sont listées (pas de filtrage par nom) ; quand elle vaut un nom absent,
  alors le message « Aucune candidature ne correspond à ces critères. » s'affiche avec le bouton
  « Réinitialiser ».

**Tri**

- **CA21** — Étant donné le jeu de référence, quand le tri « candidat croissant » est demandé,
  alors l'ordre suit `nom_normalise` croissant ; en décroissant, l'ordre est exactement inverse.
- **CA22** — Étant donné le jeu de référence, quand le tri « anomalies décroissant » est demandé,
  alors Omar (2) apparaît en premier, suivi de Sara et Imane (1, départagés par identifiant),
  puis les candidatures sans anomalie non résolue.
- **CA23** — Étant donné le tri par verdict croissant, quand la liste est affichée, alors les
  « à vérifier » viennent en premier, puis « non éligible », « éligible », « non évaluée ».
- **CA24** — Étant donné le tri par date de candidature décroissante, quand la liste est
  affichée, alors Omar est en premier et Sara en dernier.

**Pagination et performance**

- **CA25** — Étant donné un poste de 120 candidatures, quand la liste est demandée sans
  paramètre, alors 50 lignes sont affichées, la pagination indique 3 pages, et la page 3 en
  contient 20 ; avec la taille 100, la page 2 en contient 20 ; avec `page=99`, la page 3 est
  affichée.
- **CA26** — Étant donné un poste de 120 candidatures dont toutes les dates de candidature sont
  identiques, quand on parcourt les 3 pages au tri par défaut, alors chaque candidature
  apparaît exactement une fois (départage stable).
- **CA27** — Étant donné un poste de 10 candidatures et un poste de 1 000 candidatures (avec
  vérifications et anomalies), quand une page de 50 puis de 100 lignes est affichée, alors le
  nombre de requêtes SQL est identique dans tous les cas et borné par une constante fixée dans
  le test.
- **CA28** — Étant donné un poste de 5 000 candidatures fictives sur l'environnement de
  développement, quand la liste est affichée avec un filtre, un tri et une recherche, alors le
  temps de réponse serveur est inférieur à 1 s (vérification explicite, non bloquante en CI).

**HTMX et URL**

- **CA29** — Étant donné une requête portant l'en-tête `HX-Request: true` avec un filtre, quand
  la réponse est rendue, alors elle contient le tableau, le compteur et la pagination, mais pas
  le gabarit complet (pas de `<html>` ni d'en-tête de navigation) ; la même URL sans cet
  en-tête renvoie la page complète avec les mêmes lignes.
- **CA30** — Étant donné une liste filtrée, triée et paginée, quand son URL est rouverte dans
  une nouvelle session du même utilisateur, alors le même ensemble de lignes s'affiche dans le
  même ordre, avec les contrôles du formulaire dans l'état correspondant.

**Préparation KAN-15**

- **CA31** — Étant donné la liste affichée, quand le HTML est inspecté, alors chaque ligne porte
  un attribut `data-candidature-id` égal à l'identifiant de la candidature.
- **CA32** — Étant donné un jeu de paramètres de filtre et de recherche, quand la logique serveur
  de filtrage est appelée directement (hors vue), alors elle renvoie exactement l'ensemble des
  candidatures que la vue liste pour ces paramètres, toutes pages confondues.

**Audit**

- **CA33** — Étant donné un utilisateur `direction`, quand il affiche la liste (page complète ou
  fragment HTMX), alors une entrée `JournalAudit` `view` est créée avec l'acteur, l'IP, l'objet
  `PosteConcours` concerné et, en métadonnées, les filtres, la recherche, le tri, la page et le
  nombre de résultats.
- **CA34** — Étant donné une requête refusée (403), introuvable (404) ou redirigée, quand elle est
  traitée, alors aucune entrée d'audit `view` n'est créée.

**Langue**

- **CA35** — Étant donné la langue active `ar`, quand la liste est affichée, alors la balise
  `html` porte `dir="rtl"`, tous les libellés fixes passent par le système de traduction, et la
  mise en page n'utilise pas de classes d'alignement figées à gauche/droite là où une variante
  logique existe (`text-start`/`text-end`, `ms-`/`me-`…).

## Cas limites et erreurs

- **Poste sans candidature** : message « Aucune candidature pour ce poste. », sans formulaire de
  filtres actif (ou désactivé), compteur « 0 sur 0 ».
- **Filtres sans résultat** : message dédié et bouton « Réinitialiser » (CA20).
- **Paramètres invalides ou malveillants** : ignorés (CA15). La recherche est passée en paramètre
  de requête SQL, jamais concaténée ; les valeurs réaffichées sont échappées (pas d'XSS via la
  recherche).
- **Recherche avec caractères spéciaux** (apostrophes, tirets, arabe translittéré) : normalisée
  comme `nom_normalise` ; une saisie qui ne contient plus aucun mot après normalisation n'applique
  aucun filtre.
- **Candidat sans prénom** ou nom très long : affichage sans erreur, troncature visuelle avec
  le nom complet en infobulle.
- **Plusieurs vérifications par candidature** : seule la dernière compte (CA4).
- **Homonymes** : deux candidats de même nom apparaissent sur deux lignes distinctes ; la
  détection de doublons relève des anomalies (déjà modélisées), pas de cette liste.
- **Volumes importants** : plusieurs milliers de candidatures par poste (CA25 à CA28). Les index
  nécessaires (par exemple sur la dernière vérification par candidature et sur les anomalies
  non résolues par candidature) sont à prévoir par le développeur s'ils manquent ; toute
  migration d'index fait partie de ce ticket.
- **Données modifiées entre deux pages** (synchronisation, moteur, décision prise par un autre
  membre) : une ligne peut changer de page ou sortir d'un filtre ; c'est accepté, la page
  affichée est cohérente à l'instant de la requête. Le compteur est recalculé à chaque requête.
- **Accès concurrent** : la liste est en lecture seule, aucun verrou n'est nécessaire.
- **Méthodes HTTP** : seule `GET` est acceptée ; `POST` renvoie 405 (les actions en lot de KAN-15
  utiliseront leur propre point d'entrée).
- **Droits insuffisants** : CA8, CA9, CA34.

## Traçabilité et conformité

- **Journal d'audit** : une entrée `view` par réponse servie (page complète ou fragment HTMX),
  objet = `PosteConcours`, métadonnées = filtres, recherche, tri, page, taille, nombre de
  résultats. Pas d'avant/après (lecture seule). La consultation **d'un dossier individuel**
  sera journalisée par la fiche candidature (KAN-11), pas ici.
- **Données personnelles** : seuls nom et prénom sont affichés (minimisation, CA7), à des
  utilisateurs `admin` / `direction` authentifiés avec TOTP. Le terme de recherche (un nom)
  est conservé dans le journal d'audit pour la traçabilité des consultations (voir Q4) ; il
  n'est stocké nulle part ailleurs (pas de cache, pas de `localStorage`). La page reste
  marquée `noindex` (gabarit de base).
- **IA** : non utilisée. Les verdicts proviennent du moteur déterministe existant ; les
  anomalies, des détections existantes.
- **Humain dans la boucle** : « non éligible » est affiché comme verdict du moteur, distinct du
  statut ; aucune élimination ni décision n'est possible depuis cette liste.
- **Biais** : aucun tri, filtre ni colonne fondé sur le sexe, l'âge, la région, la ville, la
  photo ou la situation familiale (règle 20, CA7, CA15 avec `tri=sexe`).

## Hors périmètre

- Actions en lot (sélection, changement de statut, décision, export de la sélection) : **KAN-15**.
  Seule la préparation (règles 27 et 28) est dans ce ticket.
- Fiche candidature (visionneuse de pièces, détail du verdict, décision) : **KAN-11** / écran 3.
- Filtre et tri par **score** sur grille : à ajouter quand le scoring (epic E3) sera livré
  (cité au §7 du brief ; voir Q2).
- Exports PDF/Excel des listes : epic exports.
- Liste des candidatures d'une **offre d'emploi** (recrutement privé).
- Recherche sémantique dans le vivier (vague 2).
- Mémorisation des filtres d'un utilisateur entre sessions, vues enregistrées.

## Questions ouvertes

- [ ] **Q1 — Tri par défaut** : date de candidature croissante (premier arrivé, premier traité),
  décroissante (plus récentes d'abord, cohérent avec l'ordre du modèle) ou nom alphabétique
  (habituel des listes publiées) ? **Proposition par défaut** : date croissante. Conséquence
  nulle sur le coût ; seul l'ordre initial change.
- [ ] **Q2 — Score** : le brief cite un filtre par score sur cet écran. **Proposition par
  défaut** : hors périmètre tant que le scoring (E3) n'est pas livré ; un ticket ajoutera la
  colonne, le filtre (note minimale) et le tri. Conséquence : la liste devra être étendue, ce
  que la règle 28 facilite.
- [ ] **Q3 — Recherche par CIN ou email** : utile pour retrouver un candidat qui appelle.
  **Proposition par défaut** : non dans ce ticket (le ticket demande « par nom », et la
  minimisation CNDP plaide pour ne pas l'étendre) ; à réévaluer avec la fiche candidature.
  Si oui : correspondance exacte uniquement, sans afficher la valeur dans la liste.
- [ ] **Q4 — Terme de recherche dans le journal d'audit** : le conserver (traçabilité de « qui a
  cherché qui ») ou le masquer (minimisation) ? **Proposition par défaut** : le conserver, le
  journal étant en ajout seul et réservé à `admin`. Conséquence : le journal contient des noms
  de candidats, à couvrir par la politique de conservation et de purge (CNDP).
- [ ] **Q5 — Volume de journalisation** : une entrée par fragment HTMX (chaque changement de
  filtre, de page ou de recherche) peut générer des dizaines d'entrées par séance de travail.
  **Proposition par défaut** : oui, une entrée par réponse servie (règle simple et complète) ;
  alternative : n'enregistrer que les pages complètes et les changements de page.
- [ ] **Q6 — Colonne « dernière décision »** : faut-il afficher l'auteur et la date de la
  dernière décision humaine ? **Proposition par défaut** : non (le statut suffit pour trier le
  travail ; le détail est sur la fiche candidature).
- [ ] **Q7 — Statuts par défaut** : faut-il masquer par défaut les candidatures déjà tranchées
  (retenue, rejetée) ? **Proposition par défaut** : non, aucun filtre par défaut, pour que le
  compteur corresponde aux chiffres de la fiche concours (KAN-9).
