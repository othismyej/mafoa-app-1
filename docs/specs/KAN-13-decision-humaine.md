# KAN-13 — Décision humaine motivée depuis la fiche candidature

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-13
- **Epic** : E2 — Écrans concours et éligibilité
- **Statut** : validée le 2026-11-23 par le Product Owner (réponses par défaut retenues pour toutes les questions ouvertes ; à revoir quand l'accès aux données et aux modèles du site sera disponible, voir KAN-24 et KAN-25)

## Contexte

Le brief impose que toute décision sur un dossier soit **humaine, motivée et tracée**
(`CLAUDE.md` §2, §4 « `Decision` toujours humaine », §6 « aucune élimination automatique »). Les
e-Concours publics exigent une traçabilité forte en cas de recours.

Existant dans le code (à réutiliser) :

- `apps/evaluation/models.py` — `Decision` : `candidature`, `auteur` (obligatoire, `PROTECT`),
  `decision` (choix = **tous** les statuts de `Candidature.Statut`), `motif`, `created_at`.
  Ajout seul (`save` refuse la modification), motif non vide garanti par le modèle et par une
  contrainte `CHECK` en base.
- `apps/evaluation/services.py` — `record_decision(candidature, auteur, decision, motif,
  request)` : exige un utilisateur authentifié, crée la `Decision`, recopie la décision dans
  `Candidature.statut`, journalise `AuditAction.DECISION` (avant / après = instantanés de la
  candidature, `metadata` = `decision_id`, `motif`). **Il n'applique aucune règle de transition**
  et accepte n'importe quelle valeur de statut (y compris « reçue » ou « en analyse »).
- `run_eligibility` ne reclasse le statut que s'il appartient à `AUTO_CLASSABLE` (reçue, en
  analyse, éligible, non éligible, à vérifier). **Limite constatée** : si un humain décide
  « éligible » ou « non éligible », un nouveau passage du moteur peut écraser cette décision,
  puisque ces statuts restent dans `AUTO_CLASSABLE`. Cette story corrige ce point (règle 9).
- `apps/accounts/permissions.py` : `DIRECTION_ROLES` (`admin`, `direction`).
- La fiche candidature (KAN-11) affiche l'historique des décisions et réserve l'emplacement du
  formulaire.

## User story

En tant que membre de la direction, je veux enregistrer une décision (présélectionnée,
convoquée, retenue, rejetée…) depuis la fiche, avec un motif obligatoire, afin que chaque
décision soit humaine, motivée et tracée dans le journal d'audit.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Enregistrer toute décision permise par la table des transitions (règle 4). |
| direction | Idem admin (le brief donne les « décisions » au rôle direction). |
| consultant, client (vague 2, non attribuables) | Refusé : 403, rien n'est enregistré. |
| non connecté | Redirection vers la connexion ; rien n'est enregistré. |
| processus automatique (moteur, IA, tâche Celery, synchronisation) | Jamais : `record_decision` exige un utilisateur authentifié. |

## Règles métier

1. **Décision = acte humain.** Une décision n'est créée que par une requête `POST` d'un
   utilisateur `admin` ou `direction` authentifié avec TOTP, protégée par CSRF. Aucun code
   automatique (moteur, IA, synchronisation, webhooks, tâches) n'appelle `record_decision`.
2. **Valeurs de décision proposées** (sous-ensemble de `Candidature.Statut`) :
   - décisions d'éligibilité : **éligible**, **non éligible** (validation ou requalification
     humaine du verdict du moteur) ;
   - décisions de sélection : **présélectionnée**, **convoquée**, **retenue**, **rejetée**.
   Les valeurs **reçue**, **en analyse** et **à vérifier** ne sont jamais des décisions : elles
   sont refusées par le service, même si `Decision.decision` les accepte techniquement.
3. **Motif obligatoire** : texte libre, après suppression des espaces de début et de fin, d'au
   moins 10 caractères et d'au plus 2 000 caractères. Le motif est enregistré tel que saisi
   (espaces de bord retirés).
4. **Table des transitions permises** (statut courant → décisions possibles) :

   | Statut courant | Décisions permises |
   |---|---|
   | reçue, en analyse | éligible, non éligible, rejetée |
   | à vérifier | éligible, non éligible, rejetée |
   | éligible | non éligible, présélectionnée, convoquée, rejetée |
   | non éligible | éligible, rejetée |
   | présélectionnée | convoquée, rejetée, éligible *(annulation de la présélection)* |
   | convoquée | retenue, rejetée, présélectionnée *(annulation de la convocation)* |
   | retenue | rejetée *(désistement, annulation)*, convoquée *(correction)* |
   | rejetée | éligible, non éligible *(réouverture, ex. recours)* |

   Toute autre transition est refusée par le service (et non seulement masquée dans
   l'interface). Une décision identique au statut courant est refusée (« la candidature est
   déjà … »).
