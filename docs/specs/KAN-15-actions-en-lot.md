# KAN-15 — Actions en lot sur la liste des candidatures

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-15
- **Epic** : E2 — Écrans concours et éligibilité
- **Statut** : validée le 2026-11-23 par le Product Owner. Les questions **[HDB]** (Q1 à Q4)
  ont été tranchées explicitement : refus et « retenue » interdits en lot, « éligible » en lot
  avec confirmation explicite, anomalie haute avec choix explicite exclure / inclure.

## Contexte

Sur un e-Concours public (SRM), un poste reçoit des centaines ou des milliers de candidatures.
Après l'éligibilité, la direction convoque ou présélectionne des dizaines de candidats à la fois ;
le faire fiche par fiche (KAN-13) est long et source d'erreurs de saisie. Le brief prévoit des
« actions en lot » sur la liste des candidatures (`CLAUDE.md` §7, écran 2), tout en imposant une
décision **toujours humaine, motivée et tracée** et **aucune élimination automatique** (§4, §6).

Existant dans le code (à réutiliser, ne pas recréer) :

- **Liste des candidatures d'un poste** (KAN-10) : vue `poste_candidatures` (GET seul), gabarits
  `templates/recruitment/poste_candidatures.html` (formulaire `#filtres`, zone `#resultats`) et
  `_candidatures_resultats.html` (une ligne `<tr data-candidature-id>` par candidature et une
  **première colonne réservée** aux cases à cocher, aujourd'hui vide).
- **Filtrage unique** : `apps/recruitment/filters.py` — `filter_candidatures(poste, params)`
  renvoie `(queryset, CandidatureFilter)` ; le queryset est annoté (`dernier_verdict`,
  `nb_anomalies`, `gravite_max`) et couvre **toutes les pages**. `CandidatureFilter.querystring()`
  reconstruit les paramètres validés ; `as_metadata()` les résume pour l'audit.
- **Règles de décision** (KAN-13) : `apps/evaluation/decisions.py` — `DECISIONS`, `TRANSITIONS`,
  `REVISIONS`, `REQUALIFICATIONS`, `est_revision`, `nettoyer_motif` (10 à 2 000 caractères),
  `verifier_transition`, `avertissements` (anomalie haute, quota), `concours_termine`,
  `DecisionError`, `ConfirmationRequise`.
- **Service de décision** : `apps/evaluation/services.py` — `record_decision(candidature, auteur,
  decision, motif, request, *, statut_attendu, derniere_decision_attendue, confirmer_revision,
  confirmer_avertissements)` : seul chemin d'enregistrement, verrou `select_for_update`, une
  transaction (décision + statut + audit `decision`).
- **Moteur d'éligibilité** : `run_eligibility(candidature)` crée une `VerificationEligibilite` et ne
  reclasse le statut que si la candidature n'a **aucune** décision et que son statut est dans
  `AUTO_CLASSABLE` ; journalise `update` (acteur « moteur d'éligibilité ») seulement si le statut
  change.
- **Audit** : `apps.audit.services.log` ; `AuditAction` (pas de valeur dédiée aux lots) ; journal
  en ajout seul.
- **Permissions** : `DIRECTION_ROLES` (`admin`, `direction`), `role_required` ; la vue de décision
  KAN-13 renvoie (et ne lève pas) la 403 pour que la trace de refus survive à
  `ATOMIC_REQUESTS = True` (`config/settings/base.py`).
- **Celery** : configuré (`config/celery.py`, `CELERY_TASK_ALWAYS_EAGER` en test), aucune tâche
  d'évaluation existante.
- **Charte** : `docs/design/charte.md` (un seul `.btn-primary` par formulaire, pas d'alerte
  rouge, propriétés logiques RTL, CSP sans script en ligne).

## User story

En tant que membre de la direction, je veux sélectionner plusieurs candidatures d'un poste et
leur appliquer une même action (relancer le calcul d'éligibilité, ou enregistrer une même
décision d'avancement avec un motif commun), après avoir vu un récapitulatif de ce qui sera fait
et de ce qui sera exclu, afin de traiter les gros volumes des concours publics sans renoncer à
une décision humaine, motivée et tracée pour chaque dossier.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Sélection, récapitulatif et exécution des deux actions en lot. |
| direction | Idem admin. |
| consultant, client (vague 2, non attribuables) | Refusé : 403, rien n'est fait, refus journalisé en `access_denied` (règle 30). |
| utilisateur inactif / session expirée | Redirection vers la connexion ; rien n'est fait. |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP (middleware existant). |
| non connecté | Redirection vers la connexion ; rien n'est fait. |
| processus automatique (tâche, moteur, IA, synchronisation) | Ne déclenche jamais un lot de décisions ; la tâche Celery de relance (règle 24) n'agit qu'au nom de l'utilisateur qui l'a lancée, revérifie à l'exécution qu'il est toujours actif et `direction` / `admin` (sinon le lot est clos en erreur technique, sans relance, règle 25), et ne crée jamais de `Decision`. |

Contrôle côté serveur sur chaque point d'entrée (récapitulatif, exécution) et chaque réponse
partielle HTMX.

## Règles métier

### A. Actions proposées

1. Deux actions en lot, et seulement deux :
   - **Relancer le calcul d'éligibilité** (aucune décision, voir section F) ;
   - **Enregistrer une décision** avec un **motif commun** (voir section E).
   Aucune autre action (export, résolution d'anomalie, score, courrier) dans cette story.
2. **Décisions autorisées en lot** — liste fermée, appliquée **dans le service** (pas seulement
   masquée dans l'interface) :

   | Décision en lot | Transitions acceptées en lot (statut courant → décision) |
   |---|---|
   | **Éligible** | reçue, en analyse, à vérifier → éligible ; non éligible → éligible **uniquement si la candidature n'a aucune décision humaine** (sinon c'est une requalification, donc une révision) |
   | **Présélectionnée** | éligible → présélectionnée |
   | **Convoquée** | éligible → convoquée ; présélectionnée → convoquée |

   C'est le sous-ensemble de la table KAN-13 (règle 4) restreint aux décisions d'avancement,
   **révisions exclues** (règle 4 ci-dessous).
3. **Décisions interdites en lot** : **non éligible**, **rejetée** et **retenue** (voir Q1, Q2).
   Justification :
   - « non éligible » et « rejetée » sont des décisions **défavorables** ; les prendre en lot avec
     un motif commun revient à une élimination de masse sans examen individuel, contraire à
     l'esprit de `CLAUDE.md` §6 (« aucune élimination automatique ») et fragile en cas de
     recours (motif non individualisé). Elles restent possibles **dossier par dossier** depuis la
     fiche (KAN-13) ;
   - « retenue » est la décision finale d'un concours, soumise au quota `nombre_postes` ; son
     volume est faible par nature (≤ nombre de postes + liste d'attente), le gain d'un lot est
     nul et le risque d'erreur élevé.
   Une valeur interdite ou inconnue envoyée au point d'entrée est refusée (400), rien n'est fait,
   et le refus est journalisé (règle 30).
4. **Révisions exclues du lot** : toute transition pour laquelle `est_revision(statut, décision,
   a_deja_une_decision)` est vraie (retour en arrière, requalification après décision humaine,
   réouverture d'un dossier rejeté) n'est jamais traitée en lot ; la candidature est classée
   « non concernée — révision : à faire depuis la fiche ».

### B. Sélection

5. **Cases à cocher** : la première colonne réservée par KAN-10 (règle 27) reçoit une case par
   ligne, de valeur l'identifiant de la candidature, avec un nom accessible « Sélectionner
   <NOM Prénom> ». La sélection ne vaut que pour la **page affichée** : changer de filtre, de
   tri, de page ou de taille de page (fragment HTMX remplacé) la réinitialise. Aucune sélection
   n'est mémorisée côté serveur ni dans le navigateur (`localStorage` interdit).
6. **« Tout sélectionner sur la page »** : une case dans l'en-tête du tableau coche ou décoche
   toutes les cases de la page (amélioration progressive par un fichier JS statique, CSP
   respectée). Un compteur « N candidature(s) sélectionnée(s) » est mis à jour dans une zone
   `aria-live="polite"`.
7. **« Appliquer aux N résultats du filtre »** : un second bouton d'envoi (mode `filtre`)
   applique l'action à **toutes** les candidatures correspondant aux filtres et à la recherche
   courants, toutes pages confondues. Le formulaire transmet les paramètres de filtre validés
   (issus de `CandidatureFilter.querystring()`), et le serveur recalcule l'ensemble avec
   `filter_candidatures(poste, params)` — **la même logique que la liste**, jamais une liste
   d'identifiants calculée côté navigateur. N affiché sur le bouton est le compteur de la liste.
8. **Mode `ids`** (cases cochées) : les identifiants reçus sont dédoublonnés. Si l'un d'eux
   n'existe pas ou n'appartient pas au poste de l'URL, la requête entière est refusée (400), rien
   n'est fait, le refus est journalisé (signe d'un envoi forgé).
9. **Sélection vide** (aucune case cochée en mode `ids`, ou filtre sans résultat en mode
   `filtre`) : message « Sélectionnez au moins une candidature. », pas de récapitulatif, aucune
   écriture.
10. **Plafond** : un lot compte au plus `LOT_TAILLE_MAX` candidatures (paramètre de
    configuration, **500** par défaut, voir Q5). Au-delà, le récapitulatif est refusé avec le
    message « La sélection compte N candidatures, au-delà du plafond de 500 par lot. Affinez les
    filtres. » ; **pas de troncature** (une coupure arbitraire n'aurait pas de sens métier).
11. Le plafond porte sur la **sélection** (avant exclusion des non concernées).

### C. Récapitulatif (étape obligatoire, sans écriture métier)

12. Toute action en lot passe par un **récapitulatif** avant exécution (un `POST` de sélection
    renvoie le récapitulatif ; un second `POST` exécute). Le récapitulatif n'écrit ni `Decision`,
    ni statut, ni `VerificationEligibilite`.
13. Le récapitulatif affiche :
    - l'action et, pour une décision, son libellé (« Convoquée ») ;
    - le **nombre de candidatures sélectionnées** et le mode (cases cochées / résultats du filtre,
      avec le résumé des filtres) ;
    - la **répartition par statut courant** (libellés français, effectifs) ;
    - le nombre de **candidatures concernées** (qui seront traitées) ;
    - le nombre de **non concernées**, regroupées par raison : « transition interdite depuis
      <statut> », « déjà <statut> », « révision : à faire depuis la fiche », « concours
      terminé » ; avec, pour chaque groupe, la liste nominative (NOM Prénom, lien vers la fiche),
      limitée à 100 noms par groupe puis « et N autres » ;
    - les **avertissements** (règle 14) et, pour chacun, le nombre et la liste nominative
      (même limite) ;
    - pour la relance d'éligibilité : le nombre de candidatures **déjà décidées** (« un nouveau
      verdict sera calculé, leur statut ne changera pas ») ;
    - pour une décision : le champ **motif commun** (compteur de caractères, 10 à 2 000) et la
      case de confirmation (règle 17).
14. **Avertissements** d'une décision en lot (non bloquants, mais exigeant un choix explicite) :
    - **anomalie haute** : décision « présélectionnée » ou « convoquée » sur une candidature ayant
      au moins une anomalie non résolue de gravité haute (même règle que
      `decisions.avertissements`) ;
    - **verdict non favorable** : décision « éligible » sur une candidature dont le dernier
      verdict du moteur n'est pas « éligible » (à vérifier, non éligible, ou non évaluée).
      Libellé : « Ces dossiers n'ont pas été jugés éligibles par le moteur. En les déclarant
      éligibles en lot, vous attestez les avoir examinés. » (voir Q3, **[HDB]**).
    Pour **chaque** type d'avertissement présent, l'utilisateur doit choisir explicitement entre
    « Exclure ces N dossiers du lot » et « Les inclure : je confirme » ; **aucune option n'est
    présélectionnée** ; un envoi sans choix est refusé (« Indiquez si les dossiers signalés sont
    exclus ou inclus »). Les dossiers exclus sont reportés comme « exclus par l'utilisateur
    (avertissement) ».
15. Le quota `nombre_postes` ne s'applique pas (« retenue » est interdite en lot).
16. **Gel de la sélection** : le récapitulatif fige l'ensemble traité. Il produit un identifiant
    de lot (UUID) et un jeton signé (`django.core.signing`, durée de validité 30 minutes)
    contenant : identifiant de lot, utilisateur, poste, action, décision, mode, résumé des
    filtres **sans le terme de recherche** (le jeton est signé mais non chiffré : seul un booléen
    « recherche appliquée » y figure ; l'URL de retour reprend le terme depuis le formulaire), et
    pour chaque candidature concernée son identifiant, son **statut affiché** et
    l'identifiant de sa **dernière décision connue**, ainsi que l'ensemble des identifiants
    signalés par un avertissement. L'exécution ne traite **que** les candidatures de ce jeton,
    même si les filtres donneraient entre-temps un autre résultat.

### D. Confirmation et exécution (commun)

17. L'exécution exige : un jeton valide (signature correcte, non expiré, même utilisateur, même
    poste) ; pour une décision, un motif valide et la case « Je confirme enregistrer la décision
    « <libellé> » pour N candidatures, chacune en mon nom et avec ce motif » cochée ; un choix
    explicite pour chaque avertissement (règle 14). Sinon, rien n'est fait et le récapitulatif
    est réaffiché avec le message et le motif saisi conservé.
18. **Jeton invalide ou expiré** : 400, message « Le récapitulatif a expiré ou n'est plus valide.
    Recommencez la sélection. », rien n'est fait.
19. **Rejeu** : un identifiant de lot déjà exécuté (entrée d'audit « lot » existante pour cet
    identifiant) est refusé (« Ce lot a déjà été exécuté »), rien n'est fait. Une double
    soumission strictement simultanée est en outre neutralisée, pour les décisions, par le
    contrôle de concurrence de chaque candidature (règle 21).
20. **Traitement partiel, une transaction par candidature** : chaque candidature est traitée dans
    sa propre transaction, par ordre croissant d'identifiant. Le succès d'une candidature est
    définitif même si une autre échoue ; l'échec d'une candidature n'annule rien d'autre. Les
    vues d'exécution sont exclues de `ATOMIC_REQUESTS` (`transaction.non_atomic_requests`) pour
    que chaque candidature soit validée immédiatement et que les verrous soient relâchés au fil
    de l'eau. Une erreur technique inattendue sur une candidature est rattrapée, comptée
    « erreur technique » et n'interrompt pas le lot.
21. **Rapport d'exécution** : à la fin, l'écran affiche « X décisions enregistrées / Y non
    enregistrées » (ou « X verdicts recalculés, dont Z statuts modifiés »), puis la liste des
    échecs par raison (« modifiée entre-temps », « transition devenue interdite », « concours
    terminé entre-temps », « nouvel avertissement non confirmé », « erreur technique ») avec
    NOM Prénom et lien vers la fiche, et un lien « Revenir à la liste » qui conserve les filtres,
    le tri et la taille de page d'origine.

### E. Décision en lot

22. **Chaque candidature reçoit sa propre `Decision`**, créée par `record_decision` (seul chemin
    d'enregistrement, KAN-13 règle 14), avec : l'utilisateur comme auteur, la décision du lot, le
    motif commun (nettoyé par `nettoyer_motif`), `statut_attendu` et
    `derniere_decision_attendue` issus du jeton (contrôle de concurrence), `confirmer_revision =
    False` (les révisions sont exclues), et `confirmer_avertissements = True` **uniquement** pour
    les candidatures signalées au récapitulatif et que l'utilisateur a choisi d'inclure. Toutes
    les règles KAN-13 (transitions, motif, concours terminé, verrou, atomicité, audit) s'appliquent
    donc à chaque candidature.
    - Si une candidature a changé entre récapitulatif et exécution (statut ou dernière décision),
      elle est en échec « modifiée entre-temps ».
    - Si un avertissement apparaît à l'exécution sur une candidature qui n'en avait pas au
      récapitulatif (`ConfirmationRequise`), elle est en échec « nouvel avertissement non
      confirmé ».
23. `record_decision` reçoit un argument optionnel `lot_id` : il est inscrit dans la `Decision`
    (nouveau champ `lot_id`, UUID, nul pour une décision prise depuis la fiche — Q9) et dans
    `metadata` de l'entrée d'audit `decision` (`lot_id`, `mode = "lot"`). La fiche candidature
    (historique des décisions) affiche la mention « décision prise en lot » quand `lot_id` est
    renseigné. Le motif n'est pas modifié (pas de préfixe automatique).

### F. Relance du calcul d'éligibilité en lot

24. La relance appelle `run_eligibility` pour chaque candidature concernée (une transaction
    chacune, règle 20). Elle **ne crée jamais de `Decision`** et ne reclasse **jamais** une
    candidature ayant au moins une décision humaine (règle 9 de KAN-13, comportement existant) ni
    une candidature hors `AUTO_CLASSABLE`.
25. **Synchrone ou asynchrone** : jusqu'à `LOT_ELIGIBILITE_SYNC_MAX` candidatures (paramètre,
    **50** par défaut, Q6), la relance s'exécute dans la requête et le rapport s'affiche
    directement. Au-delà, une tâche Celery est mise en file avec l'identifiant de lot,
    l'identifiant de l'utilisateur, l'IP et le navigateur de la requête ; l'écran affiche « Relance
    lancée en arrière-plan pour N candidatures (lot <8 premiers caractères de l'UUID>). Le calcul
    se poursuit en arrière-plan : rechargez la liste dans quelques instants pour voir les verdicts
    à jour. » avec un bouton « Recharger la liste » qui conserve les filtres (aucun rafraîchissement
    automatique à la fin de la tâche, Q11). La tâche applique les mêmes règles (une transaction
    par candidature, erreurs comptées sans interrompre) et :
    - revérifie au moment de l'exécution que l'utilisateur est actif et a le rôle `direction` ou
      `admin` ; sinon elle ne relance rien et écrit l'entrée `lot` de fin avec toutes les
      candidatures en « erreur technique » (`metadata.cause = "utilisateur_non_autorise"`) ;
    - est idempotente par candidature : sous un verrou consultatif PostgreSQL (lot, candidature)
      pris dans la transaction de la candidature, une candidature ayant déjà une entrée `update`
      portant ce `lot_id` est comptée comme traitée sans nouveau calcul (ni seconde
      `VerificationEligibilite`, ni seconde entrée `update`) ;
    - si la mise en file échoue (broker indisponible), l'entrée `lot` de fin est écrite
      immédiatement, toutes les candidatures en « erreur technique »
      (`metadata.cause = "file_indisponible"`), et l'écran affiche « Le calcul en arrière-plan n'a
      pas pu être lancé (service de file indisponible). Aucun verdict n'a été recalculé ;
      réessayez dans quelques minutes. » (réponse 503, jamais d'erreur 500).
26. Toutes les candidatures sélectionnées sont concernées par la relance, sauf si le concours est
    terminé (règle 27) ; les candidatures déjà décidées sont recalculées (historique) et signalées
    au récapitulatif.

### G. Concours terminé

27. Si le concours du poste a le statut « Terminé », le récapitulatif classe toutes les
    candidatures en « non concernées — concours terminé », le bouton d'exécution n'est pas
    affiché, et un `POST` d'exécution forcé est refusé (aucune écriture). Cela vaut pour les
    décisions (cohérent avec KAN-13 règle 11) **et** pour la relance d'éligibilité (Q7). Si le
    concours passe à « Terminé » pendant l'exécution, les candidatures restantes sont en échec
    « concours terminé entre-temps ».

### H. Audit

28. **Une entrée par candidature** :
    - décision : l'entrée `decision` existante de `record_decision` (acteur, avant / après statut,
      `decision_id`, motif, avertissements confirmés), enrichie de `lot_id` et `mode = "lot"` ;
    - relance : exactement une entrée `update` par candidature traitée, acteur = l'utilisateur qui
      a lancé le lot (`actor_repr` mentionnant « via moteur d'éligibilité »), objet = la
      candidature (`object_repr` « Candidature #<id> », sans nom), `before` / `after` = statut,
      `metadata` = `lot_id`, `operation = "relance_eligibilite"`, `verification_id`, verdict,
      `statut_modifie` (booléen). L'entrée `update` « moteur d'éligibilité » que `run_eligibility`
      écrit aujourd'hui lors d'un changement de statut est remplacée par celle-ci quand l'appel
      vient d'un lot (pas de double entrée).
29. **Entrées « lot »** (nouvelle valeur `AuditAction.LOT`, libellé « Action en lot », Q10),
    objet = le `PosteConcours` :
    - une entrée au lancement (`metadata.etape = "lance"`) et une à la fin (`etape = "termine"`) ;
      pour la relance asynchrone, l'entrée de fin est écrite par la tâche ;
    - `metadata` : `lot_id`, `operation` (`decision` / `relance_eligibilite`), `decision`, `motif`
      (identique à celui des entrées `decision`), `mode` (`ids` / `filtre`), filtres appliqués
      **sans le terme de recherche** (`recherche_appliquee` booléen à la place), nombres
      sélectionnées / concernées / non concernées par raison / exclues par avertissement /
      avertissements confirmés, et à la fin succès / échecs par raison ;
      identifiants des candidatures (traitées, en échec) ;
    - **aucun nom, prénom ni autre donnée personnelle de candidat** dans ces entrées.
30. **Refus journalisés** :
    - refus de droits (403) → entrée `access_denied` (aligné sur KAN-14 règle 8, le
      récapitulatif étant un écran nominatif) : acteur, IP, navigateur, `object_repr`
      « Poste de concours #<id> », `metadata.chemin`, `metadata.poste_id`, `metadata.etape`
      (`recapitulatif` / `execution`) ; la réponse 403 est renvoyée (non levée) pour que l'entrée
      survive à `ATOMIC_REQUESTS` ;
    - refus métier (même politique que KAN-13 Q6) : valeur de décision interdite en lot,
      identifiants hors poste, jeton falsifié → entrée `update` avec `metadata.refus`
      (`valeur_interdite_en_lot`, `ids_hors_poste`, `jeton_invalide`), l'identifiant du poste,
      sans nom ni motif saisi. Les erreurs de saisie (motif trop court,
    case de confirmation absente, sélection vide, plafond dépassé) ne sont pas journalisées.
31. **Consultation** : l'affichage du récapitulatif crée une entrée `view` sur le poste
    (`metadata` : `lot_id`, `operation`, nombre sélectionnées), car il présente une liste
    nominative.

### I. Interface (HTMX, accessibilité, RTL)

32. **Barre d'actions** au-dessus du tableau, dans un formulaire `POST` dédié (`#lot`, jeton
    CSRF) distinct du formulaire de filtres (`GET`) : choix de l'action (« Relancer le calcul
    d'éligibilité », « Décision : Éligible / Présélectionnée / Convoquée »), compteur de
    sélection, bouton « Appliquer à la sélection » (mode `ids`) et bouton « Appliquer aux N
    résultats du filtre » (mode `filtre`). Un seul `.btn-primary` (« Appliquer à la sélection »).
    Les cases à cocher sont rattachées au formulaire `#lot` par l'attribut `form`.
33. **HTMX** : le récapitulatif, le réaffichage en erreur et le rapport sont des fragments insérés
    dans une zone dédiée (`#lot-panneau`) ; après exécution, la zone `#resultats` est rafraîchie
    avec les mêmes filtres. Sans JavaScript, les mêmes `POST` renvoient des pages complètes
    (récapitulatif, puis rapport) ; aucune fonctionnalité n'exige JavaScript, sauf la case
    « Tout sélectionner sur la page » et le compteur dynamique.
34. **Accessibilité** : nom accessible pour chaque case (règle 5) et pour la case d'en-tête
    (« Sélectionner toutes les candidatures de la page ») ; compteur et rapport annoncés par une
    zone `aria-live="polite"` ; après insertion du récapitulatif ou du rapport, le focus est
    placé sur son titre (`h2`, `tabindex="-1"`) ; choix d'avertissement en `fieldset` / `legend`
    avec boutons radio ; cibles ≥ 36 px ; focus visible ; texte toujours présent à côté des
    couleurs ; pas d'alerte rouge (charte).
35. **Langue** : tous les libellés passent par la traduction ; pluriels gérés (`blocktranslate
    count`) ; propriétés logiques uniquement (`ms-`, `ps-`, `text-start`, `border-s`…) ; aucun
    script ni attribut `style` en ligne.
36. Aucune donnée personnelle autre que NOM Prénom n'apparaît dans la barre, le récapitulatif ou
    le rapport (ni email, téléphone, CIN, date de naissance, âge, ville, région, sexe, photo).

## Critères d'acceptation

Jeu de données de référence (fictif) : concours C1 (statut « En évaluation »), poste P1 (référence
« P-01 », `nombre_postes = 2`) avec 10 candidatures ; poste P2 de C1 avec 1 candidature
« DUPONT Paul » ; concours C2 (statut « Terminé »), poste P3 avec 2 candidatures « éligible ».

| Réf. | Candidat (fictif) | Statut | Dernier verdict | Décision humaine | Anomalies non résolues |
|---|---|---|---|---|---|
| A | EL AMRANI Sara | à vérifier | à vérifier | — | 1 haute |
| B | BENNANI Youssef | éligible | éligible | — | — |
| C | ÉTTAHIRI Imane | non éligible | non éligible | — | 1 moyenne |
| D | ALAOUI Karim | convoquée | éligible | oui (convoquée) | — |
| E | ZIANI Nora | reçue | (aucun) | — | — |
| F | EL AMRANI Omar | à vérifier | à vérifier | — | 1 moyenne |
| G | IDRISSI Hamza | éligible | éligible | — | 1 haute |
| H | TAZI Leila | présélectionnée | éligible | oui (présélectionnée) | — |
| I | FASSI Rania | rejetée | non éligible | oui (rejetée) | — |
| J | CHRAIBI Mehdi | éligible | non éligible | oui (éligible, requalification) | — |

Motif valide de référence : « Convocation à l'épreuve orale du 12/03/2026 ».

**Accès et sécurité**

- **CA1** — Étant donné un utilisateur `direction` connecté avec TOTP, quand il ouvre la liste de
  P1, alors chaque ligne contient une case à cocher de valeur l'identifiant de la candidature et
  de nom accessible « Sélectionner <NOM Prénom> », l'en-tête contient la case « Sélectionner
  toutes les candidatures de la page », et la barre d'actions est présente dans un formulaire
  `POST` muni d'un jeton CSRF.
- **CA2** — Étant donné un utilisateur `admin`, quand il exécute le scénario de CA24, alors le
  résultat est identique.
- **CA3** — Étant donné un utilisateur `consultant` ou `client`, quand il envoie un `POST` de
  récapitulatif ou d'exécution (avec ou sans `HX-Request: true`), alors la réponse est 403, aucune
  `Decision` ni `VerificationEligibilite` n'est créée, aucun statut ne change, et une entrée
  d'audit `access_denied` est créée (`metadata.poste_id`, `metadata.chemin`, `object_repr`
  « Poste de concours #<id> » ; aligné sur KAN-14 règle 8).
- **CA4** — Étant donné un utilisateur non connecté, quand il envoie ces `POST`, alors il est
  redirigé vers la connexion et rien n'est fait ; étant donné un utilisateur `direction` sans
  TOTP actif, alors il est redirigé vers l'activation du TOTP.
- **CA5** — Étant donné un `POST` de récapitulatif ou d'exécution sans jeton CSRF valide, alors la
  réponse est 403 et rien n'est fait.
- **CA6** — Étant donné un `GET` sur l'URL de récapitulatif ou d'exécution, alors la réponse est
  405.
- **CA7** — Étant donné l'identifiant de P2 associé à l'identifiant de concours C2 dans l'URL, ou un
  poste inexistant, alors la réponse est 404 et rien n'est fait.

**Sélection**

- **CA8** — Étant donné le mode `ids` avec B et G cochés, quand le récapitulatif est demandé pour
  « Convoquée », alors il indique « 2 candidatures sélectionnées ».
- **CA9** — Étant donné le mode `ids` contenant B et l'identifiant de DUPONT Paul (P2), alors la
  réponse est 400, aucun récapitulatif n'est produit, et une entrée d'audit `update` avec
  `metadata.refus = "ids_hors_poste"` est créée ; de même pour un identifiant inexistant.
- **CA10** — Étant donné le mode `ids` avec B envoyé deux fois, alors le récapitulatif indique
  « 1 candidature sélectionnée ».
- **CA11** — Étant donné le mode `filtre` avec `statut=eligible`, alors le récapitulatif porte
  exactement sur B, G et J, soit l'ensemble renvoyé par `filter_candidatures(P1, statut=eligible)`.
- **CA12** — Étant donné un poste de 120 candidatures « éligible » affiché par pages de 50, quand
  « Appliquer aux 120 résultats du filtre » est utilisé, alors le récapitulatif indique
  « 120 candidatures sélectionnées » (toutes pages confondues).
- **CA13** — Étant donné le mode `ids` sans aucune case cochée, ou le mode `filtre` avec une
  recherche sans résultat, alors le message « Sélectionnez au moins une candidature. » s'affiche,
  aucun récapitulatif n'est produit et aucune entrée d'audit `lot` ou `view` de récapitulatif
  n'est créée.
- **CA14** — Étant donné `LOT_TAILLE_MAX = 5` (surcharge de test) et un filtre renvoyant 6
  candidatures, alors le récapitulatif est refusé avec le message de plafond indiquant 6 et 5,
  sans troncature ; avec un filtre renvoyant 5 candidatures, le récapitulatif est produit.
- **CA15** — Étant donné une liste où des cases sont cochées, quand un filtre, le tri ou la page
  change (fragment HTMX), alors le fragment renvoyé ne contient aucune case cochée.

**Décisions autorisées et récapitulatif**

- **CA16** — Étant donné la barre d'actions, alors les décisions proposées sont exactement
  « Éligible », « Présélectionnée », « Convoquée » ; « Non éligible », « Rejetée » et « Retenue »
  n'y figurent pas.
- **CA17** — Étant donné un `POST` de récapitulatif ou d'exécution avec la décision `non_eligible`,
  `rejetee`, `retenue`, `recue`, `en_analyse`, `a_verifier` ou une valeur inconnue, alors la
  réponse est 400, rien n'est fait et, pour les valeurs connues, une entrée `update` avec
  `metadata.refus = "valeur_interdite_en_lot"` est créée.
- **CA18** — Étant donné les 10 candidatures de P1 sélectionnées et la décision « Convoquée »,
  quand le récapitulatif est affiché, alors : la répartition par statut est « À vérifier 2,
  Éligible 3, Non éligible 1, Convoquée 1, Reçue 1, Présélectionnée 1, Rejetée 1 » ; les
  concernées sont B, G, H, J (4) ; les non concernées sont A, F (« transition interdite depuis À
  vérifier »), C (« … depuis Non éligible »), E (« … depuis Reçue »), I (« … depuis Rejetée ») et
  D (« déjà Convoquée ») ; l'avertissement « anomalie haute » liste G (1 dossier).
- **CA19** — Étant donné la décision « Éligible » sur A, C, E, F, H, I, J, quand le récapitulatif
  est affiché, alors les concernées sont A, C, E, F ; H et I sont « non concernées — révision :
  à faire depuis la fiche » ; J est « non concernée — déjà Éligible » ; l'avertissement « verdict
  non favorable » liste A, C, E, F.
- **CA20** — Étant donné la décision « Présélectionnée » sur D, alors D est « non concernée —
  révision » (convoquée → présélectionnée) ; étant donné une candidature supplémentaire K
  (fictive) « non éligible » par une décision humaine antérieure, quand la décision « Éligible »
  en lot porte sur K, alors K est « non concernée — révision » (requalification après décision
  humaine), alors que C (« non éligible » du moteur, sans décision) est concernée.
- **CA21** — Étant donné un récapitulatif affiché, alors aucune `Decision` ni
  `VerificationEligibilite` n'est créée, aucun statut ne change, et exactement une entrée d'audit
  `view` sur P1 est créée avec `lot_id`, `operation` et le nombre sélectionné.
- **CA22** — Étant donné un groupe de 150 non concernées, alors le récapitulatif liste 100 noms
  puis « et 50 autres ».
- **CA23** — Étant donné un poste de 10 puis de 500 candidatures sélectionnées en mode `filtre`,
  quand le récapitulatif est calculé, alors le nombre de requêtes SQL est identique et borné par
  une constante fixée dans le test.

**Exécution d'une décision en lot**

- **CA24** — Étant donné le récapitulatif « Convoquée » sur B, H, J (sans avertissement), quand
  l'utilisateur `direction` envoie le motif de référence et coche la confirmation, alors trois
  `Decision` distinctes sont créées (auteur = cet utilisateur, décision « convoquée », motif
  identique, même `lot_id`), les trois statuts deviennent « convoquée », et le rapport affiche
  « 3 décisions enregistrées, 0 non enregistrée ».
- **CA25** — Étant donné le même récapitulatif, quand le motif est vide ou fait moins de
  10 caractères après suppression des espaces, ou plus de 2 000, alors rien n'est enregistré et
  le récapitulatif est réaffiché avec le message de KAN-13 et le motif saisi.
- **CA26** — Étant donné le même récapitulatif, quand la case de confirmation n'est pas cochée,
  alors rien n'est enregistré et un message demande la confirmation.
- **CA27** — Étant donné le récapitulatif de CA18 (G signalé « anomalie haute »), quand
  l'exécution est envoyée sans choix pour cet avertissement, alors rien n'est enregistré et le
  message « Indiquez si les dossiers signalés sont exclus ou inclus » s'affiche ; avec « Exclure »,
  B, H, J sont convoqués, G reste « éligible » et figure au rapport comme « exclu par
  l'utilisateur » ; avec « Inclure : je confirme », G est aussi convoqué et son entrée d'audit
  `decision` porte `avertissements_confirmes = ["anomalie_haute"]`.
- **CA28** — Étant donné le récapitulatif de CA19, quand l'utilisateur choisit « Inclure : je
  confirme » pour l'avertissement « verdict non favorable » et exécute, alors A, C, E, F
  deviennent « éligible », chacun avec sa `Decision`, et l'entrée `lot` de fin compte 4
  avertissements confirmés.
- **CA29** — Étant donné le récapitulatif « Convoquée » sur B, H, J, quand, avant l'exécution, un
  autre utilisateur enregistre « Rejetée » sur B depuis la fiche, alors l'exécution convoque H et
  J, B reste « rejetée » avec une seule décision, et le rapport affiche « 2 décisions
  enregistrées, 1 non enregistrée » avec B en « modifiée entre-temps ».
- **CA30** — Étant donné le récapitulatif « Convoquée » sur B (sans anomalie), quand une anomalie
  haute non résolue est ajoutée à B avant l'exécution, alors B est en échec « nouvel avertissement
  non confirmé » et aucune décision n'est créée pour B.
- **CA31** — Étant donné le récapitulatif « Convoquée » sur B, H, J et une erreur simulée lors de
  l'écriture de l'audit pour H uniquement, quand le lot est exécuté, alors H n'a aucune nouvelle
  `Decision` et son statut est inchangé, B et J sont convoqués (validés en base), et le rapport
  liste H en « erreur technique ».
- **CA32** — Étant donné un jeton dont la signature est altérée, un jeton expiré (horloge avancée
  de plus de 30 minutes), ou un jeton émis pour un autre utilisateur ou un autre poste, quand
  l'exécution est envoyée, alors la réponse est 400 avec le message d'expiration, rien n'est fait,
  et une altération de signature crée une entrée `update` avec `metadata.refus =
  "jeton_invalide"`.
- **CA33** — Étant donné un lot déjà exécuté, quand le même jeton est renvoyé, alors le message
  « Ce lot a déjà été exécuté » s'affiche et aucune nouvelle `Decision` n'est créée.
- **CA34** — Étant donné les deux candidatures de P3 (concours C2 « Terminé »), quand le
  récapitulatif « Convoquée » est affiché, alors les deux sont « non concernées — concours
  terminé » et le bouton d'exécution est absent ; quand un `POST` d'exécution forgé est envoyé,
  alors rien n'est enregistré.
- **CA35** — Étant donné un lot de décisions exécuté, alors aucune `Decision` existante n'est
  modifiée et chaque `Decision` créée a un auteur humain authentifié.
- **CA36** — Étant donné une décision prise en lot, quand la fiche de la candidature est
  affichée, alors l'historique porte la mention « décision prise en lot » sur cette décision, et
  pas sur une décision prise depuis la fiche.

**Relance du calcul d'éligibilité**

- **CA37** — Étant donné B, C, D, E et J sélectionnés (≤ 50) et la relance d'éligibilité, quand
  le récapitulatif est affiché, alors il indique 5 candidatures concernées dont 2 déjà décidées
  (D, J) ; quand la relance est exécutée, alors 5 nouvelles `VerificationEligibilite` sont créées
  dans la requête, aucune `Decision` n'est créée, E prend le statut correspondant au verdict du
  moteur, D et J gardent leur statut, et le rapport indique le nombre de verdicts recalculés et de
  statuts modifiés.
- **CA38** — Étant donné J (décision humaine « éligible ») pour lequel le moteur rend « non
  éligible », quand la relance en lot est exécutée, alors une `VerificationEligibilite` « non
  éligible » est créée et le statut de J reste « éligible ».
- **CA39** — Étant donné `LOT_ELIGIBILITE_SYNC_MAX = 3` (surcharge de test) et 5 candidatures
  sélectionnées, quand la relance est exécutée, alors une tâche Celery est mise en file (appel
  simulé vérifié) avec l'identifiant de lot et l'identifiant de l'utilisateur, la réponse affiche
  le message « Relance lancée en arrière-plan pour 5 candidatures » ; en exécution immédiate
  (`CELERY_TASK_ALWAYS_EAGER`), les 5 vérifications sont créées et l'entrée `lot` de fin a pour
  acteur l'utilisateur.
- **CA40** — Étant donné une relance en lot sur B, C, E, alors exactement une entrée d'audit
  `update` par candidature est créée, avec l'utilisateur comme acteur, `metadata.lot_id`,
  `operation = "relance_eligibilite"`, `verification_id` et `statut_modifie` ; aucune entrée
  supplémentaire « moteur d'éligibilité » n'est créée pour ces candidatures.
- **CA41** — Étant donné P3 (concours « Terminé »), quand une relance en lot est demandée, alors
  le récapitulatif ne propose pas l'exécution et un `POST` forgé ne crée aucune vérification.
- **CA42** — Étant donné une erreur simulée du moteur sur C, quand la relance en lot sur B, C, E
  est exécutée, alors B et E sont recalculés et C figure au rapport en « erreur technique ».

**Audit du lot**

- **CA43** — Étant donné le lot de CA24, alors exactement deux entrées `lot` existent pour ce
  `lot_id` (`etape = "lance"` puis `"termine"`), avec objet P1, acteur l'utilisateur,
  `operation = "decision"`, `decision = "convoquee"`, le motif, le mode, les compteurs et les
  identifiants de B, H, J ; et chacune des trois entrées `decision` porte le même `lot_id` et
  `mode = "lot"`.
- **CA44** — Étant donné un lot en mode `filtre` avec la recherche « amrani », alors les entrées
  `lot` contiennent `recherche_appliquee = true` mais ni « amrani », ni aucun nom ou prénom des
  candidats du jeu de référence, ni email, téléphone ou CIN (contrôle sur le JSON sérialisé des
  entrées).

**Interface**

- **CA45** — Étant donné un `POST` de récapitulatif avec `HX-Request: true`, alors la réponse est un
  fragment (sans `<html>` ni navigation) contenant le récapitulatif ; sans cet en-tête, la réponse
  est la page complète. De même pour l'exécution, dont la réponse HTMX déclenche aussi le
  rafraîchissement de `#resultats`.
- **CA46** — Étant donné un navigateur sans JavaScript (requêtes sans `HX-Request`), quand
  l'utilisateur coche des cases, envoie « Appliquer à la sélection », remplit le motif et confirme,
  alors les décisions sont enregistrées et la page de rapport s'affiche avec le lien « Revenir à
  la liste » conservant les filtres, le tri et la taille de page d'origine.
- **CA47** — Étant donné le récapitulatif et le rapport rendus, alors ils contiennent une zone
  `aria-live="polite"`, un titre `h2` avec `tabindex="-1"`, les choix d'avertissement dans un
  `fieldset` avec `legend`, un seul bouton `.btn-primary`, aucun attribut `style` ni script en
  ligne, et aucune classe d'alignement figée à gauche / droite (`ml-`, `mr-`, `pl-`, `pr-`,
  `left-`, `right-`, `text-left`, `text-right`).
- **CA48** — Étant donné le jeu de référence, quand le HTML de la barre, du récapitulatif et du
  rapport est inspecté, alors il ne contient ni email, ni téléphone, ni CIN, ni date de naissance,
  ni ville, ni région, ni sexe des candidats.
- **CA49** — Étant donné la langue active `ar`, quand la liste avec la barre d'actions et le
  récapitulatif sont affichés, alors la balise `html` porte `dir="rtl"` et tous les libellés fixes
  passent par le système de traduction.
- **CA50** — Étant donné 500 candidatures « éligible » fictives sur l'environnement de
  développement, quand un lot « Convoquée » est exécuté, alors il se termine en moins de 20 s
  (vérification explicite, non bloquante en CI).

## Cas limites et erreurs

- **Liste vide / poste sans candidature** : la barre d'actions n'est pas affichée.
- **Filtre sans résultat** : bouton « Appliquer aux N résultats » absent ou désactivé (N = 0) ;
  en cas d'envoi, CA13.
- **Toutes les candidatures non concernées** (ex. « Convoquée » sur des « à vérifier ») : le
  récapitulatif s'affiche, avec « 0 candidature concernée » et sans bouton d'exécution.
- **Tous les dossiers concernés exclus par avertissement** : l'exécution n'enregistre rien et le
  rapport l'indique (« 0 décision enregistrée ») ; les entrées `lot` sont écrites.
- **Données manquantes** : candidature sans verdict (« non évaluée ») → avertissement « verdict
  non favorable » pour « Éligible » ; candidature sans `donnees_declarees` → la relance produit le
  verdict que donne le moteur (en pratique « à vérifier »), jamais une non-éligibilité décidée.
- **Statut inattendu** (donnée importée hors `Candidature.Statut`) : non concernée, raison
  « transition interdite ».
- **Volumes** : plafond 500 (règle 10) ; récapitulatif en nombre de requêtes constant (CA23) ;
  exécution linéaire acceptée (CA50) ; relance > 50 en tâche de fond.
- **Accès concurrent** :
  - deux utilisateurs exécutent des lots qui se recouvrent : le verrou par candidature et
    `statut_attendu` font qu'une seule décision passe par candidature, l'autre est en
    « modifiée entre-temps » ;
  - relance en lot pendant un lot de décisions : verrous par ligne ; le moteur ne reclasse jamais
    une candidature décidée (KAN-13 règle 9) ;
  - synchronisation du site ou webhook pendant un lot : ne modifie pas le statut d'une
    candidature décidée (KAN-13, à vérifier dans `integrations/site_source/sync.py`).
- **Double clic sur « Exécuter »** : second envoi refusé par la règle 19 ou, en cas de
  simultanéité, par le contrôle de concurrence par candidature.
- **Tâche Celery relivrée** (`CELERY_TASK_ACKS_LATE`) : chaque candidature n'est relancée
  qu'une fois par lot (verrou consultatif et entrée `update` existante, règle 25) : ni seconde
  vérification ni seconde entrée `update` ; l'entrée `lot` de fin n'est écrite qu'une fois
  (vérification de l'existence avant écriture).
- **Broker Celery indisponible** au lancement d'une relance asynchrone : lot clos en erreur
  technique, message clair, pas d'erreur 500 (règle 25).
- **Tâche Celery en échec global** (worker arrêté) : pas d'entrée `lot` de fin ; les entrées par
  candidature permettent de savoir ce qui a été fait. Pas de reprise automatique dans le MVP.
- **Session expirée entre récapitulatif et exécution** : redirection vers la connexion ; le motif
  n'est pas conservé ; le jeton expire de lui-même (30 min).
- **Jeton trop volumineux** : 500 entrées tiennent dans un champ caché (quelques dizaines de Ko) ;
  la limite `DATA_UPLOAD_MAX_NUMBER_FIELDS` n'est pas concernée (un seul champ). Si la taille
  pose problème, le développeur peut stocker l'instantané côté serveur (cache) sous l'identifiant
  de lot, sans changer le comportement spécifié.
- **Motif contenant du HTML** : stocké tel quel, toujours échappé à l'affichage.
- **Droits insuffisants** : CA3, CA4.

## Traçabilité et conformité

- **Journal d'audit** : par candidature, une entrée `decision` (avec `lot_id`) ou `update` de
  relance (avec `lot_id`) ; par lot, deux entrées `lot` (lancement, fin) sans données
  personnelles ; une entrée `view` à l'affichage du récapitulatif ; refus de droits et envois
  forgés journalisés. Un lot se reconstitue intégralement à partir de son `lot_id` (qui, quand,
  quelle décision, quel motif, quelles candidatures, quels résultats). Le journal reste en ajout
  seul.
- **Données personnelles** : seuls NOM et prénom sont affichés (barre, récapitulatif, rapport), à
  `admin` et `direction` authentifiés avec TOTP. Le motif commun est soumis aux mêmes règles que
  KAN-13 (visible par `admin` / `direction`, couvert par la conservation et la purge). Aucune
  sélection n'est stockée dans le navigateur. Le terme de recherche n'est pas recopié dans les
  entrées `lot` (il figure déjà dans l'entrée `view` de la liste, KAN-10 Q4).
- **IA** : non utilisée. La relance appelle le moteur déterministe existant.
- **Humain dans la boucle** :
  - chaque candidature reçoit une `Decision` distincte, au nom d'un utilisateur identifié, avec
    motif, via le seul service de décision ;
  - **aucune décision défavorable en lot** (non éligible, rejetée) et pas de « retenue » en lot ;
  - pas de révision en lot ;
  - récapitulatif obligatoire, confirmation explicite, choix explicite sur chaque avertissement
    (aucune option présélectionnée) ;
  - la relance du moteur ne décide jamais et ne reclasse jamais un dossier déjà décidé ; un cas
    incertain reste « à vérifier ».
- **Biais** : la sélection repose uniquement sur les filtres de KAN-10 (statut, verdict,
  anomalies, nom) ; aucun filtre, tri, colonne ni règle de lot fondé sur le sexe, l'âge, la
  région, la ville, la photo ou la situation familiale (CA48).

## Hors périmètre

- Décisions **non éligible**, **rejetée** et **retenue** en lot ; révisions en lot (voir Q1, Q2).
- Actions en lot sur les candidatures d'une **offre d'emploi** (recrutement privé) ou au niveau
  d'un **concours entier** (plusieurs postes).
- Export de la sélection, génération de listes ou de courriers (epic exports / E4).
- Résolution d'anomalies en lot, scoring en lot (E3), notifications aux candidats.
- Annulation d'un lot en une fois : chaque décision se révise individuellement depuis la fiche.
- Page de suivi de progression d'une relance asynchrone (voir Q11).
- Sélection mémorisée entre pages ou entre sessions (voir Q12).
- Workflow de validation à deux niveaux (« quatre yeux ») pour les lots.

## Questions ouvertes

Propositions par défaut cochées à la demande du Product Owner (« avancer ») ; il peut revenir sur
chacune. **[HDB]** = touche l'humain dans la boucle.

- [x] **Q1 [HDB] — « Non éligible » et « rejetée » en lot.** *Proposition par défaut* : interdites
  en lot (règle 3) ; elles restent possibles dossier par dossier depuis la fiche. *Pourquoi* : une
  décision défavorable avec un motif commun, sans examen individuel, s'apparente à une
  élimination de masse et se défend mal en recours. *Alternative* : autoriser « rejetée » en lot
  uniquement depuis « convoquée » (non retenus après l'oral) et seulement pour les candidatures
  dont la fiche a été consultée par l'utilisateur après le dernier changement de statut
  (vérifiable par le journal `view` de KAN-14). *Conséquence de l'alternative* : traitement plus
  rapide des résultats, mais règle plus complexe et nouvelle série de critères.
- [x] **Q2 [HDB] — « Retenue » en lot.** *Proposition par défaut* : interdite (volume faible,
  décision finale, quota). *Alternative* : autorisée depuis « convoquée », avec avertissement de
  quota calculé sur l'ensemble du lot (retenues existantes + lot > `nombre_postes`) et choix
  explicite. *Conséquence* : règle de quota collective à spécifier et tester.
- [x] **Q3 [HDB] — « Éligible » en lot sur des dossiers « à vérifier », « non éligible » (moteur,
  sans décision) ou non évalués.** *Proposition par défaut* : autorisé, avec l'avertissement
  « verdict non favorable » et un choix explicite exclure / inclure (règle 14), tracé dans
  l'audit. *Pourquoi* : cas d'usage réel de volume (lever en série une alerte bénigne après
  contrôle), décision favorable et révisable. *Alternative prudente* : interdire « Éligible » en
  lot depuis « à vérifier » et « non éligible ». *Conséquence* : « Éligible » en lot ne servirait
  plus qu'aux dossiers « reçue » / « en analyse ».
- [x] **Q4 [HDB] — Avertissement anomalie haute en lot.** *Proposition par défaut* : choix explicite
  « exclure » / « inclure : je confirme », sans option présélectionnée. *Alternative* : exclusion
  automatique des dossiers à anomalie haute (à traiter depuis la fiche). *Conséquence* : plus sûr,
  mais oblige à repasser par la fiche pour chaque dossier concerné.
- [x] **Q5 — Plafond par lot.** *Proposition par défaut* : 500, paramètre `LOT_TAILLE_MAX`, sans
  troncature. *Conséquence* : un poste de 2 000 dossiers se traite en plusieurs lots filtrés ;
  relever le plafond impose de passer les décisions en tâche de fond (durée de requête).
- [x] **Q6 — Seuil synchrone de la relance.** *Proposition par défaut* : 50
  (`LOT_ELIGIBILITE_SYNC_MAX`) ; au-delà, Celery. Les décisions restent toujours synchrones (acte
  humain, rapport immédiat).
- [x] **Q7 — Relance d'éligibilité sur un concours « Terminé ».** *Proposition par défaut* :
  interdite (résultats figés, cohérence en cas de recours). *Alternative* : autorisée (les
  vérifications ne sont qu'un historique et ne reclassent pas les dossiers décidés).
- [x] **Q8 — Traitement partiel ou tout-ou-rien.** *Proposition par défaut* : partiel, une
  transaction par candidature, rapport des succès et échecs (règle 20). *Alternative* :
  tout-ou-rien. *Conséquence de l'alternative* : un seul conflit de concurrence ferait échouer
  tout le lot, verrous tenus sur des centaines de lignes pendant toute la requête.
- [x] **Q9 — Champ `lot_id` sur `Decision`.** *Proposition par défaut* : oui (UUID nullable,
  migration additive), affiché « décision prise en lot » sur la fiche. *Alternative* : seulement
  dans le journal d'audit. *Conséquence* : sans le champ, la fiche ne peut pas signaler l'origine
  en lot sans interroger le journal.
- [x] **Q10 — Nouvelle valeur `AuditAction.LOT`.** *Proposition par défaut* : oui (« Action en
  lot »), pour filtrer les lots dans le journal. *Alternative* : réutiliser `update` avec
  `metadata.operation`.
- [x] **Q11 — Suivi d'une relance asynchrone.** *Proposition par défaut* : pas de page de suivi
  dans le MVP ; message de lancement, verdicts visibles dans la liste, entrée `lot` de fin au
  journal. *Alternative* : page de suivi du lot (progression par HTMX). *Conséquence* : nécessite
  un modèle `ActionLot` (état, compteurs).
- [x] **Q12 — Sélection sur plusieurs pages.** *Proposition par défaut* : non ; sélection limitée à
  la page affichée, et « appliquer aux N résultats du filtre » pour les ensembles plus larges.
  *Conséquence* : pas d'état de sélection à stocker.
- [x] **Q13 [HDB] — Motif commun.** *Proposition par défaut* : mêmes règles que KAN-13 (10 à 2 000
  caractères), enregistré tel quel sur chaque décision, sans préfixe automatique ; l'origine en lot
  est portée par `lot_id`. *Alternative* : exiger un motif plus long (ex. 30 caractères) pour un
  lot. *Conséquence* : motifs plus explicites en recours, saisie un peu plus lourde.
- [x] **Q14 — Liste nominative dans le récapitulatif.** *Proposition par défaut* : oui pour les non
  concernées et les dossiers signalés, 100 noms par groupe au maximum. *Alternative* : effectifs
  seuls. *Conséquence* : sans noms, l'utilisateur ne peut pas vérifier les exclusions avant de
  confirmer.
