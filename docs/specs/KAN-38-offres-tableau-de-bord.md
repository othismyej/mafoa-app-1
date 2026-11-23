# KAN-38 — Écran des offres d'emploi et candidatures privées ; tableau de bord clarifié

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-38
- **Epic** : E2 — Écrans concours et éligibilité
- **Statut** : validée le 2026-11-23 par le Product Owner (PO : « tout faire maintenant » ; la
  proposition par défaut est retenue pour chaque question ouverte)

## Contexte

Retour du Product Owner après la recette de KAN-15 (verbatim) : « je ne vois pas les
candidatures spontanées ? et aussi les candidatures privées, je sais pas quand je vois le
tableau de bord, à quoi cela fait référence ».

Constat :

- Les candidatures sur **offres d'emploi** (recrutement privé, `Candidature.offre`) existent en
  base (données fictives de `FixtureSource`) mais **aucun écran ne les montre** : la seule
  liste de candidatures (KAN-10) est rattachée à un poste de concours.
- Le **tableau de bord** (`apps/accounts/views.py::dashboard`,
  `templates/dashboard/index.html`) affiche quatre tuiles **non cliquables** ; le compteur
  « Candidatures » additionne sans le dire concours publics et offres privées ; « Offres
  d'emploi (privé) » n'est expliqué nulle part et ne mène nulle part.
- Les **candidatures spontanées** (sans offre ni poste) font l'objet de **KAN-39**, en
  parallèle, qui modifiera le modèle `Candidature`. Ce ticket ne touche pas au modèle et prévoit
  seulement leur emplacement sur le tableau de bord.
- Point ouvert de la PR #9 (fiche candidature) : la fiche d'une candidature sur offre n'a pas
  de lien de retour vers une liste.

Existant à réutiliser (ne pas recréer) :

- **Modèles** (`apps/recruitment/models.py`) : `OffreEmploi` (organisation,
  `entreprise_confidentielle`, intitulé, ville, `type_contrat`, `mode_travail`, secteur,
  fonction, `nombre_postes`, salaire, `fiche_de_poste`, `competences`, critères
  d'éligibilité, dates, `statut` ouverte / fermée / pourvue) ; `Candidature` (`poste_concours`
  XOR `offre`, `statut`).
- **Liste des candidatures** (KAN-10) : `apps/recruitment/filters.py` (`CandidatureFilter`,
  `filter_candidatures(poste, params)`, annotations `dernier_verdict`, `nb_anomalies`,
  `gravite_max`), vue `poste_candidatures`, gabarits `poste_candidatures.html` (formulaire
  `#filtres`) et `_candidatures_resultats.html` (zone `#resultats`, fragment HTMX).