5. **Conséquences explicites de la table** :
   - un dossier **éligible peut être rejeté** (motif obligatoire, ex. non-présentation, classement) ;
   - un dossier **non éligible ne peut être ni présélectionné, ni convoqué, ni retenu**
     directement : il faut d'abord une décision humaine « éligible » motivée (requalification),
     elle-même tracée ;
   - un dossier **à vérifier** doit être tranché (éligible / non éligible) ou rejeté avant toute
     présélection ;
   - **retenue** n'est atteignable que depuis **convoquée** ;
   - on peut **revenir sur une décision** uniquement par une nouvelle décision : les retours
     permis sont ceux en italique dans la table. La décision antérieure n'est jamais modifiée ni
     supprimée.
6. **Révision** : une décision est une « révision » lorsqu'elle fait reculer le dossier dans le
   parcours (présélectionnée → éligible, convoquée → présélectionnée, retenue → convoquée ou
   rejetée, rejetée → éligible / non éligible, éligible ↔ non éligible après une décision
   humaine). Le formulaire affiche alors un avertissement et demande une confirmation explicite
   (case « Je confirme revenir sur la décision du JJ/MM/AAAA ») ; le journal porte
   `metadata.revision = true` et l'identifiant de la décision remplacée.
7. **Contrôle de concurrence** : le formulaire transmet le statut courant affiché et
   l'identifiant de la dernière décision connue. Si l'un des deux a changé entre l'affichage et
   l'envoi, la décision est refusée (rien n'est enregistré), un message indique qu'une autre
   décision a été prise entre-temps et la section est rafraîchie. Le service verrouille la
   candidature (`select_for_update`) pendant l'enregistrement.
8. **Atomicité** : création de la `Decision`, mise à jour de `Candidature.statut` et écriture
   du journal d'audit se font dans une seule transaction ; si l'une échoue, rien n'est
   enregistré.
9. **Le moteur ne reclasse jamais après une décision humaine** : dès qu'une candidature possède
   au moins une `Decision`, `run_eligibility` continue à créer une `VerificationEligibilite`
   (historique) mais **ne modifie plus** `Candidature.statut`, quel que soit le statut. L'écart
   éventuel est signalé sur la fiche (KAN-11, règle 10).
10. **Avertissements non bloquants** (confirmation requise, motif toujours obligatoire) :
    - décision « présélectionnée », « convoquée » ou « retenue » alors que la candidature a au
      moins une anomalie non résolue de gravité **haute** ;
    - décision « retenue » alors que le nombre de candidatures déjà retenues sur le poste (ou
      l'offre) atteint `nombre_postes`.
    L'avertissement et la confirmation sont inscrits dans `metadata` du journal.
11. **Concours terminé** : aucune décision n'est possible sur une candidature dont le concours a
    le statut « Terminé » (`Concours.Statut.TERMINE`) ; le formulaire n'est pas affiché et le
    service refuse. (Voir Q3.)
12. **Interface (HTMX)** : sur la fiche, un formulaire avec une liste des seules décisions
    permises pour le statut courant (libellés français), un champ motif (compteur de
    caractères), un bouton « Enregistrer la décision ». L'envoi se fait en HTMX : en succès, le
    statut de l'en-tête et l'historique des décisions sont rafraîchis et un message de
    confirmation s'affiche ; en erreur, le formulaire est réaffiché avec le message et le motif
    saisi conservé. Sans JavaScript, le même `POST` fonctionne avec redirection vers la fiche.
13. **Pas de décision en lot** dans cette story ; pas de suppression ni de modification de
    décision dans l'interface, l'administration Django ou une API.
14. **Un seul chemin d'enregistrement** : toutes les règles ci-dessus (valeurs permises,
    transitions, motif, révision, concours terminé, verrou) sont appliquées **dans le service**
    `record_decision`, pas seulement dans la vue. Constat : `DecisionAdmin` permet aujourd'hui
    d'ajouter une décision depuis l'administration Django (via `record_decision`, sans
    confirmation ni contrôle de concurrence). L'ajout depuis l'administration est désactivé
    (`has_add_permission = False`) : la fiche est le seul point d'entrée (voir Q10).

