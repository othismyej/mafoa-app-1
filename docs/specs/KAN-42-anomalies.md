# KAN-42 — Détection des doublons, pièces manquantes et incohérences

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-42
- **Epic** : E3 — Lecture des pièces, IA et évaluation
- **Statut** : validée le 2026-11-27 par le Product Owner (« avancer » : la proposition par défaut
  est retenue pour chaque question ouverte ; le PO peut revenir sur chacune)

## Contexte

Cas d'usage n° 2 du MVP (`CLAUDE.md` §2) : « Signaler pièces manquantes, doublons de candidats,
incohérences (dates, noms) ». Sur un e-Concours public, un doublon (même personne, deux comptes,
deux postes du même concours), un diplôme absent ou une date incohérente sont exactement les
points soulevés en recours. Le brief impose que l'application **signale** sans jamais éliminer
(§4, §6) : une anomalie informe la direction, elle ne change ni le statut, ni le verdict.

Existant dans le code (à réutiliser, ne pas recréer) :

- `apps/evaluation/models.py` — `Anomalie` : `candidature`, `piece` (nullable),
  `candidature_liee` (nullable, `SET_NULL`), `type` (`piece_manquante`, `doublon_candidat`,
  `doublon_piece`, `incoherence_dates`, `incoherence_nom`, `empreinte_differente`, `autre`),
  `gravite` (`info`, `moyenne`, `haute`), `description`, `resolue`, `resolue_par`, `resolue_le`,
  `created_at` ; index `(candidature, resolue)`. **Aucun service ne détecte quoi que ce soit** :
  les anomalies affichées aujourd'hui viennent des données fictives, sauf
  `empreinte_differente` créée par la visionneuse (KAN-12, `apps/recruitment/pieces.py`,
  `signaler_empreinte_differente` : verrou sur la pièce, idempotence, audit `create` au nom de
  « système (contrôle d'empreinte) »).
- `apps/evaluation/admin.py` — `AnomalieAdmin` permet aujourd'hui de **modifier** une anomalie
  (dont `resolue`) sans motif ni trace métier.
- `apps/recruitment/models.py` — `Candidat` calcule à chaque `save()` les clés de rapprochement
  `email_normalise`, `telephone_normalise`, `nom_normalise`, `cle_identite` (nom + prénom triés
  + date de naissance), indexées ; `apps/recruitment/normalization.py` (déterministe : gmail sans
  points ni `+suffixe`, téléphone au format `212XXXXXXXXX`, nom sans accents, mots triés).
  `Candidature` : `type` (concours, offre, spontanée — KAN-39), `concours` dénormalisé,
  contrainte `uniq_candidature_un_poste_par_concours` **par `Candidat`** : la règle du site « un
  seul poste par concours » ne peut donc être contournée qu'avec **deux comptes**, ce que seule la
  détection de doublons révèle. `donnees_declarees` : `formation` (`intitule`, `niveau`,
  `specialite`, `etablissement`, `annee_obtention`), `experiences` (`debut`, `fin`, `employeur`,
  `poste`). `Piece` : `type`, `taille`, `sha256`.
- `apps/evaluation/eligibility/` — `parse_niveau`, `NiveauEtudes`, `match_specialite`,
  `compute_experience` (déterministes, testés). Le moteur ignore déjà une période « fin avant
  début » et rend « à vérifier » ; son commentaire annonce qu'elle « donnera lieu à une
  anomalie ».
- `integrations/site_source/sync.py` — `sync_from_source` (une transaction, import idempotent par
  `source_id`, `SyncReport`, audit `sync` sans données personnelles) ; commande `sync_site
  [--evaluate]`. `fixture_source.py` produit déjà des cas piégés : deux doublons (second compte
  à un autre poste du même concours avec CV partagé ; même email à la casse près dans un autre
  concours), pièces manquantes (pas de diplôme si `idx % 11 == 0`, pas d'attestation si
  `idx % 13 == 0`), dates manquantes ou inversées.
- `apps/evaluation/decisions.py` — avertissement `anomalie_haute` (KAN-13 règle 10) et KAN-15
  (avertissement en lot, échec « nouvel avertissement non confirmé ») : ils lisent
  `anomalies.filter(resolue=False, gravite="haute")`.
- Fiche candidature (`templates/recruitment/candidature_detail.html`, KAN-11 règle 11) : bloc
  « Anomalies » (non résolues d'abord, type, gravité, description, pièce, lien « Voir la
  candidature liée (NOM Prénom) », mention « Résolue par … le … »), compteur dans l'en-tête,
  indicateur sur les pièces. Listes (KAN-10, KAN-39) : filtre « avec / sans anomalie non
  résolue », colonne nombre + gravité maximale. Export Excel (KAN-37) : anomalies non résolues.
- `apps/audit` — `log(...)`, `AuditAction` (dont `create`, `update`, `access_denied`, `lot`),
  index sur `metadata.candidature_id` (historique d'un dossier, KAN-14).
- **KAN-41** (en parallèle) ajoute l'extraction des pièces par l'IA et exposera une API de
  lecture des données extraites ; les contrôles « pièce ↔ candidat » et « déclaré ↔ extrait » de
  cette story s'appuient dessus (interface attendue en section D, non figée).

## User story

En tant que membre de la direction, je veux que l'application signale automatiquement, pour
chaque candidature, les doublons de candidats, les pièces obligatoires manquantes et les
incohérences de dates, d'identité ou entre la déclaration et les pièces, avec leur gravité et
leur explication, et pouvoir résoudre chaque signalement avec un motif, afin de traiter les
dossiers douteux avant toute décision et de justifier ces contrôles en cas de recours, sans
qu'aucun candidat soit écarté automatiquement.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Voir les anomalies, relancer la détection sur une candidature, résoudre et rouvrir une anomalie. Lancer la détection en masse (commande de gestion). Configurer les pièces obligatoires (règle 14). |
| direction | Voir, relancer la détection sur une candidature, résoudre et rouvrir une anomalie. |
| consultant, client (vague 2, non attribuables) | Refusé : 403 sur la relance, la résolution et la réouverture ; rien n'est fait ; refus journalisé `access_denied` (comme KAN-14 règle 8). |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP (middleware existant). |
| non connecté | Redirection vers la connexion ; rien n'est fait. |
| processus automatique (synchronisation, tâche, extraction IA) | Crée des anomalies et marque « cause disparue » ; **ne résout jamais** une anomalie, ne change jamais un statut, ne crée jamais de `Decision`. |

Contrôle côté serveur sur chaque point d'entrée (relance, résolution, réouverture) et chaque
réponse partielle HTMX. L'admin Django des anomalies passe en **lecture seule** (règle 32).

## Règles métier

### A. Principes

1. **Signaler, jamais décider.** Une anomalie n'a **aucun effet** sur `Candidature.statut`, sur
   `VerificationEligibilite`, sur le score ni sur les `Decision`. La détection ne crée jamais de
   `Decision` et n'appelle jamais `record_decision` ni `run_eligibility`. Le seul effet indirect
   est l'avertissement existant « anomalie haute » de KAN-13 / KAN-15 (règle 29).
2. **Détection déterministe.** Tous les contrôles sont du code Python testé, sans appel IA dans
   cette story. Les contrôles « pièce ↔ candidat » et « déclaré ↔ extrait » comparent des données
   **déjà extraites** par KAN-41 ; ils n'en déclenchent pas.
3. **Explicabilité.** Chaque anomalie porte : un type, un **code** (sous-type, tableau règle 6),
   une gravité, une description en français lisible, une **source** (données déclarées, pièce,
   extraction, compte rapproché) et des **détails structurés** (JSON) suffisants pour la
   comprendre sans relancer la détection. La version du détecteur (`DETECTEUR_VERSION`, ex.
   « 1.0.0 ») est enregistrée sur l'anomalie.
4. **Minimisation.** Description, détails et clé d'idempotence ne contiennent **aucune valeur
   d'identification** (email, téléphone, CIN, date de naissance, nom ou prénom), ni de la
   candidature, ni a fortiori de la candidature liée. Ils peuvent citer des dates, années,
   niveaux et spécialités **de la candidature elle-même** (déjà visibles sur sa fiche) et
   l'identifiant interne `#id` de la candidature ou de la pièce liée.
5. **Critères interdits.** Aucun contrôle ne repose sur le sexe, la région, la ville, la photo,
   la situation familiale ou l'âge. La date de naissance ne sert qu'à la clé de rapprochement
   `cle_identite` (doublons) ; aucun âge n'est calculé et aucune cohérence « âge / diplôme » n'est
   contrôlée.

### B. Catalogue des anomalies

6. Les contrôles, codes et gravités sont les suivants (deux nouveaux types :
   `plusieurs_postes_concours` « Plusieurs candidatures dans un même concours » et
   `ecart_declaration` « Écart entre déclaration et pièce » ; migration de choix, voir Q1) :

   | Type | Code | Condition (résumé, détail aux règles 7 à 13) | Gravité |
   |---|---|---|---|
   | `plusieurs_postes_concours` | `autre_compte` | Un **autre compte** rapproché (règle 7) a une candidature dans le **même concours** (même poste ou autre poste) | haute |
   | `doublon_candidat` | `meme_offre` | Un autre compte rapproché a une candidature à la **même offre** | haute |
   | `doublon_candidat` | `meme_cin` | Autre compte rapproché par la CIN (Q3 bis révisée), autre cible ou candidature spontanée | haute |
| `doublon_candidat` | `autre_compte` | Autre compte rapproché par email ou par identité (nom + date de naissance), autre cible ou candidature spontanée | moyenne |
   | `doublon_candidat` | `telephone_seul` | Autre compte rapproché **uniquement** par le téléphone, autre cible ou spontanée | info |
   | `doublon_candidat` | `valeur_partagee` | Une clé de la candidature est partagée par plus de `DOUBLONS_SEUIL_GROUPE` comptes (règle 8) | info |
   | `doublon_piece` | `autre_candidat` | Même empreinte SHA-256 qu'une pièce d'un **autre compte** : diplôme ou attestation de travail | haute |
   | `doublon_piece` | `autre_candidat` | Idem : CV, lettre ou autre | moyenne |
   | `doublon_piece` | `meme_dossier` | Deux pièces de la même candidature ont la même empreinte | info |
   | `piece_manquante` | `<type de pièce>` | Pièce obligatoire absente, ou présente avec `taille = 0` (règles 14 à 16) | haute |
   | `incoherence_dates` | `fin_avant_debut` | Expérience déclarée dont la fin précède le début | moyenne |
   | `incoherence_dates` | `debut_apres_candidature` | Expérience déclarée qui commence après la date de candidature | moyenne |
   | `incoherence_dates` | `diplome_apres_candidature` | Année d'obtention déclarée postérieure à l'année de candidature | moyenne |
   | `incoherence_dates` | `date_illisible` | Date d'expérience absente (début) ou illisible (début ou fin) | info |
   | `incoherence_dates` | `chevauchement` | Deux expériences déclarées se chevauchent d'au moins `CHEVAUCHEMENT_SEUIL_JOURS` jours | info |
   | `incoherence_dates` | `experience_avant_diplome` | Sans l'expérience antérieure au diplôme, le minimum exigé n'est plus atteint (règle 12) | moyenne |
   | `incoherence_nom` | `titulaire_different` | Nom lu sur la pièce ≠ nom du candidat ; aucun mot commun, sur diplôme ou attestation | haute |
   | `incoherence_nom` | `titulaire_different` | Idem, avec des mots communs mais sans inclusion, ou sur un CV / autre pièce | moyenne |
   | `ecart_declaration` | `niveau` | Niveau lu sur le diplôme < niveau déclaré ; **haute** si le niveau lu est inférieur au minimum exigé alors que le niveau déclaré l'atteint | moyenne / haute |
   | `ecart_declaration` | `specialite` | Spécialité lue sans correspondance avec la spécialité déclarée (`match_specialite`) | moyenne |
   | `ecart_declaration` | `annee_obtention` | Année lue différente de l'année déclarée | info |
   | `ecart_declaration` | `periode` | Expérience déclarée rapprochée d'une attestation dont le début ou la fin diffère de plus de 31 jours ; **haute** si, avec les dates attestées, le minimum exigé n'est plus atteint alors qu'il l'était avec les dates déclarées | moyenne / haute |

   Les types `empreinte_differente` (KAN-12) et `autre` ne sont pas produits par ce détecteur et
   restent inchangés.

### C. Doublons

7. **Rapprochement de comptes.** Deux `Candidat` **distincts** sont rapprochés s'ils partagent au
   moins une clé non vide : `email_normalise`, `telephone_normalise` (au moins 9 chiffres),
   `cle_identite` ou `cin_normalise` (CIN en majuscules, sans espaces ni ponctuation, au moins 4
   caractères ; Q3 bis révisée). Le nom seul (homonymes) ne suffit jamais. Les clés sont celles calculées par
   `Candidat.save()` (aucune nouvelle normalisation dans cette story). Un même `Candidat` ayant
   plusieurs candidatures n'est **pas** un doublon (comportement normal ; KAN-39 l'affiche déjà
   comme « Autres candidatures »). Les **candidatures spontanées** sont incluses comme les autres
   (KAN-39 règle 33).
8. **Une anomalie par paire de candidatures**, sur **chacune** des deux (symétrie) : pour une
   candidature X du compte A et une candidature Y du compte B rapproché, X porte une anomalie
   liée à Y (`candidature_liee = Y`) et Y une anomalie liée à X. Le type et le code suivent la
   règle 6 selon la cible commune (même concours > même offre > autre) puis la force du
   rapprochement (email ou identité > téléphone seul). Les critères de rapprochement (`email`,
   `telephone`, `identite`) figurent dans les détails, sans valeur. **Valeur partagée** : si une
   valeur de clé est portée par plus de `DOUBLONS_SEUIL_GROUPE` comptes (paramètre, **5** par
   défaut, Q4), elle n'est pas utilisée pour créer des paires (numéro de standard, adresse
   générique) ; chaque candidature concernée reçoit une seule anomalie `valeur_partagee`
   (gravité info, sans candidature liée, détails : critère et nombre de comptes).
9. **Description** (exemples, sans valeur personnelle) :
   - `plusieurs_postes_concours` : « Un autre compte candidat (rapproché par : email, identité) a
     aussi postulé à ce concours (candidature #Y, autre poste). Règle du site : un seul poste par
     concours. » (« même poste » le cas échéant) ;
   - `doublon_candidat` : « Un autre compte candidat (rapproché par : téléphone) a une
     candidature (#Y). » ;
   - `doublon_piece` : « Fichier identique (même empreinte) à une pièce d'un autre compte
     candidat (candidature #Y, Diplôme). ».
   Sur la fiche, le lien existant « Voir la candidature liée (NOM Prénom) » reste le seul élément
   de la candidature liée affiché, avec sa cible (intitulé du poste, de l'offre ou « Candidature
   spontanée ») ; aucun email, téléphone, CIN, date de naissance, statut, pièce ni décision de la
   candidature liée n'est affiché. Ouvrir la candidature liée est une consultation distincte,
   journalisée (KAN-14).
10. **Pièces en double.** Deux pièces de `sha256` identique et non vide, de `taille` inconnue ou
    > 0 (les fichiers vides ont tous la même empreinte et relèvent de la règle 15) :
    - appartenant à des candidatures de **comptes différents** : anomalie `doublon_piece` /
      `autre_candidat` sur chacune des deux candidatures, rattachée à la pièce (`piece`) et liée à
      l'autre candidature ; gravité selon le type de **la pièce de la candidature qui porte
      l'anomalie** (règle 6) ;
    - dans la **même** candidature : une anomalie `meme_dossier` (info) rattachée à la seconde
      pièce (identifiant le plus élevé) ;
    - dans deux candidatures du **même** compte (CV réutilisé) : aucune anomalie.
    Le même seuil de groupe (règle 8) s'applique à une empreinte partagée par plus de
    `DOUBLONS_SEUIL_GROUPE` comptes (ex. modèle de lettre) : une anomalie info par pièce, sans lien.

### D. Incohérences

11. **Dates déclarées** (`donnees_declarees`, sans IA ; date de comparaison = date locale de
    `date_candidature`) — une anomalie par entrée concernée :
    - `fin_avant_debut` : `fin < debut` ;
    - `debut_apres_candidature` : `debut > date de candidature` ; une **fin** postérieure à la
      candidature n'est **pas** une anomalie (contrat à durée déterminée en cours, Q15) ;
    - `diplome_apres_candidature` : `annee_obtention > année de candidature` ;
    - `date_illisible` : `debut` absent, ou `debut` / `fin` non vides mais illisibles (même
      analyse que `services._parse_date`) ; une `fin` vide signifie « en cours » et n'est pas une
      anomalie ;
    - `chevauchement` : deux expériences (dates lisibles, fin vide = date de candidature) dont
      l'intersection compte au moins `CHEVAUCHEMENT_SEUIL_JOURS` jours (paramètre, **180** par
      défaut, Q16), une anomalie par paire.
    Une clé `experiences` absente ou une liste vide ne produit aucune anomalie de dates.
12. **Expérience antérieure au diplôme** (`experience_avant_diplome`) : uniquement pour une
    candidature de concours ou d'offre dont la cible a un `experience_min_annees`. Diplôme de
    référence = le diplôme déclaré de niveau le plus élevé atteignant `niveau_etudes_min` (ou le
    plus élevé si aucun minimum), avec une année d'obtention lisible ; date d'obtention retenue =
    **1er janvier** de cette année (interprétation favorable au candidat). Si
    `compute_experience` sur les périodes déclarées donne un total ≥ minimum, mais < minimum une
    fois les périodes tronquées pour ne commencer qu'à cette date, l'anomalie est créée (détails :
    total déclaré, total postérieur au diplôme, minimum, date de référence de la cible). Elle ne
    modifie pas le verdict d'éligibilité (Q10 : la règle « expérience postérieure au diplôme »
    n'est pas un critère du moteur dans le MVP).
13. **Pièce ↔ candidat et déclaré ↔ extrait** (dépend de KAN-41) :
    - le détecteur lit les données extraites par une **interface de lecture** fournie par KAN-41,
      attendue sous la forme suivante (indicative, non figée ; le développeur l'adapte à KAN-41
      sans changer le comportement spécifié) :
      `extractions_candidature(candidature) -> list[PieceExtraite]`, où chaque `PieceExtraite`
      porte `piece_id`, `type_piece`, `extraction_id`, `modele`, `prompt_version`, `extrait_le`
      et des champs `ChampExtrait(valeur, confiance ∈ [0, 1], extrait cité)` : `titulaire` (nom
      complet lu), `diplome` (`intitule`, `niveau`, `specialite`, `annee_obtention`),
      `periodes` (attestations : `employeur`, `poste`, `debut`, `fin`) ; seule la **dernière**
      extraction de chaque pièce est renvoyée ;
    - un champ absent ou de confiance inférieure à `EXTRACTION_CONFIANCE_MIN` (paramètre, **0,7**
      par défaut, Q11) est **ignoré** : il ne produit aucune anomalie (ni n'en fait disparaître
      la cause, règle 21) ;
    - `incoherence_nom` : `normalize_name(titulaire)` comparé à `Candidat.nom_normalise` par
      ensembles de mots ; **aucune anomalie** si les ensembles sont égaux ou si l'un est inclus
      dans l'autre (prénom composé, nom d'épouse) ; sinon anomalie rattachée à la pièce, gravité
      règle 6 ;
    - `ecart_declaration` / `niveau` et `specialite` : la pièce de type diplôme est comparée au
      diplôme déclaré de même niveau ou, à défaut, au diplôme déclaré de niveau le plus élevé ;
      `niveau` via `parse_niveau` (niveau lu non reconnu : ignoré), `specialite` via
      `match_specialite(lue, [déclarée])` ; `annee_obtention` : égalité stricte ;
    - `ecart_declaration` / `periode` : une expérience déclarée est rapprochée d'une période
      d'attestation si les employeurs normalisés (`normalize_text`) sont égaux ou inclus l'un dans
      l'autre ; une expérience sans attestation rapprochée ne produit **aucune** anomalie (le
      manque d'attestation relève des pièces obligatoires) ;
    - les détails citent l'`extraction_id`, le modèle, la version du prompt, la valeur lue
      (niveau, spécialité, année, dates — jamais le nom lu), la confiance et l'extrait cité,
      pour l'explicabilité ; la valeur du nom lu n'est pas recopiée (minimisation, règle 4) ;
    - tant que KAN-41 n'est pas fusionné, l'interface renvoie une liste vide : ces contrôles ne
      produisent rien et sont comptés « non exécutés » au rapport (règle 23).

### E. Pièces obligatoires

14. **Configuration.** Nouveau champ `pieces_obligatoires` (liste de valeurs de `Piece.Type`,
    nullable = « par défaut ») sur `Concours`, `PosteConcours` et `OffreEmploi`. Résolution pour
    une candidature : poste (si renseigné) → concours (si renseigné) → valeur par défaut du
    paramètre `PIECES_OBLIGATOIRES_DEFAUT` par type de candidature : **concours = CV + diplôme**,
    **offre = CV**, **spontanée = CV** (Q5). Une liste vide `[]` signifie « aucune pièce
    obligatoire ». Valeur hors de `Piece.Type` ou `autre` : refusée à l'enregistrement. Édition
    par l'admin Django (rôle `admin`), journalisée `update` (avant / après) ; pas d'écran dédié
    dans cette story.
15. **Contrôle.** Pour chaque type obligatoire, au moins une pièce de ce type avec `taille`
    inconnue ou > 0 doit exister ; sinon une anomalie `piece_manquante` (code = type) de gravité
    **haute** : « Pièce obligatoire manquante : Diplôme. » ou, si seules des pièces de taille 0
    existent, « Pièce obligatoire reçue vide : Diplôme. » (rattachée à cette pièce). Les pièces
    de type `autre` ne remplacent jamais une pièce obligatoire.
16. Modifier la configuration ne relance pas la détection d'elle-même ; elle s'applique à la
    prochaine exécution (règles 18 à 20), qui crée les nouvelles anomalies et marque « cause
    disparue » celles qui ne sont plus fondées.

### F. Idempotence et cycle de vie

17. **Clé d'idempotence.** Chaque anomalie produite par le détecteur porte une `cle` non
    nominative et stable (ex. `doublon_candidat:cand=812`, `piece_manquante:diplome`,
    `incoherence_dates:fin_avant_debut:exp=<empreinte courte des champs de l'entrée>`,
    `ecart_declaration:niveau:piece=455`). Contrainte d'unicité en base sur `(candidature, cle)`
    pour `cle` non vide : **jamais deux anomalies de même clé sur une candidature**, même en cas
    d'exécutions concurrentes. Les anomalies sans clé (données fictives existantes,
    `empreinte_differente`) ne sont jamais touchées par le détecteur.
18. **Exécution sur une candidature** :
    - cause constatée, aucune anomalie de cette clé → création (audit `create`) ;
    - cause constatée, anomalie existante **non résolue** → aucune écriture (description, gravité
      et date de détection inchangées) ; si elle était marquée « cause disparue », la marque est
      retirée (audit `update`) ;
    - cause constatée, anomalie existante **résolue** → aucune écriture, aucune nouvelle anomalie
      (le jugement humain est respecté ; même politique que KAN-12) ;
    - cause **non** constatée, anomalie existante non résolue → marque « cause disparue »
      (`cause_disparue_le` = maintenant, audit `update`), **jamais de résolution automatique** ;
      déjà marquée → aucune écriture ;
    - cause non constatée, anomalie résolue → aucune écriture.
19. **« Cause disparue »** : l'anomalie reste **non résolue** ; elle continue de compter dans les
    compteurs, les filtres « avec anomalie », l'export et l'avertissement « anomalie haute »
    (Q6). La fiche l'affiche avec le badge « Cause disparue le JJ/MM/AAAA — à confirmer » et
    propose la résolution avec l'issue « Cause disparue, confirmée » (règle 25).
20. **Champs ajoutés à `Anomalie`** (migration additive, nuls ou vides pour l'existant) :
    `code`, `cle`, `details` (JSON), `detecteur_version`, `cause_disparue_le`,
    `issue_resolution`, `motif_resolution`. Sur `Candidature` : `anomalies_verifiees_le`
    (dernière exécution complète du détecteur pour ce dossier).
21. **Contrôle non exécutable ≠ cause disparue.** Si un contrôle n'a pas pu s'exécuter (pièce
    sans extraction ou champ sous le seuil de confiance, règle 13 ; date de référence absente,
    règle 12), les anomalies de ce contrôle sont laissées telles quelles. Une pièce qui n'existe
    plus dans l'application, une candidature liée supprimée (`candidature_liee` devenue nulle) ou une clé de
    rapprochement qui ne correspond plus valent « cause non constatée ».

### G. Déclenchement

22. **Trois déclencheurs**, un seul service (`detecter_anomalies(candidatures, *, declencheur,
    acteur=None, run_id)`, forme indicative) :
    - **après synchronisation** : à la validation de la transaction de `sync_from_source`
      (`transaction.on_commit`), une tâche Celery `evaluation.detecter_anomalies` est mise en file
      avec les candidatures créées ou mises à jour, et les candidatures des comptes rapprochés
      sont réexaminées pour les contrôles de doublons. Si la mise en file échoue, la
      synchronisation **n'échoue pas** ; l'entrée d'audit `sync` porte `detection = "non_lancee"`
      et la détection est rattrapée à la synchronisation suivante ou manuellement ;
    - **après extraction** (KAN-41) : à la fin d'une extraction validée, le détecteur est appelé
      pour la candidature de la pièce (contrôles règle 13 et pièces en double), même tâche ;
    - **relance manuelle** : bouton « Relancer la détection » sur la fiche (`POST`, CSRF,
      `admin` / `direction`, exécution synchrone pour cette candidature, doublons compris), qui
      réaffiche le bloc Anomalies (fragment HTMX, ou page complète sans JavaScript) ; et commande
      de gestion `detecter_anomalies [--concours ID | --poste ID | --offre ID | --spontanees |
      --tout]` pour la détection en masse.
    La commande `sync_site` affiche le nombre d'anomalies créées / marquées « cause disparue »
    lorsqu'elle s'exécute en mode immédiat.
23. **Rapport d'exécution** (renvoyé et journalisé, règle 34) : nombre de candidatures examinées,
    anomalies créées par type et gravité, marquées « cause disparue », marques retirées, contrôles
    non exécutés (par contrôle), erreurs techniques (identifiants), durée.
24. **Concours terminé, candidature rejetée ou décidée** : la détection s'exécute quand même
    (l'anomalie n'est qu'une information, utile en recours ; Q9). Elle ne modifie rien d'autre.

### H. Résolution humaine

25. **Résoudre** une anomalie non résolue depuis la fiche (`POST`, CSRF, `admin` / `direction`) :
    - **issue** obligatoire parmi : « Vérifiée : anomalie confirmée et traitée »
      (`confirmee`), « Vérifiée : pas une anomalie » (`faux_positif`), « Cause disparue,
      confirmée » (`cause_disparue`, proposée seulement si l'anomalie est marquée « cause
      disparue ») ;
    - **motif** obligatoire, 10 à 2 000 caractères après suppression des espaces (mêmes règles et
      messages que `decisions.nettoyer_motif`) ;
    - effet : `resolue = True`, `resolue_par`, `resolue_le`, `issue_resolution`,
      `motif_resolution` ; sous verrou de la ligne `Anomalie` ; une anomalie déjà résolue
      entre-temps est refusée (409, « Cette anomalie a déjà été résolue. La fiche a été
      actualisée. »), rien n'est écrit.
26. **Rouvrir** une anomalie résolue (même contrôle, motif obligatoire, mêmes bornes) :
    `resolue = False`, champs de résolution vidés (l'historique reste dans le journal),
    `cause_disparue_le` vidé ; la prochaine exécution du détecteur la remarque « cause disparue »
    si la cause n'existe plus.
27. **Indépendance des anomalies symétriques** : résoudre l'anomalie de doublon de X ne résout
    pas celle de Y (chaque dossier est examiné et motivé séparément ; Q7). La fiche de X indique
    sous l'anomalie : « Anomalie correspondante sur la candidature liée : non résolue » ou
    « … : résolue », sans autre détail.
28. Ni la résolution ni la réouverture ne modifient le statut, le verdict, les décisions ou les
    autres anomalies. La résolution est permise quel que soit le statut du concours. Aucune
    résolution en lot dans cette story.

### I. Effets, affichage et performance

29. **Avertissement « anomalie haute »** (KAN-13 règle 10, KAN-15 règle 14) : inchangé dans son
    principe ; il porte désormais aussi sur les anomalies détectées (gravité haute, non
    résolues, y compris « cause disparue »). Aucun autre effet sur les décisions.
30. **Fiche** (bloc Anomalies, KAN-11 règle 11, complété) : libellé du code (ex. « Pièce
    obligatoire manquante — Diplôme »), source (« Données déclarées », « Pièce : Diplôme
    (fichier.pdf) », « Extraction du JJ/MM/AAAA, modèle …, prompt … » avec l'extrait cité,
    « Compte rapproché par : email, identité »), badge « cause disparue », issue et motif de
    résolution pour une anomalie résolue, formulaires « Résoudre » et « Rouvrir » (motif + issue,
    un seul `.btn-primary` par formulaire), bouton « Relancer la détection », mention
    « Détection effectuée le JJ/MM/AAAA HH:MM » ou « Détection non encore effectuée ». Libellés
    traduits (`{% translate %}`), propriétés logiques RTL, aucun script ni style en ligne,
    contenus échappés.
31. **Listes et exports** : les filtres, colonnes et exports existants (KAN-10, KAN-37, KAN-39)
    s'appliquent sans changement aux anomalies détectées. Pas de nouveau filtre par type dans
    cette story.
32. **Admin Django** : `AnomalieAdmin` en consultation seule (ajout, modification et suppression
    désactivés), comme `DecisionAdmin` : la fiche est le seul point de résolution.
33. **Volumes** : la détection en masse traite **10 000 candidatures** :
    - rapprochements par regroupement en base (une requête par clé, pas de comparaison paire à
      paire en Python sur l'ensemble) ; nombre de requêtes SQL borné par lot de traitement et
      indépendant du nombre de candidatures dans un lot ;
    - traitement par lots de `DETECTION_LOT_TAILLE` candidatures (paramètre, **500**), une
      transaction par lot ; l'échec d'un lot est rattrapé, compté « erreur technique » et
      n'interrompt pas les autres ;
    - écritures groupées (`bulk_create`) des anomalies et des entrées d'audit ;
    - les exécutions concurrentes (synchronisation, relance manuelle, extraction) sont
      sérialisées par un verrou consultatif PostgreSQL par candidature ou par exécution en masse ;
      l'unicité `(candidature, cle)` reste le garde-fou final.

### J. Audit

34. **Entrées** (aucune donnée personnelle de candidat : ni nom, ni email, téléphone, CIN, date
    de naissance) :
    - **création** d'une anomalie : `create`, objet = l'anomalie, `object_repr` « Anomalie #<id>
      (Candidature #<id>) », acteur = l'utilisateur pour une relance manuelle, sinon
      `actor_repr` « système (détection d'anomalies) » ; `after` = type, code, gravité ;
      `metadata` = `candidature_id`, `candidature_liee_id`, `piece_id`, `run_id`,
      `declencheur` (`sync`, `extraction`, `manuel`, `commande`), `detecteur_version` ;
    - **cause disparue** / **marque retirée** : `update`, `before` / `after` =
      `cause_disparue_le`, mêmes métadonnées ;
    - **résolution** / **réouverture** : `update` au nom de l'utilisateur, `before` / `after` =
      `resolue`, `issue_resolution`, `metadata` = `candidature_id`, `motif`, `operation`
      (`resolution` / `reouverture`), IP et navigateur ;
    - **exécution** : une entrée par exécution, nouvelle valeur `AuditAction.DETECTION`
      (« Détection d'anomalies », Q12), objet = la candidature (relance manuelle) ou sans objet
      (masse, synchronisation), `metadata` = `run_id`, `declencheur`, compteurs du rapport
      (règle 23), identifiants en erreur (200 au plus, comme `MAX_IDS_AUDIT`) ;
    - **refus** : 403 → `access_denied` (`metadata.chemin`, `metadata.candidature_id`) renvoyé
      (non levé) pour survivre à `ATOMIC_REQUESTS` ; un identifiant d'anomalie n'appartenant pas à
      la candidature de l'URL → 404 et entrée `update` avec `metadata.refus =
      "anomalie_hors_candidature"`. Les erreurs de saisie (motif trop court, issue absente) ne
      sont pas journalisées ;
    - **configuration** des pièces obligatoires : `update` sur le concours, le poste ou l'offre,
      `before` / `after` = `pieces_obligatoires`.
    `metadata.candidature_id` étant indexé, toutes ces entrées apparaissent dans l'historique du
    dossier (KAN-14).

## Critères d'acceptation

Jeu de données de référence (fictif ; emails en `example.org`, téléphones en `0600…`) :
concours **C1** (« En évaluation », `pieces_obligatoires` nul) avec postes **P1** (Bac+5, spécialité
« Génie civil », expérience min. 3 ans, date limite 2026-10-31) et **P2** ; concours **C2** avec
poste **P3** ; concours **C3** (« Terminé ») avec poste **P4** ; offre **O1** (expérience min. 2
ans). Date de candidature : 2026-10-15 pour toutes les candidatures.

| Réf. | Compte (fictif) | Clés | Candidature → cible | Pièces |
|---|---|---|---|---|
| K1 | EL AMRANI Sara | email `sara.elamrani@example.org`, tél. `0600000101`, née 1990-04-12 | A1 → P1 | CV (sha `aa…`), diplôme |
| K2 | Sara El Amrani | email `s.elamrani.pro@example.org`, tél. `+212 600000101`, née 1990-04-12 | A2 → P2 | CV (sha `aa…`), diplôme |
| K3 | BENNANI Youssef | email `Youssef.Bennani@Example.org`, né 1985-02-02 | A3 → P1 | CV, diplôme (taille 0) |
| K4 | BENNANI Youssef | email `youssef.bennani@example.org`, né 1987-07-07 | A4 → P3 | CV, diplôme |
| K5 | TAZI Leila | tél. `0600000505` | A5 → P1 | CV, diplôme |
| K6 | ALAOUI Karim | tél. `06 00 00 05 05` | A6 → O1 | aucune |
| K7 | ZIANI Nora | née 1993-03-03 | A7 → P1 | CV, diplôme |
| K8 | Nora ZIANI | autre email, née 1993-03-03 | S2 (spontanée) | CV |
| K9 / K10 | IDRISSI Hamza / IDRISSI Hamza | nés 1988-01-01 / 1992-05-05, emails et tél. différents | A9 → P1 / A10 → P1 | CV, diplôme (même sha `bb…` pour les deux) |

Données déclarées utiles : A1 = diplôme Bac+5 « Génie civil » obtenu en 2018, expériences
2015-01-01 → 2017-12-31 et 2024-01-01 → en cours ; A3 = une expérience 2022-06-01 → 2021-03-01 ;
A5 = une expérience débutant le 2027-01-01 ; A7 = diplôme Bac+5 « Génie civil », expériences
2020-01-01 → 2022-12-31 et 2022-01-01 → 2023-06-30 ; A9 = expériences 2019-01-01 → 2020-12-31 et
2020-11-01 → 2022-12-31 (chevauchement de 61 jours). Les autres candidatures ont des données
déclarées sans incohérence. Le détecteur est exécuté par la commande `detecter_anomalies --tout`
sauf mention contraire ; l'interface d'extraction (KAN-41) est simulée.

**Doublons**

- **CA1** — Étant donné le jeu de référence, quand la détection est exécutée, alors A1 porte une
  anomalie `plusieurs_postes_concours`, gravité haute, `candidature_liee = A2`, et A2 la même
  anomalie liée à A1 ; les détails des deux citent les critères `email` absent, `telephone` et
  `identite` présents.
- **CA2** — Étant donné le jeu de référence, alors A3 et A4 portent chacune une anomalie
  `doublon_candidat` / `autre_compte`, gravité moyenne, liée à l'autre (email identique à la
  casse près).
- **CA3** — Étant donné le jeu de référence, alors A5 et A6 portent chacune une anomalie
  `doublon_candidat` / `telephone_seul`, gravité info.
- **CA4** — Étant donné K7 et K8 (même nom inversé et même date de naissance), alors A7 et la
  candidature spontanée S2 portent chacune une anomalie `doublon_candidat`, gravité moyenne,
  liée à l'autre.
- **CA5** — Étant donné K9 et K10 (homonymes, dates de naissance différentes), alors ni A9 ni A10
  ne porte d'anomalie `doublon_candidat` ni `plusieurs_postes_concours` (le nom seul ne suffit
  pas ; leur diplôme partagé relève de CA9).
- **CA6** — Étant donné un compte K11 ayant deux candidatures (P3 et O1), alors aucune anomalie de
  doublon n'est créée entre ces deux candidatures.
- **CA7** — Étant donné un compte K12 ayant postulé à O1 et un compte K13 de même email ayant
  aussi postulé à O1, alors chacune porte une anomalie `doublon_candidat` / `meme_offre`, gravité
  haute.
- **CA8** — Étant donné `DOUBLONS_SEUIL_GROUPE = 5` et 6 comptes partageant le téléphone
  `0600009999`, alors aucune anomalie liée n'est créée pour ce critère et chacune des 6
  candidatures porte exactement une anomalie `valeur_partagee`, gravité info, sans
  `candidature_liee` ; avec 5 comptes, les anomalies par paire sont créées.
- **CA9** — Étant donné les CV de A1 et A2 de même empreinte, alors A1 et A2 portent chacune une
  anomalie `doublon_piece`, gravité moyenne, rattachée à leur propre CV et liée à l'autre ;
  étant donné les diplômes de A9 et A10 de même empreinte (comptes non rapprochés), alors A9 et
  A10 portent chacune une anomalie `doublon_piece` de gravité haute ; étant donné deux pièces de
  taille 0 dans deux candidatures de comptes différents, aucune anomalie `doublon_piece`.
- **CA10** — Étant donné deux pièces de même empreinte dans une même candidature, alors une seule
  anomalie `doublon_piece` / `meme_dossier`, gravité info, rattachée à la pièce d'identifiant le
  plus élevé ; étant donné le même CV dans deux candidatures du même compte, aucune anomalie.
- **CA11** — Étant donné les anomalies de CA1 à CA10, alors aucune description, aucun détail et
  aucune clé ne contient un email, un téléphone (sous aucune forme), une date de naissance, un
  nom ou un prénom des comptes du jeu de référence (contrôle sur le texte et le JSON sérialisé).
- **CA12** — Étant donné la fiche de A1, quand elle est affichée, alors l'anomalie de CA1 montre
  le lien « Voir la candidature liée » vers A2 avec NOM Prénom et l'intitulé de P2, et la page ne
  contient ni l'email, ni le téléphone, ni le CIN, ni la date de naissance de K2, ni le statut,
  les pièces ou les décisions de A2.

**Pièces obligatoires**

- **CA13** — Étant donné le jeu de référence (C1 sans configuration), alors A5 ne porte aucune
  anomalie `piece_manquante` ; A3 porte `piece_manquante` / `diplome`, gravité haute, description
  « Pièce obligatoire reçue vide : Diplôme. », rattachée au diplôme de taille 0 ; une
  candidature de C1 sans diplôme porte « Pièce obligatoire manquante : Diplôme. » sans pièce
  rattachée.
- **CA14** — Étant donné A6 (offre, aucune pièce), alors A6 porte exactement une anomalie
  `piece_manquante` / `cv` ; étant donné S2 (spontanée avec CV), aucune.
- **CA15** — Étant donné `P1.pieces_obligatoires = ["cv", "diplome", "attestation_travail"]`,
  alors chaque candidature de P1 sans attestation porte `piece_manquante` / `attestation_travail`,
  et les candidatures de P2 (héritant de C1, défaut) n'en portent pas ; étant donné
  `C1.pieces_obligatoires = []` et `P1` nul, alors aucune candidature de C1 ne porte
  `piece_manquante`.
- **CA16** — Étant donné une candidature de C1 dont la seule « pièce » est de type `autre`, alors
  elle porte deux anomalies `piece_manquante` (CV, diplôme).
- **CA17** — Étant donné l'enregistrement de `pieces_obligatoires = ["photo"]` ou `["autre"]`,
  alors il est refusé par la validation du modèle ; étant donné une modification valide par
  l'admin Django, alors une entrée `update` porte `pieces_obligatoires` avant et après.

**Incohérences de dates**

- **CA18** — Étant donné A3, alors A3 porte `incoherence_dates` / `fin_avant_debut`, gravité
  moyenne, dont la description cite « 01/06/2022 → 01/03/2021 » ; étant donné A5, alors A5 porte
  `debut_apres_candidature`, gravité moyenne.
- **CA19** — Étant donné une expérience « en cours » ou dont la fin (2027-06-30) est postérieure
  à la date de candidature, alors aucune anomalie de dates n'est créée pour cette entrée.
- **CA20** — Étant donné un diplôme déclaré `annee_obtention = 2027`, alors
  `diplome_apres_candidature` est créée ; étant donné `debut = null` et `debut = "2020-13-45"`
  sur deux expériences, alors deux anomalies `date_illisible`, gravité info.
- **CA21** — Étant donné A7 (chevauchement de 365 jours) et A9 (61 jours), avec
  `CHEVAUCHEMENT_SEUIL_JOURS = 180`, alors A7 porte une anomalie `chevauchement`, gravité info, et
  A9 aucune.
- **CA22** — Étant donné A1 (5,83 ans au total, 2,83 ans depuis le 01/01/2018, minimum 3 ans),
  alors A1 porte `experience_avant_diplome`, gravité moyenne, dont les détails donnent le total
  déclaré, le total postérieur au diplôme, le minimum et la date de référence 31/10/2026 ; avec
  un minimum de 2 ans, aucune ; pour S2 (spontanée), aucune.
- **CA23** — Étant donné `donnees_declarees = {}` ou `experiences = []`, alors aucune anomalie de
  dates et aucune erreur.

**Pièce ↔ candidat et déclaré ↔ extrait** (interface d'extraction simulée)

- **CA24** — Étant donné une extraction du diplôme de A1 de titulaire « Sara EL AMRANI »
  (confiance 0,95), ou du CV de A5 de titulaire « Leïla Tazi-Berrada », alors aucune anomalie
  `incoherence_nom`.
- **CA25** — Étant donné une extraction du diplôme de A3 de titulaire « Hamza IDRISSI »
  (confiance 0,9), alors A3 porte `incoherence_nom`, gravité haute, rattachée au diplôme, dont les
  détails citent `extraction_id`, modèle et version de prompt mais ni « Hamza » ni « IDRISSI » ;
  avec titulaire « Youssef ALAMI » (un mot commun), gravité moyenne ; même titulaire sur le CV,
  gravité moyenne.
- **CA26** — Étant donné le même cas qu'en CA25 avec une confiance de 0,6
  (`EXTRACTION_CONFIANCE_MIN = 0,7`), alors aucune anomalie n'est créée.
- **CA27** — Étant donné A7 déclarant un Bac+5 et une extraction de son diplôme lue « Bac+3 »
  (P1 exige Bac+5), alors A7 porte `ecart_declaration` / `niveau`, gravité haute ; étant donné un
  diplôme déclaré Bac+5 lu « Bac+4 » pour un poste exigeant Bac+3, gravité moyenne.
- **CA28** — Étant donné A1 déclarant « Génie civil » et une extraction lue « Génie mécanique »,
  alors `ecart_declaration` / `specialite`, gravité moyenne ; lue « génie civil » (casse),
  aucune ; année lue 2019 pour 2018 déclarée, `annee_obtention`, gravité info.
- **CA29** — Étant donné une expérience déclarée « Société Fictive Eau » 2019-01-01 → 2023-12-31
  et une attestation lue « SOCIETE FICTIVE EAU » 2021-01-01 → 2023-12-31 sur un poste exigeant
  4 ans, alors `ecart_declaration` / `periode`, gravité haute ; avec un écart de 20 jours, aucune ;
  une expérience sans attestation rapprochée, aucune.
- **CA30** — Étant donné l'interface d'extraction renvoyant une liste vide (KAN-41 absent), alors
  la détection s'exécute sans erreur, ne crée aucune anomalie `incoherence_nom` ni
  `ecart_declaration`, et le rapport compte ces contrôles « non exécutés ».

**Idempotence et cycle de vie**

- **CA31** — Étant donné une détection déjà exécutée sur le jeu de référence, quand elle est
  relancée sans changement, alors aucune anomalie n'est créée ni modifiée et aucune entrée
  `create` ou `update` d'anomalie n'est écrite (seule l'entrée `detection` d'exécution).
- **CA32** — Étant donné deux exécutions simultanées de la détection sur A1 (deux threads,
  base PostgreSQL de test), alors il n'existe qu'une anomalie par clé sur A1.
- **CA33** — Étant donné l'anomalie `piece_manquante` / `diplome` d'une candidature, quand la
  pièce arrive par synchronisation puis que la détection s'exécute, alors l'anomalie reste
  `resolue = False`, `cause_disparue_le` est renseigné, une entrée `update` est écrite, et la
  fiche affiche « Cause disparue le … — à confirmer » ; quand la pièce disparaît à nouveau, la
  marque est retirée (entrée `update`), sans nouvelle anomalie.
- **CA34** — Étant donné une anomalie de doublon résolue « faux positif », quand la détection est
  relancée et que la cause existe toujours, alors aucune nouvelle anomalie n'est créée et
  l'anomalie résolue est inchangée.
- **CA35** — Étant donné une anomalie de fixture sans clé et une anomalie `empreinte_differente`,
  quand la détection est exécutée, alors elles sont inchangées.
- **CA36** — Étant donné une anomalie `ecart_declaration` existante, quand la dernière extraction
  de la pièce a une confiance inférieure au seuil, alors l'anomalie n'est pas marquée « cause
  disparue » ; quand la pièce n'existe plus dans l'application (supprimée en base, ex. purge),
  elle l'est.
- **CA37** — Étant donné une candidature A1 avec une anomalie haute marquée « cause disparue »,
  quand on décide « convoquée » (KAN-13), alors l'avertissement `anomalie_haute` est demandé.

**Absence d'effet sur les décisions**

- **CA38** — Étant donné le jeu de référence avec statuts et décisions variés, quand la détection
  est exécutée, alors aucun `Candidature.statut`, aucune `Decision`, aucune
  `VerificationEligibilite` et aucun `Score` n'est créé ni modifié (comparaison avant / après).
- **CA39** — Étant donné P4 (concours « Terminé ») avec une candidature sans diplôme, quand la
  détection est exécutée, alors l'anomalie `piece_manquante` est créée et le statut reste
  inchangé.

**Déclenchement**

- **CA40** — Étant donné `FixtureSource` et `CELERY_TASK_ALWAYS_EAGER`, quand `sync_from_source`
  est exécutée, alors la tâche de détection est mise en file après validation de la transaction
  (appel vérifié), les deux doublons de la source fictive produisent des anomalies de doublon et
  les dossiers sans diplôme une anomalie `piece_manquante` ; si la mise en file lève une erreur
  de connexion au broker, la synchronisation réussit et l'entrée `sync` porte
  `detection = "non_lancee"`.
- **CA41** — Étant donné une synchronisation annulée (`SyncError`), alors aucune tâche de
  détection n'est mise en file.
- **CA42** — Étant donné un utilisateur `direction`, quand il envoie « Relancer la détection » sur
  la fiche de A1 avec `HX-Request: true`, alors la réponse est le fragment du bloc Anomalies à
  jour, `anomalies_verifiees_le` est renseigné, et une entrée `detection` a pour acteur cet
  utilisateur et pour objet A1 ; sans l'en-tête, la page complète de la fiche est renvoyée.
- **CA43** — Étant donné la fin d'une extraction (simulée) du diplôme de A3, alors la détection
  est appelée pour A3 avec `declencheur = "extraction"`.

**Résolution humaine**

- **CA44** — Étant donné l'anomalie de CA1 sur A1, quand un utilisateur `direction` la résout
  avec l'issue `confirmee` et le motif « Deux comptes vérifiés, même personne : signalé au
  client. », alors `resolue = True`, `resolue_par`, `resolue_le`, l'issue et le motif sont
  enregistrés, une entrée `update` (`operation = "resolution"`, motif, `before` / `after`) est
  écrite, et l'anomalie symétrique sur A2 reste non résolue ; la fiche de A1 indique
  « Anomalie correspondante sur la candidature liée : non résolue ».
- **CA45** — Étant donné le même formulaire, quand le motif fait moins de 10 caractères après
  suppression des espaces, plus de 2 000, ou que l'issue est absente, alors rien n'est enregistré,
  le formulaire est réaffiché avec le message et le motif saisi, et aucune entrée d'audit n'est
  écrite ; l'issue `cause_disparue` sur une anomalie non marquée est refusée (400).
- **CA46** — Étant donné une anomalie résolue entre l'affichage et l'envoi par un autre
  utilisateur, quand la résolution est envoyée, alors la réponse est 409 avec le message de
  la règle 25 et la première résolution est conservée.
- **CA47** — Étant donné une anomalie résolue, quand un utilisateur `admin` la rouvre avec un
  motif valide, alors elle redevient non résolue, ses champs de résolution sont vidés et une
  entrée `update` (`operation = "reouverture"`) est écrite.
- **CA48** — Étant donné la résolution de l'anomalie haute de A1, alors le statut de A1 et ses
  décisions sont inchangés ; une décision « convoquée » ultérieure ne demande plus
  l'avertissement `anomalie_haute` si A1 n'a plus d'autre anomalie haute non résolue.
- **CA49** — Étant donné l'identifiant d'une anomalie de A2 envoyé sur l'URL de résolution de A1,
  alors la réponse est 404, rien n'est modifié, et une entrée `update` avec `metadata.refus =
  "anomalie_hors_candidature"` est écrite.

**Accès et sécurité**

- **CA50** — Étant donné un utilisateur `consultant` ou `client`, quand il envoie la relance, la
  résolution ou la réouverture (avec ou sans `HX-Request`), alors la réponse est 403, rien n'est
  modifié, et une entrée `access_denied` persiste avec `metadata.candidature_id` et
  `metadata.chemin`.
- **CA51** — Étant donné un utilisateur non connecté, alors ces `POST` redirigent vers la
  connexion ; sans TOTP actif, vers l'activation du TOTP ; sans jeton CSRF, 403 ; en `GET`, 405.
- **CA52** — Étant donné l'admin Django, quand un `admin` ouvre une anomalie, alors aucun champ
  n'est modifiable et les actions d'ajout et de suppression sont absentes ; un `POST` de
  modification est refusé.

**Audit et données personnelles**

- **CA53** — Étant donné l'exécution de CA1, alors chaque anomalie créée a exactement une entrée
  `create` (acteur « système (détection d'anomalies) », `metadata` avec `candidature_id`,
  `candidature_liee_id`, `run_id`, `declencheur`, `detecteur_version`), et une entrée
  `detection` porte les compteurs par type et gravité ; aucune de ces entrées ne contient de nom,
  prénom, email, téléphone, CIN ou date de naissance du jeu de référence (JSON sérialisé).
- **CA54** — Étant donné l'historique du dossier A1 filtré par `metadata.candidature_id` (KAN-14),
  alors les entrées de création, de cause disparue et de résolution de ses anomalies y figurent.

**Interface**

- **CA55** — Étant donné la fiche de A1 après détection, alors chaque anomalie affiche le libellé
  du code, sa source (ex. « Données déclarées », « Compte rapproché par : téléphone,
  identité »), la mention « Détection effectuée le … » ; une candidature jamais examinée affiche
  « Détection non encore effectuée ».
- **CA56** — Étant donné la fiche rendue, alors les formulaires de résolution contiennent un
  `fieldset` / `legend` pour l'issue, un compteur de caractères du motif, un seul `.btn-primary`
  par formulaire, aucun attribut `style` ni script en ligne, aucune classe `ml-`, `mr-`, `pl-`,
  `pr-`, `text-left`, `text-right` ; avec la langue `ar`, `<html dir="rtl">` et page 200.
- **CA57** — Étant donné un motif de résolution contenant `<script>alert(1)</script>`, alors il
  est échappé sur la fiche.

**Performance**

- **CA58** — Étant donné 100 puis 2 000 candidatures fictives (avec doublons, pièces et données
  déclarées) et `DETECTION_LOT_TAILLE = 500`, quand la détection en masse est exécutée, alors le
  nombre de requêtes SQL par lot est identique (à une constante près fixée par le test).
- **CA59** — Étant donné 10 000 candidatures fictives sur l'environnement de développement, quand
  `detecter_anomalies --tout` est exécutée, alors elle se termine en moins de 5 minutes et un
  second passage sans changement en moins de 2 minutes, sans créer d'anomalie (vérification
  explicite, non bloquante en CI).
- **CA60** — Étant donné une erreur simulée sur un lot de traitement, alors les autres lots sont
  traités et validés, et le rapport ainsi que l'entrée `detection` listent les candidatures du
  lot en erreur technique.

## Cas limites et erreurs

- **Listes vides** : aucune candidature dans le périmètre de la commande → rapport à zéro, entrée
  `detection` écrite, pas d'erreur. Candidature sans pièce ni données déclarées → seules les
  anomalies `piece_manquante` (selon configuration).
- **Données manquantes** : clés de rapprochement vides (pas d'email, pas de téléphone, pas de date
  de naissance) → aucun rapprochement sur ces clés ; téléphone de moins de 9 chiffres ignoré ;
  `sha256` vide (empreintes non calculées, `compute_hashes=False`) → pas de contrôle de pièce en
  double ; cible sans date de référence → `experience_avant_diplome` non exécuté (règle 21) ;
  `donnees_declarees` mal formé (`formation` non liste, entrée sans clé) → entrée ignorée, aucune
  erreur 500.
- **Données importées incohérentes** : valeur de `Piece.type` inconnue → traitée comme `autre`.
- **Translittérations** (« El Amrani » / « Al Amrani », « Bennani » / « Benani ») : produisent une
  anomalie `incoherence_nom` de gravité moyenne (mots partiellement communs) ou haute ; la
  résolution « faux positif » motivée évite qu'elle revienne (règle 18). Normalisation renforcée :
  Q18.
- **Doublon dont un dossier est purgé** (KAN-31) : `candidature_liee` devient nulle → la fiche
  affiche « candidature liée indisponible » et la prochaine exécution marque « cause disparue ».
- **Correction d'une clé à la source** (email modifié) : à la synchronisation suivante, la paire
  n'est plus rapprochée → « cause disparue » ; un nouveau rapprochement crée une nouvelle clé.
- **Doublon impliquant trois comptes** : une anomalie par paire de candidatures (3 comptes à une
  candidature chacun → 2 anomalies par candidature).
- **Volumes importants** : règle 33, CA58 à CA60 ; valeur partagée par de nombreux comptes
  (règle 8) bornée à une anomalie par candidature.
- **Accès concurrent** :
  - détection et décision simultanées : l'avertissement est évalué au moment de la décision
    (sous verrou de la candidature, KAN-13) ; une anomalie haute apparue entre récapitulatif et
    exécution d'un lot produit l'échec « nouvel avertissement non confirmé » (KAN-15, CA30) ;
  - deux exécutions de la détection : verrou consultatif et unicité (CA32) ;
  - résolution pendant une détection : la détection n'écrit jamais `resolue` ; si elle marque
    « cause disparue » une anomalie résolue entre-temps, la marque est sans effet sur la
    résolution (la ligne est relue sous verrou avant écriture) ;
  - deux résolutions simultanées : CA46.
- **Broker Celery indisponible** après synchronisation : règle 22, CA40. **Tâche relivrée**
  (`acks_late`) : idempotente (règle 18), seule l'entrée `detection` est dupliquée avec le même
  `run_id`, ce qui est acceptable et visible.
- **Droits insuffisants** : CA50 à CA52 ; fragments HTMX contrôlés comme les pages.

## Traçabilité et conformité

- **Journal d'audit** : création, marque « cause disparue » et retrait, résolution, réouverture
  (avec motif), exécution de la détection (compteurs, déclencheur, acteur), configuration des
  pièces obligatoires, refus de droits et envois forgés (règle 34). Toutes les entrées liées à un
  dossier portent `metadata.candidature_id` et s'affichent dans son historique (KAN-14). Le
  journal reste en ajout seul ; l'anomalie elle-même n'est jamais supprimée par un utilisateur.
- **Données personnelles (loi 09-08)** : la détection traite des données déjà détenues pour la
  finalité de recrutement (vérification de l'intégrité des candidatures) ; elle ne collecte
  rien de nouveau. Minimisation : aucune valeur d'identification dans les anomalies, leurs clés
  et le journal (règle 4, CA11, CA53) ; la fiche n'expose de la candidature liée que NOM Prénom,
  la cible et le lien (CA12). Les anomalies suivent la durée de conservation et la purge de leur
  candidature (cascade, KAN-31) ; le droit d'accès d'un candidat inclut ses anomalies. Le
  rapprochement de deux comptes d'une même personne entre plusieurs clients (SRM, entreprises)
  est un traitement à mentionner dans le registre CNDP (Q17).
- **IA** : aucun appel IA dans cette story. Les contrôles « pièce ↔ candidat » et « déclaré ↔
  extrait » réutilisent les sorties de KAN-41, dont la traçabilité (modèle, prompt, entrée
  pseudonymisée, sortie) relève de KAN-41 ; chaque anomalie qui en découle cite l'extraction
  utilisée, son modèle, sa version de prompt, la confiance et l'extrait (explicabilité). Un champ
  extrait peu fiable ne produit rien (seuil de confiance).
- **Humain dans la boucle** : aucune élimination automatique, aucun changement de statut,
  aucune décision ; toute résolution est humaine, motivée et tracée ; la disparition d'une cause
  est signalée mais jamais conclue automatiquement.
- **Biais** : aucun contrôle sur le sexe, l'âge, la région, la ville, la photo ou la situation
  familiale ; la date de naissance ne sert qu'à la clé d'identité, sans calcul d'âge (règle 5) ;
  le téléphone seul (partagé dans un foyer) ne donne qu'une information (gravité info).

## Hors périmètre

- Écran dédié « Anomalies d'un concours », filtre des listes par type d'anomalie, résolution en
  lot, ajout de la détection aux actions en lot de KAN-15.
- Fusion de comptes candidats ou rattachement automatique de deux dossiers.
- Rapprochement approché (distance d'édition, phonétique, translittération arabe / latin) et
  recherche sémantique des doublons dans le vivier : vague 2.
- Contrôles de cohérence de la date de naissance ou de
  l'âge (exclus par principe).
- Nouveaux appels IA, extraction des pièces, schémas d'extraction (KAN-41).
- Écran d'édition des pièces obligatoires hors admin Django.
- Correction de la synchronisation qui réécrit `sha256` (ticket issu de KAN-12 Q12).
- Notification du candidat, du client ou du site ; l'application n'écrit jamais dans la base du
  site.
- Tableau de bord de contrôle des écarts par sexe et région (vague 2).

## Questions ouvertes

Propositions par défaut cochées à la demande du Product Owner (« avancer », 2026-11-27) ; il peut
revenir sur chacune. **[HDB]** = touche l'humain dans la boucle.

- [x] **Q1 — Nouveaux types `plusieurs_postes_concours` et `ecart_declaration`.** *Proposition
  par défaut* : oui (migration de choix sans impact sur les données), pour filtrer et compter la
  violation de la règle « un poste par concours » et les écarts avec les pièces. *Alternative* :
  réutiliser `doublon_candidat` et `autre` avec un code. *Conséquence* : moins de migrations,
  mais types peu parlants sur la fiche et à l'export.
- [x] **Q2 [HDB] — Gravités** (tableau de la règle 6), notamment **haute** pour : plusieurs postes
  d'un même concours, même offre, pièce obligatoire manquante, diplôme ou attestation identique à
  celle d'un autre compte, titulaire sans mot commun, niveau lu sous le minimum, période attestée
  sous le minimum. *Proposition par défaut* : ce tableau. *Conséquence* : ces cas déclenchent
  l'avertissement avant présélection, convocation ou retenue. *Alternative* : pièce manquante en
  moyenne (si les clients tolèrent les compléments de dossier).
- [x] **Q3 — Téléphone seul.** *Proposition par défaut* : rapprochement, mais gravité info (un
  numéro peut être partagé dans un foyer ou un cybercafé). *Alternative* : ignorer le téléphone
  seul. **Q3 bis — CIN comme clé** : *proposition par défaut* : non dans cette story (le brief
  retient email, téléphone, nom + date de naissance) ; ticket à créer si le site fournit des CIN
  fiables. *Conséquence* : un doublon de même CIN mais d'email, téléphone et date de naissance
  différents n'est pas détecté. **Révisée le 2026-11-27** : CIN présente sur le site, cf.
  docs/site/modeles-admin.md (profil candidat : CIN, email, téléphone, nom complet, date de
  naissance). La CIN normalisée devient une clé de rapprochement (`doublon_candidat` / `meme_cin`,
  gravité haute ; même concours ou même offre : règle 6 inchangée) ; elle n'apparaît ni dans les
  descriptions, ni dans les détails, ni dans les clés, ni dans le journal (seul le critère `cin`
  est cité).
- [x] **Q4 — Seuil de valeur partagée** : 5 comptes (`DOUBLONS_SEUIL_GROUPE`). *Conséquence* :
  évite l'explosion de paires sur un numéro de standard ; un vrai groupe de plus de 5 comptes
  n'est signalé qu'en information.
- [x] **Q5 — Pièces obligatoires** : champ sur concours, poste et offre, défauts concours = CV +
  diplôme, offre = CV, spontanée = CV ; attestation de travail non obligatoire par défaut même si
  une expérience minimale est exigée ; édition dans l'admin Django par `admin` seulement.
  *Alternative* : attestation obligatoire dès qu'une expérience minimale est exigée.
  *Conséquence de l'alternative* : beaucoup plus d'anomalies hautes sur les dossiers des
  fixtures et des concours réels où l'attestation est demandée plus tard.
- [x] **Q6 [HDB] — Cause disparue.** *Proposition par défaut* : jamais de résolution automatique ;
  marque « cause disparue » à confirmer par un humain ; l'anomalie compte toujours comme non
  résolue (avertissement compris). *Alternative* : ne plus la compter dans l'avertissement.
  *Conséquence* : plus d'avertissements à confirmer, mais aucune anomalie ne s'éteint sans
  regard humain.
- [x] **Q7 [HDB] — Anomalies symétriques** : résolution indépendante sur chaque dossier, avec
  l'état de l'autre affiché. *Alternative* : résoudre les deux ensemble (un motif).
  *Conséquence* : saisie doublée pour un même constat, mais chaque dossier a son motif en
  recours.
- [x] **Q8 — Issues de résolution et réouverture** : trois issues (confirmée et traitée, pas une
  anomalie, cause disparue confirmée), motif 10 à 2 000 caractères, réouverture motivée permise.
  *Alternative* : motif libre seul, sans issue. *Conséquence* : sans issue, impossible de mesurer
  le taux de faux positifs du détecteur.
- [x] **Q9 — Concours terminé** : la détection continue (information utile en recours), la
  résolution reste possible. *Alternative* : figer les anomalies des concours terminés (cohérent
  avec KAN-15 Q7). *Conséquence* : un doublon découvert après la clôture ne serait pas signalé.
- [x] **Q10 [HDB] — Expérience antérieure au diplôme** : signalée en gravité moyenne seulement
  quand elle fait passer sous le minimum ; le verdict du moteur n'est pas modifié. À confirmer
  avec les clients publics : l'expérience exigée doit-elle être postérieure au diplôme ? Si oui,
  ticket pour en faire un critère du moteur (avec « à vérifier » en cas de doute).
- [x] **Q11 — Seuil de confiance de l'extraction** : 0,7 (`EXTRACTION_CONFIANCE_MIN`) ; en dessous,
  aucune anomalie. *Alternative* : anomalie info « lecture incertaine ». *Conséquence* : moins de
  bruit, mais une pièce mal lue n'est signalée par aucun contrôle de cette story (la fiche KAN-41
  affiche la confiance).
- [x] **Q12 — Nouvelle valeur `AuditAction.DETECTION`** (« Détection d'anomalies »).
  *Alternative* : `update` avec `metadata.operation`. *Conséquence* : filtrage direct des
  exécutions dans le journal.
- [x] **Q13 — Admin Django des anomalies en lecture seule.** *Proposition par défaut* : oui.
  *Conséquence* : une anomalie de fixture erronée ne se corrige plus que par résolution motivée.
- [x] **Q14 — Détection après synchronisation en tâche Celery**, pas dans la transaction de
  synchronisation. *Conséquence* : la synchronisation reste rapide et ne peut pas échouer à cause
  de la détection ; quelques secondes de décalage avant l'apparition des anomalies.
- [x] **Q15 — Fin d'expérience postérieure à la candidature** : pas d'anomalie (CDD en cours).
- [x] **Q16 — Chevauchement** : seuil 180 jours, gravité info. *Alternative* : moyenne.
  *Conséquence* : le cumul d'emplois à temps partiel est fréquent ; une gravité plus haute
  produirait beaucoup de faux positifs.
- [x] **Q17 — Registre CNDP** : mentionner le rapprochement de comptes entre clients dans la fiche
  de traitement, à valider avec le DPO avant données réelles. *Proposition par défaut* : la
  détection porte sur toute la base (tous clients confondus), l'affichage ne révélant que NOM
  Prénom et la cible de la candidature liée. *Alternative* : limiter les rapprochements à un même
  client (organisation). *Conséquence de l'alternative* : un candidat à deux concours de deux SRM
  sous deux comptes ne serait pas signalé (sans enjeu pour la règle « un poste par concours »).
- [x] **Q18 — Translittérations** : pas de normalisation renforcée dans cette story ; gravité
  modulée par les mots communs et résolution « faux positif ». *Alternative* : table de variantes
  (El/Al, lettres doublées). *Conséquence* : quelques faux positifs à résoudre à la main.