- **Fiche candidature** (KAN-11/12/13) : `candidature_detail`, déjà compatible avec les offres
  (décision, quota « nombre de postes » de l'offre).
- **Actions en lot** (KAN-15) : `apps/evaluation/lots.py` et `lot_views.py`, rattachés au
  poste (jeton signé portant `poste.pk`, tâche Celery `poste_id`, contrôle « concours
  terminé »).
- **Audit** (KAN-14) : `apps.audit.services` — `log`, `log_consultation`, `deny`,
  `role_required_logged`.
- **Charte** : `docs/design/charte.md` (composants `stat`, `badge`, `breadcrumb`, tableaux).

## User story

En tant que membre de la direction, je veux voir les offres d'emploi de nos clients privés et
les candidatures reçues sur chacune, et comprendre d'un coup d'œil ce que représente chaque
chiffre du tableau de bord, afin de piloter le recrutement privé comme les concours publics.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Tableau de bord, liste des offres, page d'une offre (avec ses candidatures), page « Dossiers à vérifier ». |
| direction | Idem admin. |
| consultant, client (vague 2, inactifs) | Refusé : 403, avec une entrée `access_denied` au journal pour les trois nouveaux écrans (page complète et fragment HTMX). |
| utilisateur inactif | Refusé (session refusée par l'authentification). |
| non connecté | Redirection vers la page de connexion (avec `next`). |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP. |

Contrôle côté serveur sur chaque vue, y compris les réponses partielles HTMX.

## Règles métier

### A. Vocabulaire affiché (pour un dirigeant non technique)

1. Deux familles de recrutement, nommées de la même façon partout :
   - **« Concours publics »** : e-Concours organisés pour des entités publiques (ex. SRM), avec
     des postes ;
   - **« Offres d'emploi (recrutement privé) »** : postes à pourvoir chez nos clients
     entreprises. Dans les tableaux et le menu, le libellé court est « Offres d'emploi ».
2. « Dossiers à vérifier » = candidatures dont le **statut** est « À vérifier » : le moteur
   d'éligibilité a signalé un doute et **aucune décision humaine** n'a encore tranché (le
   moteur ne reclasse plus une candidature qui a une décision, KAN-13). C'est la définition
   déjà utilisée par le tableau de bord ; elle est reprise pour les offres (voir Q2).
3. Entreprise d'une offre : nom de l'organisation, ou **« Entreprise confidentielle »** si
   `entreprise_confidentielle` est vrai (comme sur le site). La fiche candidature d'une offre
   confidentielle adopte le même libellé français (au lieu de « Confidential Company »).

### B. Liste des offres d'emploi

4. Adresse indicative `/offres/`, accessible depuis le menu principal (« Offres d'emploi »),
   la tuile « Offres d'emploi » du tableau de bord et la section « Offres d'emploi ».
5. Une ligne par offre : **Entreprise** (règle 3), **Offre** (intitulé, lien vers la page de
   l'offre), **Ville**, **Contrat** (`type_contrat`), **Statut** (badge Ouverte / Fermée /
   Pourvue), **Date limite** (jj/mm/aaaa ou « — »), **Candidatures** (nombre),
   **À vérifier** (nombre, règle 2 ; mis en évidence s'il est non nul).
6. Filtre **Statut** : choix multiple parmi ouverte, fermée, pourvue (OU) ; aucun filtre par
   défaut. Valeur inconnue ignorée, sans erreur. État porté par l'URL (GET) ; bouton
   « Réinitialiser ». Sans JavaScript, le formulaire est un GET classique.
7. Ordre : offres **ouvertes** d'abord, puis par date de publication décroissante, puis
   identifiant décroissant (départage stable).
8. Pagination serveur de 50 offres ; une page invalide affiche la page 1, une page au-delà de
   la dernière affiche la dernière.
9. Compteur « N offres affichées sur M ». Comptages faits en base ; nombre de requêtes SQL
   **constant** (indépendant du nombre d'offres et de candidatures).
10. La liste n'affiche **aucune donnée de candidat** (seulement des nombres).

### C. Page d'une offre et liste de ses candidatures

11. Adresse indicative `/offres/<id>/` ; 404 si l'offre n'existe pas.
12. Fil d'Ariane : « Tableau de bord › Offres d'emploi › <intitulé> ».
13. **Fiche de poste résumée** : entreprise (sur-titre), intitulé (titre), statut, ville,
    contrat, mode de travail, postes à pourvoir, date limite ; carte « Fiche de poste » :
    début du texte (environ 60 mots) avec un dépliant « Lire la fiche complète » (élément
    `<details>`, sans JavaScript), compétences attendues (pastilles), critères d'éligibilité
    (niveau d'études minimum, expérience minimale / maximale, autres critères) quand ils sont
    renseignés, salaire s'il est renseigné.
14. **Liste des candidatures de l'offre** : mêmes colonnes, filtres (statut, verdict,
    anomalies), recherche par nom, tri, pagination, compteur « N affichées sur M », messages
    vides, fragment HTMX et paramètres d'URL que la liste d'un poste (KAN-10 règles 4 à 26),
    avec **la même logique serveur et les mêmes gabarits** (règle 16). Le message « aucune
    candidature » dit « Aucune candidature pour cette offre. ».
15. Le nom du candidat est le lien vers la fiche candidature existante (KAN-11/12/13).
16. **Généralisation de la logique de filtrage** : `filter_candidatures(cible, params)` accepte
    une **cible** qui est un `PosteConcours` ou une `OffreEmploi` (tout autre type : erreur de
    programmation `TypeError`). Le comportement pour un poste est strictement inchangé (tous
    les tests KAN-10 et KAN-15 restent verts). Les gabarits du formulaire de filtres et des
    résultats sont partagés (inclusion), la barre d'actions en lot n'y apparaissant que pour un
    poste.
17. **Actions en lot** : non proposées sur une offre dans ce ticket (voir Q1). La page n'a ni
    barre d'actions en lot ni cases à cocher ; la première colonne (liseré « à vérifier »)
    reste présente pour garder la même mise en page.
18. **Retour depuis la fiche** (point ouvert de la PR #9) : la fiche d'une candidature sur
    offre affiche le fil d'Ariane « Tableau de bord › Offres d'emploi › <offre> › <candidat> »,
    le lien de l'offre (attribut `data-retour-liste`) menant à la page de l'offre. Les fiches
    de candidature sur poste sont inchangées.

### D. Tableau de bord

19. Sous le titre, une phrase explique les deux familles (règle 1).
20. Quatre tuiles, chacune avec un libellé explicite, un chiffre et un sous-texte :
    - **« Concours publics »** : nombre de concours ; sous-texte « N postes · M en cours » ;
      tuile cliquable vers la section « Concours » de la page ;
    - **« Offres d'emploi (recrutement privé) »** : nombre d'offres ; sous-texte « N ouvertes » ;
      tuile cliquable vers la liste des offres ;
    - **« Candidatures reçues »** : total ; ventilation affichée dans la tuile,
      « Concours publics : X » (lien vers la section « Concours ») et « Offres d'emploi : Y »
      (lien vers la liste des offres) ; X + Y = total tant qu'il n'existe que ces deux
      familles (KAN-39 ajoutera une troisième ligne) ;
    - **« Dossiers à vérifier »** : nombre (règle 2) ; sous-texte « En attente d'un examen
      humain » ; tuile cliquable vers la page « Dossiers à vérifier » (règles 23 à 26) ; ton
      « attention » (ambre) si non nul.
21. Une tuile cliquable est un **lien unique** dont le nom accessible contient le libellé et
    le chiffre ; focus visible, cible ≥ 40 px. Une tuile contenant plusieurs liens
    (« Candidatures reçues ») n'est pas elle-même un lien.
22. Sections, dans l'ordre :
    - **« Concours »** (existante, ancre `#concours`) ;
    - **« Offres d'emploi »** (nouvelle, ancre `#offres`) : les 10 offres de la liste (même
      ordre et mêmes colonnes que la règle 5) et un lien « Voir toutes les offres d'emploi » ;
    - **« Candidatures spontanées »** (emplacement réservé, ancre `#spontanees`, attribut
      `data-section="spontanees"`) : carte indiquant « Bientôt disponible : les candidatures
      envoyées sans viser une offre précise apparaîtront ici. » Aucun chiffre ni lien tant que
      KAN-39 n'est pas livré.
23. Nombre de requêtes SQL du tableau de bord **constant** (indépendant des volumes).

### E. Page « Dossiers à vérifier »

24. Adresse indicative `/a-verifier/` ; titre « Dossiers à vérifier » ; fil d'Ariane
    « Tableau de bord › Dossiers à vérifier ».
25. Une ligne par **poste de concours ou offre** ayant au moins un dossier à vérifier (règle 2) :
    **Type** (« Concours public » / « Offre d'emploi »), **Recrutement** (concours et
    « référence — intitulé » du poste, ou intitulé de l'offre et entreprise), **Dossiers à
    vérifier** (nombre), et un lien « Voir les dossiers » vers la liste des candidatures de la
    cible **préfiltrée** sur le statut « À vérifier » (`?statut=a_verifier`).
26. Tri par nombre décroissant puis libellé ; total affiché en tête (« N dossiers à vérifier »),
    égal au chiffre de la tuile. Si aucun : message « Aucun dossier à vérifier. ». Aucune donnée
    de candidat. Requêtes SQL en nombre constant.

### F. Audit, langue, accessibilité

27. **Liste des offres** et **page « Dossiers à vérifier »** (aucune donnée nominative) : une
    entrée `view` par réponse servie, sans objet, `object_repr` « Liste des offres d'emploi » /
    « Dossiers à vérifier », métadonnées : filtres, page, nombre de résultats.
28. **Page d'une offre** (liste nominative de candidats) : journalisée **comme la liste d'un
    poste** (KAN-10 règle 33, KAN-14 règle 4) : une entrée `view` par réponse servie (page
    complète ou fragment), objet = l'offre, métadonnées = filtres, recherche, tri, page, taille
    et nombre de résultats.
29. **Refus** (rôle non autorisé) sur les trois nouveaux écrans : réponse 403 **renvoyée** et
    entrée `access_denied` (`deny` / `role_required_logged`), sans entrée `view`. Une 404 ou une
    redirection ne crée aucune entrée `view`.
30. Tous les libellés passent par la traduction (`fr` par défaut, `ar` prévu) ; propriétés
    logiques uniquement (`ms-`/`me-`, `text-start`…) ; `dir="rtl"` en arabe.
31. Charte : badges de statut d'offre distincts du rouge de marque (ouverte = émeraude,
    fermée = neutre, pourvue = pierre), documentés dans `docs/design/charte.md` ; la tuile
    cliquable est un composant (`components/stat.html` avec `href`).

## Critères d'acceptation

Jeu de données de référence (fictif) :

- Organisation privée « Société fictive Atlas » ; organisation publique « SRM fictive ».
- Concours C1 (en cours) avec un poste P1 « P-01 — Ingénieur réseaux » et 3 candidatures
  (statuts : à vérifier, éligible, reçue).
- Offre O1 « Responsable RH fictif » (Société fictive Atlas, Marrakech, CDI, ouverte, publiée le
  2026-11-01, date limite 2026-12-15) avec 4 candidatures : EL AMRANI Sara (à vérifier,
  verdict à vérifier, 1 anomalie haute), BENNANI Youssef (à vérifier), ALAOUI Karim (éligible),
  ZIANI Nora (reçue).
- Offre O2 « Comptable fictif » (entreprise confidentielle, fermée, publiée le 2026-10-01) avec
  1 candidature reçue.
- Offre O3 « Chef de projet fictif » (pourvue, publiée le 2026-11-10) sans candidature.

**Liste des offres**

- **CA1** — Étant donné `direction` avec TOTP, quand il ouvre `/offres/`, alors la réponse est 200,
  le compteur indique « 3 offres affichées sur 3 » et l'ordre est O1 (ouverte), O3, O2.
- **CA2** — Étant donné la liste, alors la ligne O1 affiche « Société fictive Atlas »,
  « Marrakech », « CDI », « Ouverte », « 15/12/2026 », 4 candidatures et 2 à vérifier, et son
  intitulé est un lien vers la page de O1 ; la ligne O2 affiche « Entreprise confidentielle » et
  pas le nom de l'organisation ; O3 affiche 0 et 0.
- **CA3** — Étant donné le filtre statut = fermée, alors seule O2 est listée et la case est
  cochée ; statut = ouverte + pourvue liste O1 et O3 ; `statut=inconnu&page=abc` renvoie 200
  avec les 3 offres.
- **CA4** — Étant donné 10 puis 60 offres avec candidatures, quand la liste est affichée, alors
  le nombre de requêtes SQL est identique ; 60 offres donnent 2 pages (50 + 10).
- **CA5** — Étant donné le HTML de la liste, alors aucun nom de candidat n'y figure.

**Page d'une offre**

- **CA6** — Étant donné O1, quand `direction` ouvre sa page, alors la réponse est 200, le fil
  d'Ariane est « Tableau de bord › Offres d'emploi › Responsable RH fictif », la fiche de poste
  résumée (entreprise, ville, contrat, postes à pourvoir, date limite, statut) est affichée et
  la liste contient les 4 candidatures de O1, et aucune de P1 ni de O2 (« 4 candidatures
  affichées sur 4 »).
- **CA7** — Étant donné O1, quand le filtre statut = à vérifier est appliqué, alors seules Sara
  et Youssef sont listées ; la recherche « amrani » ne liste que Sara ; le filtre anomalies =
  « avec » ne liste que Sara.
- **CA8** — Étant donné une requête `HX-Request: true` sur la page de O1, alors la réponse est un
  fragment (sans `<html>`) contenant les lignes ; sans l'en-tête, la page complète.
- **CA9** — Étant donné `filter_candidatures(O1, params)` appelée hors vue, alors elle renvoie
  exactement les candidatures listées par la vue pour ces paramètres ; avec P1, le résultat
  est inchangé par rapport à KAN-10 ; avec un autre type d'objet, `TypeError`.
- **CA10** — Étant donné la page de O1, alors chaque nom de candidat est un lien vers sa fiche,
  et la page ne contient ni barre d'actions en lot (`data-lot-barre`) ni case `data-lot-case`.
- **CA11** — Étant donné O3 (aucune candidature), alors la page affiche « Aucune candidature pour
  cette offre. ».
- **CA12** — Étant donné un identifiant d'offre inexistant, alors 404 et aucune entrée `view`.
- **CA13** — Étant donné O1 avec 5 puis 120 candidatures (avec vérifications et anomalies),
  alors le nombre de requêtes SQL de la page est identique.
- **CA14** — Étant donné la fiche de la candidature de Sara (sur O1), alors le fil d'Ariane
  contient « Offres d'emploi » (lien vers la liste) et « Responsable RH fictif » (lien
  `data-retour-liste` vers la page de O1) ; la fiche d'une candidature de O2 affiche
  « Entreprise confidentielle ».

**Tableau de bord**

- **CA15** — Étant donné le jeu de référence, quand `direction` ouvre le tableau de bord, alors
  il affiche : « Concours publics » 1 avec « 1 poste · 1 en cours » ; « Offres d'emploi
  (recrutement privé) » 3 avec « 1 ouverte » ; « Candidatures reçues » 8 avec
  « Concours publics : 3 » et « Offres d'emploi : 5 » ; « Dossiers à vérifier » 3.
- **CA16** — Étant donné le tableau de bord, alors la tuile « Offres d'emploi » est un lien vers
  la liste des offres, la tuile « Concours publics » un lien vers `#concours`, la tuile
  « Dossiers à vérifier » un lien vers la page « Dossiers à vérifier », et la ventilation de
  la tuile « Candidatures reçues » contient un lien vers `#concours` et un vers la liste des
  offres.
- **CA17** — Étant donné le tableau de bord, alors la section « Offres d'emploi » liste O1, O3, O2
  avec leurs nombres de candidatures et un lien « Voir toutes les offres d'emploi » ; la
  section « Candidatures spontanées » (`data-section="spontanees"`) est présente avec la
  mention « Bientôt disponible ».
- **CA18** — Étant donné peu puis beaucoup de concours, offres et candidatures, alors le nombre de
  requêtes SQL du tableau de bord est identique.

**Dossiers à vérifier**

- **CA19** — Étant donné le jeu de référence, quand `direction` ouvre la page, alors elle affiche
  « 3 dossiers à vérifier », la ligne O1 (2, type « Offre d'emploi ») avant la ligne P1 (1, type
  « Concours public ») ; le lien de O1 mène à `/offres/<O1>/?statut=a_verifier` et celui de P1 à
  la liste de P1 avec `?statut=a_verifier` ; en suivant le lien de O1, seules Sara et Youssef
  sont listées.
- **CA20** — Étant donné aucune candidature à vérifier, alors « Aucun dossier à vérifier. » ;
  le nombre de requêtes SQL est identique pour 1 et 30 cibles ; aucun nom de candidat dans le
  HTML.

**Permissions et audit**

- **CA21** — Étant donné `admin`, alors les trois nouveaux écrans renvoient 200 ; étant donné
  `consultant` ou `client`, alors 403 (page complète et `HX-Request`), une entrée
  `access_denied` par refus et aucune entrée `view`.
- **CA22** — Étant donné un utilisateur non connecté, alors redirection vers la connexion avec
  `next` ; `direction` sans TOTP : redirection vers l'activation du TOTP.
- **CA23** — Étant donné `direction` qui affiche la page de O1 avec une recherche (page complète
  puis fragment), alors deux entrées `view` sont créées avec l'objet O1, la recherche, les
  filtres, la page et le nombre de résultats.
- **CA24** — Étant donné la liste des offres et la page « Dossiers à vérifier », alors une entrée
  `view` sans objet est créée pour chacune, avec les filtres et le nombre de résultats.

**Langue et charte**

- **CA25** — Étant donné la langue `ar`, alors les nouveaux écrans portent `dir="rtl"` ; les
  gabarits nouveaux ou modifiés n'utilisent aucune classe directionnelle figée
  (`ml-`, `mr-`, `pl-`, `pr-`, `left-`, `right-`, `text-left`, `text-right`) ; les nouveaux
  libellés figurent dans le catalogue `ar`.

## Cas limites et erreurs

- **Offre sans organisation visible** : impossible (clé obligatoire) ; confidentielle → règle 3.
- **Offre sans date limite / sans ville / sans contrat** : « — ».
- **Fiche de poste vide** : la carte affiche « Fiche de poste non renseignée. ».
- **Volumes** : dizaines d'offres, quelques centaines de candidatures par offre ; mêmes garanties
  de performance que KAN-10 (filtrage, tri, comptage en base).
- **Méthodes** : seules les requêtes `GET` sont acceptées (405 sinon).
- **Concurrence / synchronisation** : écrans en lecture seule, recalculés à chaque requête.
- **Paramètres invalides** : ignorés (règle 6, KAN-10 règle 13).

## Traçabilité et conformité

- **Journal d'audit** : règles 27 à 29. Le terme de recherche de la page d'une offre est
  conservé comme pour la liste d'un poste (KAN-10 Q4).
- **Données personnelles** : seuls nom et prénom des candidats, sur la page d'une offre ; aucune
  donnée nominative sur le tableau de bord, la liste des offres ni « Dossiers à vérifier ».
- **IA** : non utilisée.
- **Humain dans la boucle** : aucune décision depuis ces écrans ; « Dossiers à vérifier » oriente
  vers l'examen humain.
- **Biais** : aucun filtre, tri ni colonne fondé sur le sexe, l'âge, la région, la ville du
  candidat, la photo ou la situation familiale.

## Hors périmètre

- Candidatures spontanées (modèle, écrans, compteurs) : **KAN-39** ; seul l'emplacement est prévu.
- Actions en lot sur les candidatures d'une offre (Q1).
- Exports des listes : **KAN-37**.
- Création ou modification d'offres depuis l'application (l'app ne fait que lire le site).
- Score d'adéquation CV / fiche de poste (cas d'usage 5 du MVP) : ticket dédié.
- Liste générale des concours (la section du tableau de bord en tient lieu).

## Questions ouvertes

Toutes tranchées le 2026-11-23 : la proposition par défaut est retenue.

- [x] **Q1 — Actions en lot sur une offre** : la logique de lot (KAN-15) est liée au poste
  (jeton signé portant le poste, tâche Celery par poste, contrôle « concours terminé », refus
  journalisés par poste) ; la généraliser est un chantier à part entière. **Proposition par
  défaut** : pas d'actions en lot sur une offre dans ce ticket (volumes privés faibles,
  décisions dossier par dossier depuis la fiche) ; un ticket de suivi généralisera `lots.py` à
  une cible offre si le besoin se confirme.
- [x] **Q2 — Définition de « à vérifier »** : statut « À vérifier » (travail humain restant) ou
  dernier verdict du moteur « à vérifier » (utilisé par la fiche concours KAN-9) ?
  **Proposition par défaut** : le statut, déjà utilisé par le tableau de bord ; il disparaît
  du compteur dès qu'une décision humaine est prise. Les liens « à vérifier » de la fiche
  concours (verdict) restent inchangés.
- [x] **Q3 — Entreprise confidentielle** : afficher le vrai nom du client à la direction (outil
  interne) ou le libellé du site ? **Proposition par défaut** : le libellé « Entreprise
  confidentielle », comme sur le site, pour éviter qu'un export ou une capture ne divulgue le
  client ; à revoir si la direction en exprime le besoin.
- [x] **Q4 — Journalisation de la page d'une offre** : `log_consultation` (vue dédiée) ou même
  forme que la liste d'un poste ? **Proposition par défaut** : même forme que la liste d'un
  poste (KAN-10 / KAN-14 règle 4), pour que le journal reste homogène entre listes ; le refus
  est tracé en `access_denied`.
- [x] **Q5 — Menu principal** : ajouter une entrée « Offres d'emploi » ? **Proposition par
  défaut** : oui, entre « Tableau de bord » et « Sécurité ».