## Critères d'acceptation

- **CA1** — Étant donné un utilisateur `direction` et une candidature « éligible », quand il
  envoie la décision « présélectionnée » avec un motif valide, alors une `Decision` est créée
  avec cet auteur, cette décision et ce motif, le statut de la candidature devient
  « présélectionnée » et la réponse affiche le nouveau statut et la décision en tête de
  l'historique.
- **CA2** — Étant donné la même action par un utilisateur `admin`, alors le résultat est
  identique (décision créée, statut mis à jour).
- **CA3** — Étant donné un utilisateur au rôle `consultant` ou `client`, quand il envoie une
  décision, alors la réponse est 403, aucune `Decision` n'est créée, le statut est inchangé.
- **CA4** — Étant donné un utilisateur non connecté, quand il envoie un `POST` de décision,
  alors il est redirigé vers la connexion et rien n'est enregistré.
- **CA5** — Étant donné un `POST` sans jeton CSRF valide, alors la réponse est 403 et rien n'est
  enregistré.
- **CA6** — Étant donné un `GET` sur l'URL d'enregistrement de décision, alors la réponse est
  405 et rien n'est enregistré.
- **CA7** — Étant donné un motif vide, composé d'espaces, ou de moins de 10 caractères après
  suppression des espaces, quand la décision est envoyée, alors elle est refusée avec le message
  « Le motif est obligatoire (10 caractères minimum) », aucune `Decision` n'est créée et le
  statut est inchangé.
- **CA8** — Étant donné un motif de plus de 2 000 caractères, alors la décision est refusée avec
  un message explicite et rien n'est enregistré.
- **CA9** — Pour chaque couple (statut courant, décision) de la table de la règle 4, étant donné
  une candidature dans ce statut, quand la décision est envoyée (avec confirmation si requise),
  alors elle est acceptée ; pour chaque couple **absent** de la table, alors le service lève une
  erreur de transition, la vue répond 400 avec un message, et rien n'est enregistré (test
  paramétré sur les 9 × 6 couples).
- **CA10** — Étant donné une décision « reçue », « en analyse » ou « à vérifier » envoyée
  directement au service ou à la vue, alors elle est refusée et rien n'est enregistré.
- **CA11** — Étant donné une candidature « non éligible », quand on tente « présélectionnée »,
  « convoquée » ou « retenue », alors c'est refusé ; quand on décide d'abord « éligible »
  (motif valide) puis « présélectionnée », alors les deux décisions sont enregistrées dans cet
  ordre.
- **CA12** — Étant donné une candidature « éligible », quand on décide « rejetée » avec motif
  valide, alors la décision est acceptée.
- **CA13** — Étant donné une candidature « éligible », quand on décide « retenue », alors c'est
  refusé (retenue n'est possible que depuis « convoquée »).
- **CA14** — Étant donné une candidature dont le statut courant est « convoquée », quand on
  envoie « convoquée », alors c'est refusé avec le message « La candidature est déjà
  Convoquée ».
- **CA15** — Étant donné une candidature « rejetée » par une décision D1, quand on décide
  « éligible » avec motif valide et confirmation de révision, alors une nouvelle décision D2 est
  créée, D1 est inchangée en base (même motif, même date), le statut devient « éligible » et
  l'entrée d'audit porte `metadata.revision = true` et `metadata.decision_remplacee_id = D1.id`.
- **CA16** — Étant donné une transition de révision (règle 6) envoyée **sans** la confirmation,
  alors elle est refusée avec le message demandant la confirmation, et rien n'est enregistré.
- **CA17** — Étant donné une décision acceptée, alors exactement une entrée `JournalAudit` est
  créée avec `action = decision`, `actor` = l'utilisateur, objet = la candidature, `before.statut`
  = ancien statut, `after.statut` = nouveau statut, `metadata.decision_id`, `metadata.motif`,
  l'adresse IP et le navigateur de la requête.
- **CA18** — Étant donné une décision refusée (motif invalide, transition interdite,
  concurrence, droits), alors aucune entrée `JournalAudit` d'action `decision` n'est créée.
- **CA19** — Étant donné une erreur provoquée lors de l'écriture du journal d'audit (simulée),
  quand une décision est envoyée, alors aucune `Decision` n'est créée et le statut est inchangé
  (transaction annulée).
- **CA20** — Étant donné deux utilisateurs ayant ouvert la fiche d'une candidature « convoquée »,
  quand le premier enregistre « retenue » puis le second envoie « rejetée » avec le statut
  attendu « convoquée », alors la seconde décision est refusée avec le message « Une autre
  décision a été enregistrée entre-temps », une seule `Decision` existe et le statut est
  « retenue ».
- **CA21** — Étant donné une candidature ayant reçu une décision humaine « éligible », quand
  `run_eligibility` est exécuté et que le moteur rend « non éligible », alors une nouvelle
  `VerificationEligibilite` est créée et le statut reste « éligible » ; il en va de même pour
  une décision humaine « non éligible » et un verdict moteur « éligible ».
- **CA22** — Étant donné une candidature sans décision au statut « à vérifier », quand
  `run_eligibility` rend « éligible », alors le statut devient « éligible » (comportement
  existant conservé).
- **CA23** — Étant donné une candidature avec une anomalie non résolue de gravité haute, quand
  on décide « convoquée » sans confirmation, alors la décision est refusée avec un
  avertissement ; avec confirmation, elle est acceptée et `metadata` du journal mentionne
  l'avertissement confirmé.
- **CA24** — Étant donné un poste avec `nombre_postes = 1` et une candidature déjà « retenue »,
  quand on décide « retenue » pour une autre candidature « convoquée » du même poste, alors un
  avertissement est demandé ; avec confirmation, la décision est acceptée et tracée.
- **CA25** — Étant donné une candidature dont le concours a le statut « Terminé », quand la fiche
  est affichée, alors le formulaire de décision n'est pas présent ; quand un `POST` de décision
  est envoyé, alors il est refusé et rien n'est enregistré.
- **CA26** — Étant donné une candidature « éligible », quand la fiche est affichée, alors la
  liste des décisions proposées contient exactement « Non éligible », « Présélectionnée »,
  « Convoquée », « Rejetée ».
- **CA27** — Étant donné un envoi HTMX (`HX-Request: true`) accepté, alors la réponse est un
  fragment contenant le nouveau statut et l'historique mis à jour ; étant donné un envoi HTMX
  refusé pour motif invalide, alors le fragment réaffiche le formulaire avec le message
  d'erreur et le motif saisi.
- **CA28** — Étant donné un envoi classique (sans `HX-Request`) accepté, alors la réponse est une
  redirection 302 vers la fiche de la candidature.
- **CA29** — Étant donné une `Decision` existante, quand on tente de la modifier ou de la
  supprimer via l'administration Django, alors l'action n'est pas disponible (pas de permission
  de modification ni de suppression) ; via l'ORM, `save()` sur une décision existante lève une
  erreur. Étant donné un `admin` sur l'administration Django, quand il ouvre la page d'ajout
  d'une décision, alors l'accès est refusé (403).
- **CA31** — Étant donné un appel direct à `record_decision` (sans passer par la vue) avec une
  transition absente de la table ou un motif de moins de 10 caractères, alors le service lève
  une erreur et rien n'est enregistré.
- **CA30** — Étant donné un identifiant de candidature inexistant dans l'URL de décision, alors
  la réponse est 404 et rien n'est enregistré.

## Cas limites et erreurs

- Valeur de décision inconnue ou falsifiée dans le `POST` : 400, rien n'est enregistré.
- Candidature dans un statut inattendu (donnée importée) : aucune décision proposée, message
  « Statut non pris en charge, contactez un administrateur ».
- Double clic / double envoi : le second envoi est refusé par le contrôle de concurrence
  (règle 7) ou par la règle « décision identique au statut courant ».
- Utilisateur désactivé ou session expirée pendant la saisie : redirection vers la connexion ;
  le motif n'est pas conservé côté serveur.
- Motif contenant du HTML ou des scripts : stocké tel quel, toujours échappé à l'affichage.
- Motif contenant des données personnelles d'un tiers : non contrôlé automatiquement ; consigne
  dans l'aide du champ (« Indiquez des faits liés au dossier, sans donnée sensible »).
- Synchronisation du site ou webhook pendant une décision : la synchronisation ne modifie jamais
  `Candidature.statut` d'une candidature ayant une décision (à vérifier lors de l'implémentation
  dans `integrations/site_source/sync.py`).
- Volume : la vérification du quota (règle 10) reste une requête de comptage indexée par poste.

## Traçabilité et conformité

- **Journal d'audit** : chaque décision acceptée produit une entrée `decision` (acteur, objet,
  avant/après statut, motif, `decision_id`, révision éventuelle et décision remplacée,
  avertissements confirmés, IP, navigateur). Les tentatives refusées ne créent pas de
  `Decision` ; leur journalisation éventuelle est une question ouverte (Q6). Le journal reste en
  ajout seul (modèle, admin et trigger PostgreSQL existants).
- **Données personnelles** : le motif peut contenir des informations sur le candidat ; il est
  visible uniquement par `admin` et `direction` (fiche, journal). Il entre dans le périmètre de
  la durée de conservation et du droit d'accès (loi 09-08), à traiter avec la purge (story
  dédiée).
- **IA** : aucune. La décision ne peut provenir ni de l'IA ni du moteur ; le verdict du moteur
  est une aide affichée, jamais appliqué comme décision (règles 1 et 9).
- **Humain dans la boucle** : aucune élimination automatique ; « rejetée » et « non éligible »
  en tant que décisions sont toujours portées par un utilisateur nommé, avec motif.
- **Biais** : le formulaire n'affiche ni sexe, ni âge, ni région, ni photo, ni situation
  familiale ; aucune règle de transition ne dépend de ces données.

## Hors périmètre

- Décisions en lot depuis la liste des candidatures.
- Génération de courriers ou de listes (convoqués, résultats) à partir des décisions (E4/exports).
- Motifs prédéfinis (liste de motifs types) et workflow de validation à deux niveaux (« quatre
  yeux »).
- Résolution des anomalies.
- Notification du candidat ou écriture vers le site (l'app n'écrit jamais dans la base du site).
- Journalisation des consultations de la fiche (KAN-14).

## Questions ouvertes

Toutes tranchées le 2026-11-23 : la proposition par défaut est retenue pour chaque question.

- [x] **Q1 — Table des transitions (règle 4)** : la valider telle quelle. *Proposition par
  défaut* : adopter la table ; en particulier « convoquée » directement depuis « éligible » est
  permis (concours sans étape de présélection publiée). *Conséquence* : si le PO veut imposer
  la présélection, retirer « convoquée » de la ligne « éligible ».
- [x] **Q2 — « Non éligible » décidé par un humain est-il une élimination définitive ?**
  *Proposition par défaut* : non ; « non éligible » reste un statut d'éligibilité, la clôture
  définitive est « rejetée ». Les listes officielles des non retenus s'appuient sur « rejetée »
  et « non éligible ». *Alternative* : supprimer « non éligible » des décisions et ne garder que
  « rejetée ».
- [x] **Q3 — Décisions après la fin du concours** : bloquer quand le concours est « Terminé »
  (règle 11) ? *Proposition par défaut* : oui pour `direction`, et aussi pour `admin` ; un
  recours après clôture se traite en repassant d'abord le concours en « En évaluation »
  (action admin tracée). *Alternative* : autoriser l'admin seul, avec révision obligatoire.
- [x] **Q4 — Qui peut revenir sur une décision « retenue » ou « rejetée »** ? *Proposition par
  défaut* : `admin` et `direction`, avec confirmation de révision et motif. *Alternative* :
  réserver les révisions de « retenue » et « rejetée » à `admin`.
- [x] **Q5 — Longueur minimale du motif** : 10 caractères ? *Proposition par défaut* : 10
  (évite « ok », « non ») ; maximum 2 000.
- [x] **Q6 — Journaliser les tentatives refusées** (transition interdite, conflit de
  concurrence) ? *Proposition par défaut* : non pour les erreurs de saisie (motif trop court),
  oui pour les refus de droits (403) et les transitions interdites envoyées directement
  (signal d'un usage anormal), via une action d'audit existante (`update`) avec
  `metadata.refus`. *Conséquence* : ajout d'une valeur `AuditAction` dédiée si le PO préfère.
- [x] **Q7 — Quota `nombre_postes` (règle 10)** : avertir ou bloquer ? *Proposition par défaut* :
  avertir (les listes d'attente et désistements existent) ; bloquer obligerait à gérer une
  liste complémentaire.
- [x] **Q8 — Anomalie haute non résolue (règle 10)** : avertir ou bloquer la présélection ?
  *Proposition par défaut* : avertir avec confirmation tracée.
- [x] **Q9 — Candidatures à une offre privée** : même table de transitions ? *Proposition par
  défaut* : oui ; « convoquée » vaut « convoquée en entretien ».
- [x] **Q10 — Ajout de décision depuis l'administration Django** : le supprimer (règle 14) ?
  *Proposition par défaut* : oui, la fiche devient le seul point d'entrée ; l'administration
  reste en lecture seule pour les décisions. *Alternative* : le garder pour l'admin, soumis aux
  mêmes règles du service mais sans confirmation de révision ni contrôle de concurrence.
