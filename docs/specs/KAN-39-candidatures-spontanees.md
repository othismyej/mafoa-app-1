# KAN-39 — Candidatures spontanées (vivier) : modèle, synchronisation et écran

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-39
- **Epic** : à confirmer dans Jira (E2 — écrans ; volet synchronisation rattaché à l'intégration site, E7)
- **Statut** : validée le 2026-11-23 sous réserve du code source du site (questions QS1 à QS7 en attente du code du site ; réponses par défaut retenues pour toutes les autres)

## Contexte

Le Product Owner confirme que le site mafoamanagement.com reçoit des **candidatures
spontanées** : un candidat dépose un CV (et souvent une lettre) sans viser ni un poste de
concours ni une offre d'emploi. Ces dossiers alimentent le **vivier** de Mafoa. Ils ne sont
aujourd'hui ni modélisés ni visibles dans l'application, alors qu'ils contiennent des données
personnelles soumises à la loi 09-08 (finalité, durée de conservation) et que la direction doit
pouvoir les parcourir et les qualifier.

Le PO demande de tout livrer maintenant. Le code source du site (structure réelle des
candidatures spontanées) arrivera plus tard : cette story définit un modèle et un contrat
`SiteSource` **robustes, à confirmer** (QS1 à QS7), et enrichit `FixtureSource` pour développer
et tester sans donnée réelle.

Le brief place la « recherche sémantique dans le vivier » en **vague 2** (`CLAUDE.md` §2). Cette
story ne livre que la base MVP : stockage, synchronisation, liste filtrable par critères
structurés, fiche, décision humaine simple. Aucune IA.

Existant dans le code (à réutiliser, pas à re-spécifier) :

- `apps/recruitment/models.py` :
  - `Candidature` : `candidat`, `poste_concours` (FK nullable), `offre` (FK nullable), `concours`
    (dénormalisé depuis le poste dans `save()`, non éditable), `date_candidature`, `statut`
    (9 valeurs), `donnees_declarees` (JSON), `source_id` / `source_synced_at`.
    Contraintes : `candidature_poste_xor_offre` (**exactement** un poste ou une offre : interdit
    une candidature sans cible), `uniq_candidature_un_poste_par_concours`,
    `uniq_candidature_par_offre`, `uniq_candidature_source`. La propriété `cible` renvoie
    `poste_concours or offre` ; `__str__` affiche `« candidat → cible »`.
  - `Candidat` (identité, contacts, clés normalisées `email_normalise`, `telephone_normalise`,
    `nom_normalise`, `cle_identite` ; `sexe` et `region` réservés au contrôle des biais).
  - `Piece` (FK `candidature`, type CV / diplôme / attestation / lettre / autre, `storage_key`,
    `sha256`, `taille`).
  - `Fonction` (11 fonctions du site), `ModeTravail`.
- `integrations/site_source/` :
  - `dto.py` : `CandidatureDTO(source_id, candidat_source_id, date_candidature,
    donnees_declarees, poste_source_id=None, offre_source_id=None)` — aucune notion de type ;
  - `base.py` : `SiteSource` (lecture seule), `UnavailableSource`, `get_source` ;
  - `sync.py` : `sync_from_source` (import idempotent par `source_id`, une transaction,
    empreinte SHA-256 des pièces, une entrée d'audit `sync`). **Constat** : la cible est résolue
    par `postes.get(poste_source_id)` / `offres.get(offre_source_id)`, qui renvoie `None` pour un
    identifiant inconnu ; aujourd'hui la contrainte XOR fait alors échouer la synchronisation. Si
    le type de candidature était simplement déduit de l'absence de cible, une candidature à un
    poste inconnu deviendrait silencieusement « spontanée » (règle 3) ;
  - `fixture_source.py` : 50 candidats + 2 doublons, une candidature chacun (poste ou offre),
    PDF générés de façon déterministe ; `tests/test_site_source.py` vérifie notamment
    `len(list_candidatures()) == len(list_candidats())` ;
  - `management/commands/sync_site.py` : option `--evaluate` qui appelle `run_eligibility` sur
    **toutes** les candidatures (**constat** : échouerait sur une candidature sans cible) ;
  - `webhooks.py` : non implémenté (cible).
- `apps/evaluation/` :
  - `services.run_eligibility` : évalue `candidature.cible` (critères du poste ou de l'offre) ;
  - `services.record_decision` et `decisions.py` : table `TRANSITIONS` (KAN-13), sous-ensemble
    lot (KAN-15), motif 10 à 2 000 caractères, révision, contrôle de concurrence, audit ;
  - `Anomalie` (types dont `doublon_candidat`, `piece_manquante`) : **aucun service de
    détection** des doublons ou pièces manquantes n'existe encore (cas d'usage 2 du MVP, story
    dédiée).
- `apps/recruitment/views.py` : `candidature_detail` (fiche KAN-11, journalisée KAN-14),
  `decision_enregistrer` (KAN-13), `piece_visionneuse` / `piece_contenu` (KAN-12),
  `poste_candidatures` + `filters.CandidatureFilter` (KAN-10, liste par poste).
- `apps/audit/services.py` : `log`, `log_consultation` (`VUES_CONSULTATION`),
  `role_required_logged`, `log_access_denied`.
- `apps/accounts/views.dashboard` : compte `Candidature.objects.count()` toutes cibles
  confondues.
- Charte : `docs/design/charte.md` (badges de statut, `.table-data`, `.chip`, RTL).

Coordination : **KAN-38** (en parallèle) livre l'écran des offres d'emploi et clarifie le tableau
de bord, dont la **tuile « Candidatures spontanées »**. KAN-39 fournit les données, le sélecteur
de comptage (règle 41) et l'écran cible de la tuile ; KAN-38 affiche la tuile.

## User story

En tant que membre de la direction de Mafoa, je veux retrouver dans l'application les
candidatures spontanées reçues sur le site, les filtrer par fonction, secteur et lieu souhaités,
consulter leurs pièces et les qualifier (conserver au vivier ou rejeter, avec un motif), afin
d'exploiter ce vivier pour de futurs recrutements dans le respect de la loi 09-08.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Liste des candidatures spontanées, fiche, visionneuse des pièces, décisions (table de la règle 26). |
| direction | Idem admin. |
| consultant, client (vague 2, non attribuables) | Refusé : 403 sur la liste, ses fragments HTMX, la fiche, les pièces et l'envoi de décision ; chaque refus est tracé en `access_denied` (KAN-14 règle 8). |
| utilisateur inactif | Refusé (pas de session). |
| non connecté | Redirection vers la connexion avec `next` ; rien n'est journalisé. |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP. |
| processus automatique (synchronisation, moteur, tâches) | Crée et met à jour les données importées ; **jamais** de décision, jamais de changement de statut d'une candidature spontanée. |

Contrôle de rôle côté serveur sur chaque vue **et** chaque fragment HTMX.

## Règles métier

### A. Modèle de données

1. **Type de candidature explicite.** `Candidature` reçoit un champ `type` (nom technique
   indicatif) à trois valeurs : `concours` (« Concours »), `offre` (« Offre d'emploi »),
   `spontanee` (« Candidature spontanée »). Il est indexé et non modifiable après création
   (règle 14).
2. **Contrainte remplaçante.** La contrainte `candidature_poste_xor_offre` est remplacée par une
   contrainte de base `candidature_type_cible` :
   - `type = concours` ⇔ `poste_concours` renseigné et `offre` vide ;
   - `type = offre` ⇔ `offre` renseignée et `poste_concours` vide ;
   - `type = spontanee` ⇔ `poste_concours` **et** `offre` vides (et donc `concours` vide).
   Toute autre combinaison (y compris un `type` vide) est refusée par la base. Les contraintes
   d'unicité existantes (un poste par concours, une candidature par offre, `source_id`) sont
   conservées telles quelles.
3. **Pas de déduction silencieuse du type spontané.** Par commodité, `Candidature.save()` peut
   renseigner un `type` vide à `concours` si un poste est fourni, ou à `offre` si une offre est
   fournie. Il ne renseigne **jamais** `spontanee` de lui-même : une candidature sans cible et
   sans type explicite est refusée (contrainte de la règle 2). Ainsi une cible inconnue ou
   perdue ne transforme jamais une candidature de concours en candidature spontanée.
4. **Statut dédié « Conservée au vivier ».** Une valeur `vivier` (libellé « Conservée au
   vivier ») est ajoutée à `Candidature.Statut`. Badge `badge-vivier` à ajouter à la charte
   (teinte neutre-positive, distincte des statuts de concours ; contraste AA vérifié).
5. **Statuts permis selon le type** (contrainte de base `candidature_statut_selon_type`) :
   - `spontanee` : `recue`, `en_analyse`, `vivier`, `rejetee` uniquement ;
   - `concours` et `offre` : les 9 statuts existants, **jamais** `vivier`.
   Les statuts d'éligibilité et de sélection (éligible, non éligible, à vérifier,
   présélectionnée, convoquée, retenue) n'ont pas de sens sans poste ni offre.
6. **Statut initial** d'une candidature spontanée : `recue`. Aucun processus automatique ne le
   change (pas de moteur, règle 20).
7. **Souhaits du candidat.** Les champs propres aux candidatures spontanées sont stockés dans un
   objet lié un-à-un à la candidature (nom indicatif `DetailsSpontanee`), créé uniquement pour
   `type = spontanee`, afin de ne pas alourdir `Candidature` et de pouvoir l'ajuster quand la
   structure du site sera connue (QS2). Champs proposés, tous facultatifs :
   - `fonction_souhaitee` : une valeur de `Fonction` (vide si absente ou inconnue) ;
   - `secteur_souhaite` : texte (≤ 150 caractères), libellé du site ;
   - `lieu_souhaite` : texte (≤ 100 caractères) : **lieu de travail souhaité** déclaré par le
     candidat (préférence de mobilité, distincte de `Candidat.ville` et `Candidat.region`) ;
   - `disponibilite` : texte libre (≤ 100 caractères), tel que saisi sur le site ;
   - `message` : texte libre du candidat (≤ 5 000 caractères, tronqué au-delà avec mention) ;
   - `consentement_vivier` : vrai / faux / inconnu (nul) ; `consentement_le` : date-heure ou nul
     (QS4) ;
   - `fonction_souhaitee_brute` : libellé d'origine si la fonction du site ne correspond à
     aucune valeur de `Fonction` (conservé pour affichage, non filtrable).
   Aucun champ de souhait ne porte sur le sexe, l'âge, la situation familiale ou la photo ; un
   tel champ fourni par le site n'est pas importé.
8. **Plusieurs candidatures spontanées par candidat.** L'application n'impose **aucune**
   contrainte d'unicité sur (candidat, type spontané) : le site est la source de vérité et
   refuser un dossier ferait échouer la synchronisation. Un candidat ayant plus d'une
   candidature spontanée non rejetée est signalé dans la liste (indicateur « N dossiers
   spontanés ») et sur la fiche (règle 34). Le comportement exact du site (un dossier mis à jour
   ou plusieurs dépôts) est à confirmer (QS3).
9. **`cible` et libellés.** Pour une candidature spontanée, `cible` vaut `None` et le libellé
   textuel (`__str__`, administration Django, journaux qui l'utilisent) devient
   « NOM Prénom → Candidature spontanée » au lieu de « → None ». L'administration Django
   (`CandidatureAdmin`) ajoute le filtre « Type » et affiche « Candidature spontanée » dans la
   colonne cible.
10. **Migration sans perte.** Une migration :
    1. ajoute `type` ;
    2. le renseigne pour **toutes** les lignes existantes (`concours` si poste, `offre` si offre),
       en une mise à jour ensembliste ;
    3. remplace la contrainte XOR par les contraintes des règles 2 et 5 ;
    4. ajoute le statut `vivier` aux choix (`Candidature.statut`, `Decision.decision`) et crée la
       table des souhaits (règle 7) ;
    5. ajoute un index `(type, date_candidature)` pour la liste.
    Le nombre de candidatures, leurs statuts, décisions, vérifications, pièces et anomalies sont
    inchangés. La migration inverse est possible tant qu'aucune candidature spontanée n'existe ;
    sinon elle échoue avec un message explicite (pas de suppression implicite de données). Le
    journal d'audit n'est pas touché (ajout seul, trigger).

### B. Contrat `SiteSource` et synchronisation

11. **DTO.** `CandidatureDTO` reçoit :
    - `type` : `"concours"`, `"offre"` ou `"spontanee"` (fourni par la source) ;
    - `souhaits` : objet facultatif (nom indicatif `SouhaitsDTO`) portant les champs de la règle 7
      (`fonction_souhaitee`, `secteur_souhaite`, `lieu_souhaite`, `disponibilite`, `message`,
      `consentement_vivier`, `consentement_le`), tous facultatifs.
    Compatibilité : si `type` est absent, il est déduit **uniquement** de la présence d'un
    `poste_source_id` (→ concours) ou d'un `offre_source_id` (→ offre). Un DTO sans type et sans
    aucune cible est **refusé** (jamais classé spontané par défaut). Le contrat est documenté dans
    `dto.py` comme « à confirmer avec le code source du site » (QS1, QS2).
12. **Cohérence d'un DTO.** Sont refusés (non importés, comptés dans le rapport, règle 16) :
    - `type = spontanee` avec un `poste_source_id` ou un `offre_source_id` renseigné ;
    - `type = concours` sans `poste_source_id`, `type = offre` sans `offre_source_id` ;
    - `type` hors des trois valeurs ;
    - `souhaits` fournis pour une candidature qui n'est pas spontanée (souhaits ignorés, la
      candidature est importée, l'écart est compté).
13. **Cible inconnue.** Une candidature de concours ou d'offre dont le poste ou l'offre est
    inconnu de la synchronisation n'est **jamais** enregistrée comme spontanée. Le comportement
    actuel (échec de la synchronisation, transaction annulée) est conservé ; il n'est pas
    modifié par cette story.
14. **Type immuable.** Si une candidature déjà importée change de type ou de cible à la source
    (ex. le site rattache une candidature spontanée à une offre), la synchronisation **ne
    modifie pas** la candidature existante dans l'application (ni type, ni cible, ni statut, ni
    décisions) ; elle compte un « conflit de type » dans le rapport et dans l'entrée d'audit
    `sync` (identifiant interne et `source_id`, sans nom). Le traitement de ces conflits est
    humain (QS5). Les autres champs importés (date, données déclarées, souhaits, pièces) restent
    mis à jour.

    **14 bis (amendement relecture conformité, 2026-11-23).** Si le DTO d'une candidature
    **déjà importée** devient incohérent (règle 12), il est refusé (rien n'est modifié, compté
    dans `refusees`) **et** compté comme conflit (`conflits_type`, avec `motif =
    "dto_incoherent"`), pour qu'un humain le voie dans l'audit `sync`. Un DTO incohérent pour
    une candidature inconnue de l'application reste un simple refus.

    *Limite connue* : l'immuabilité du type et de la cible est garantie par la synchronisation
    et le service, **pas par la base** (un `update()` ORM ou SQL cohérent — type et cible changés
    ensemble — passe les contraintes). Un déclencheur en base fera l'objet d'un ticket dédié ;
    un test documente la limite actuelle.
15. **Souhaits.** À chaque synchronisation, l'objet de souhaits d'une candidature spontanée est
    créé ou mis à jour à partir du DTO (le site reste la source de vérité) ; `souhaits` absent
    = tous les champs vides. Une `fonction_souhaitee` hors des valeurs de `Fonction` est stockée
    vide, avec son libellé d'origine dans `fonction_souhaitee_brute`.
16. **Rapport et audit de synchronisation.** `SyncReport` distingue les candidatures créées et
    mises à jour **par type**, et compte les DTO refusés (règle 12) et les conflits de type
    (règle 14). L'entrée d'audit `sync` existante porte ces compteurs en métadonnées
    (`candidatures_par_type`, `refusees`, `conflits_type` avec les seuls identifiants). Les
    statuts propres à l'application ne sont jamais écrasés (comportement existant).
17. **Pièces.** Les pièces d'une candidature spontanée sont importées comme les autres
    (`PieceDTO.candidature_source_id`, empreinte SHA-256, lecture à la demande, aucune copie
    permanente). Types attendus : CV et lettre de motivation ; tout type existant est accepté.
18. **Implémentations.** `FixtureSource` implémente le nouveau contrat (règle 19).
    `DatabaseSource` et `ApiSource` restent indisponibles ; leur correspondance avec le site est
    à écrire quand le code du site sera fourni (QS1, QS6). Les webhooks (non implémentés)
    utiliseront le même DTO.
19. **Enrichissement de `FixtureSource`.** Ajout, de façon **déterministe** et **sans modifier**
    les candidats, candidatures et pièces existants (mêmes `source_id`, mêmes empreintes pour
    une même graine), d'un petit jeu de candidatures spontanées fictives :
    - 6 candidatures spontanées (`source_id` préfixés `SP-`), dont :
      - 4 de nouveaux candidats fictifs (emails `@example.com` / `@example.org`, téléphones
        `06 00 …`, CIN préfixées `ZZ`) ;
      - 1 d'un candidat ayant déjà une candidature à un poste de concours ;
      - 1 seconde candidature spontanée, plus ancienne, d'un des nouveaux candidats (règle 8) ;
    - souhaits variés : au moins deux fonctions différentes, un secteur, un lieu, une
      disponibilité ; une candidature **sans aucun souhait** ; une avec une fonction hors
      référentiel (ex. « Data scientist ») ; consentement vrai, faux et inconnu représentés ;
    - pièces : CV + lettre pour la plupart ; une candidature avec CV seul ; une sans CV (lettre
      seule) ; PDF générés avec la mention « DOCUMENT FICTIF », la lettre ayant pour objet
      « Candidature spontanée » (et non un poste) ;
    - les dates de candidature sont fixes (pas d'aléa supplémentaire qui décalerait la
      génération existante).
    Les tests existants de `FixtureSource` qui supposent une candidature par candidat sont
    adaptés (comptes par type), sans changer les données existantes.

### C. Moteur d'éligibilité

20. **Aucun verdict pour une candidature spontanée.** Elle n'a pas de critères : `run_eligibility`
    refuse explicitement une candidature spontanée (erreur dédiée, aucune
    `VerificationEligibilite` créée, aucun statut modifié, aucune entrée d'audit `update`).
    `sync_site --evaluate` ne sélectionne que les candidatures de concours et d'offre, et
    indique le nombre de candidatures spontanées ignorées. Les relances en lot (KAN-15) étant
    limitées à un poste, elles ne peuvent pas viser une candidature spontanée (CA).

### D. Écran « Candidatures spontanées » (liste)

21. **Accès.** URL dédiée (forme indicative `/candidatures-spontanees/`), `GET` uniquement
    (autre méthode : 405). Entrée « Candidatures spontanées » dans la navigation principale pour
    `admin` et `direction` (`aria-current` sur la page active). Cible de la tuile du tableau de
    bord de KAN-38. Fil d'Ariane « Tableau de bord › Candidatures spontanées ». Compteur
    « N candidatures affichées sur M » (M = total des candidatures spontanées).
22. **Colonnes** (une ligne par candidature spontanée) :
    - **Candidat** : « NOM Prénom », lien vers la fiche ; indicateur « N dossiers spontanés »
      si le candidat en a plusieurs non rejetés (règle 8) ;
    - **Date de candidature** (`JJ/MM/AAAA HH:MM`, fuseau de l'application, `tabular-nums`) ;
    - **Fonction souhaitée** (libellé de `Fonction`, sinon libellé brut en texte secondaire,
      sinon « — ») ;
    - **Secteur souhaité** ;
    - **Lieu souhaité** ;
    - **Pièces** : présence d'un CV (« CV » / « Sans CV ») et nombre total de pièces ;
    - **Statut** (badge + libellé) ;
    - **Anomalies** : nombre non résolues et gravité maximale (comme KAN-10).
    Aucune autre donnée personnelle : ni email, téléphone, CIN, date de naissance, âge,
    `Candidat.ville`, région, sexe, photo, ni le message libre.
23. **Filtres** (combinés en ET ; valeurs multiples d'un même filtre en OU) :
    - **Statut** : reçue, en analyse, conservée au vivier, rejetée (seuls statuts permis,
      règle 5) ;
    - **Fonction souhaitée** : choix multiple parmi les 11 valeurs de `Fonction` + « Non
      précisée » ;
    - **Secteur souhaité** : choix multiple parmi les valeurs distinctes présentes en base (liste
      calculée en base, triée, plafonnée à 100 valeurs) ;
    - **Lieu souhaité** : choix multiple parmi les valeurs distinctes présentes en base (mêmes
      règles) ;
    - **Période de candidature** : date de début et de fin incluses ;
    - **Pièces** : « toutes », « avec CV », « sans CV » ;
    - **Anomalies** : toutes / avec anomalie non résolue / sans (comme KAN-10).
    Aucun filtre ni tri sur sexe, âge, région, `Candidat.ville`, photo ou situation familiale.
24. **Recherche par nom** identique à KAN-10 (normalisation `nom_normalise`, mots en ET, à partir
    de 2 caractères utiles, 100 caractères au plus). Pas de recherche par email, téléphone, CIN ni
    dans le message libre (minimisation, et recherche plein texte = vague 2).
25. **Tri, pagination, état, performance** :
    - colonnes triables : candidat, date, fonction souhaitée, statut, anomalies ; tri par défaut
      **date de candidature décroissante** (les plus récentes d'abord) ; départage stable par
      identifiant ;
    - pagination serveur 50 par défaut, 25 / 50 / 100 ;
    - état complet dans l'URL (GET), valeurs invalides ignorées sans erreur, bouton
      « Réinitialiser » ;
    - HTMX : fragment « tableau + compteur + pagination » ; sans JavaScript, formulaire GET
      classique ;
    - filtrage, tri, comptage en base ; nombre de requêtes SQL constant, indépendant du volume et
      de la taille de page ;
    - l'interprétation des paramètres en ensemble de candidatures est une **logique serveur
      unique**, testable hors vue (comme `filters.CandidatureFilter`), réutilisable par une
      future action en lot ou un export.
    Pas d'actions en lot sur cet écran dans cette story.

### E. Fiche d'une candidature spontanée

26. **Fiche réutilisée.** La fiche KAN-11 (même URL `/candidatures/<id>/`, même vue, mêmes
    contrôles d'accès et même journalisation) s'adapte au type :
    - **en-tête** : nom, prénom, statut, date de candidature, mention « Candidature spontanée » à
      la place de la cible ; fil d'Ariane et lien de retour vers la liste des candidatures
      spontanées ;
    - **bloc « Souhaits du candidat »** : fonction (ou libellé brut), secteur, lieu de travail
      souhaité, disponibilité, message (échappé, retours à la ligne conservés), consentement au
      vivier (« Oui, le JJ/MM/AAAA », « Non », « Non renseigné ») ; champ vide : « Non
      renseigné » ;
    - **identité et contacts, données déclarées, pièces et visionneuse (KAN-12), anomalies,
      historique des décisions** : inchangés ;
    - **bloc éligibilité absent** (ni « Éligibilité non encore vérifiée », ni bandeau d'écart
      verdict / statut, ni emplacement de score) ; à la place, une mention : « Candidature sans
      poste ni offre : aucune vérification d'éligibilité. » ;
    - **conservation** : « Données conservées jusqu'au JJ/MM/AAAA » calculé selon la règle 36.
27. **Autres candidatures du candidat** (fiche spontanée uniquement) : liste des autres
    candidatures du même `Candidat` (type, cible ou « Candidature spontanée », date, statut, lien
    vers la fiche), au plus 20, les plus récentes d'abord, avec le total. Aucune autre donnée de
    ces dossiers n'est affichée ; ouvrir l'un d'eux est une consultation distincte (KAN-14).
28. **Performance** : nombre de requêtes SQL de la fiche spontanée borné, indépendant du nombre
    de pièces, d'anomalies, de décisions et d'autres candidatures.

### F. Décisions humaines

29. **Table des transitions d'une candidature spontanée** (appliquée **dans le service**
    `record_decision`, selon le type) :

    | Statut courant | Décisions permises |
    |---|---|
    | reçue, en analyse | conservée au vivier, rejetée |
    | conservée au vivier | rejetée |
    | rejetée | conservée au vivier *(révision, confirmation requise)* |

    Toute autre valeur (éligible, non éligible, présélectionnée, convoquée, retenue, reçue, en
    analyse, à vérifier) est refusée pour une candidature spontanée (`transition_interdite`,
    journalisée comme dans KAN-13). Inversement, `vivier` est refusé pour une candidature de
    concours ou d'offre. La table KAN-13 des candidatures de concours et d'offre est inchangée.
30. **Règles KAN-13 conservées** : motif obligatoire (10 à 2 000 caractères), acte humain
    (`POST` authentifié, CSRF), décision identique au statut refusée, contrôle de concurrence et
    verrou, atomicité décision + statut + audit, révision confirmée et tracée
    (`metadata.revision`, `decision_remplacee_id`), ajout seul. Les avertissements « anomalie
    haute » et « quota » ne s'appliquent pas (aucune décision de sélection). La règle « concours
    terminé » est sans objet.

    **30 bis (amendement relecture conformité, 2026-11-23). Consentement et décision
    « vivier ».** Pour une candidature spontanée, la décision « Conservée au vivier » déclenche
    un **avertissement non bloquant** (même mécanisme que KAN-13 : `ConfirmationRequise`,
    confirmation explicite `confirmer_avertissements`, case jamais pré-cochée) lorsque le
    consentement à la conservation (règle 7) est :
    - **refusé** (`False`) : « Le candidat a refusé la conservation de son dossier au vivier. »
      (code `consentement_refuse`) ;
    - **inconnu** (nul, ou objet de souhaits absent) : « Le consentement du candidat à la
      conservation au vivier n'est pas établi. » (code `consentement_inconnu`).

    Sans confirmation, rien n'est enregistré. Un consentement « oui » ne déclenche aucun
    avertissement ; la décision « Rejetée » n'en déclenche jamais. L'entrée d'audit `decision`
    d'une candidature spontanée porte l'état du consentement au moment de la décision
    (`metadata.consentement_vivier` : `oui`, `non` ou `inconnu`) et, le cas échéant, les
    avertissements confirmés (`metadata.avertissements_confirmes`). Libellés traduisibles.
31. **Pas de rattachement à une offre ou à un poste** dans cette story (vague 2) : aucune action
    ne transforme une candidature spontanée en candidature à une offre.
32. **Pas de décision en lot** sur les candidatures spontanées ; le point d'entrée des lots
    (KAN-15) refuse tout identifiant de candidature spontanée (déjà garanti par le contrôle
    « candidature hors poste »).

### G. Doublons et anomalies

33. Les candidatures spontanées sont des `Candidature` ordinaires pour les anomalies : la future
    détection des doublons et pièces manquantes (cas d'usage 2, story dédiée) **doit** les
    inclure (même `Candidat`, clés normalisées). Cette story ne livre **aucun** détecteur ; elle
    garantit l'affichage des anomalies existantes (liste, fiche, lien `candidature_liee` vers une
    candidature spontanée ou non).
34. Indicateurs fournis dès cette story (sans créer d'anomalie) : « N dossiers spontanés » (règle
    22) et la liste des autres candidatures du candidat (règle 27).

### H. Conservation (loi 09-08)

35. Les candidatures spontanées sont des données personnelles collectées pour la finalité
    « recrutement » ; elles ne sont utilisées pour aucune autre finalité (pas de prospection,
    pas de formation commerciale).
36. **Durée de conservation configurable** propre aux candidatures spontanées (paramètre, nom
    indicatif `RETENTION_CANDIDATURE_SPONTANEE_MOIS`, défaut **24 mois** à compter de la date de
    candidature, ou de la dernière mise à jour à la source si le site la fournit, QS3). La date
    de fin de conservation est calculée et affichée sur la fiche, avec le libellé « Durée de
    conservation : N mois à compter de la candidature ou de sa dernière mise à jour sur le site
    (paramètre). » La fin de conservation et le sélecteur « dépassée » utilisent la **même
    référence** : date locale (fuseau de l'application) du début de conservation + N mois ; une
    candidature est « dépassée » à partir du lendemain de sa date de fin. Le paramètre doit être
    un entier ≥ 1 : toute autre valeur est refusée au démarrage (contrôle système Django
    `recruitment.E001`) *(amendement relecture conformité, 2026-11-23)*. La **purge** effective, le
    droit d'accès et le droit de suppression relèvent de **KAN-31** ; cette story ne supprime
    rien mais fournit un sélecteur testable « candidatures spontanées dont la durée est
    dépassée ».
37. Le consentement au vivier (règle 7) est affiché tel que reçu ; un consentement « Non » ou
    « Non renseigné » n'empêche pas l'affichage dans le MVP (la candidature est une démarche
    active du candidat) mais est signalé dans la fiche. Les conséquences sur la purge sont
    traitées par KAN-31 (QS4).

### I. Audit

38. **Liste** : une entrée `view` par réponse servie (page complète ou fragment HTMX), sans
    objet, `object_repr = "Liste : candidatures spontanées"`, `metadata.vue =
    "spontanees_liste"` (nouvelle valeur de `VUES_CONSULTATION`), filtres, recherche, tri, page,
    taille, nombre de résultats (même politique que KAN-10 Q4 pour le terme de recherche).
    Refus 403 : entrée `access_denied` (`metadata.chemin`).
39. **Fiche, pièces, décisions** : inchangés (KAN-11, KAN-12, KAN-13, KAN-14) ; les entrées
    portent `candidature_id` comme pour toute candidature. L'entrée `decision` porte en plus
    `metadata.type_candidature = "spontanee"`.
40. **Synchronisation** : règle 16. **Migration** : aucune entrée (opération technique), mais la
    note de version mentionne le changement de contrainte.

### J. Tableau de bord (coordination KAN-38) et interface

41. **Sélecteur de comptage** fourni par KAN-39 (fonction de service, une requête agrégée) :
    total des candidatures spontanées, « à traiter » (reçue + en analyse), « conservées au
    vivier », « rejetées », reçues sur les 30 derniers jours. KAN-38 l'utilise pour la tuile et
    décide de la présentation et de l'éventuelle séparation du compteur global
    « candidatures » par type.
42. **Interface** : charte (`.table-data`, `.chip`, badges de statut dont `badge-vivier`,
    alertes non rouges) ; libellés via `{% translate %}` / `gettext` avec pluriels ; propriétés
    logiques uniquement (`ms-`/`me-`, `ps-`/`pe-`, `text-start`/`text-end`, `border-s`) ;
    `dir="rtl"` en arabe.
43. **Accessibilité** : tableau avec `<caption>` (éventuellement masquée visuellement) et en-têtes
    `scope="col"` ; en-têtes triables sous forme de liens avec `aria-sort` ; filtres dans des
    `fieldset`/`legend` ; zone de résultats HTMX annoncée (`aria-live="polite"`) ; focus visible ;
    le statut, la gravité et la présence de CV ne sont jamais portés par la seule couleur ;
    cibles ≥ 36 px.
44. **Liste des candidatures d'un poste (KAN-10)** : le filtre statut n'y propose pas « Conservée
    au vivier » (statut impossible pour un concours).

## Critères d'acceptation

Jeu de données de référence (fictif) :

| Réf. | Candidat (fictif) | Type | Date de candidature | Statut | Souhaits | Pièces | Anomalies non résolues |
|---|---|---|---|---|---|---|---|
| S1 | HAMDI Salma | spontanée | 2026-11-02 10:00 | reçue | ingénieur, « Eau et assainissement », Agadir, « Immédiate », consentement oui | CV, lettre | — |
| S2 | BERRADA Anas | spontanée | 2026-10-15 09:00 | conservée au vivier (décision D1) | technicien, « Électricité », Tanger | CV | — |
| S3 | TAZI Rim | spontanée | 2026-09-01 14:00 | rejetée | responsable, « Finance / Comptabilité », Casablanca | CV, lettre | — |
| S4 | EL IDRISSI Omar | spontanée | 2026-11-20 08:30 | reçue | aucun souhait, consentement inconnu | aucune | — |
| S5 | NACIRI Hajar | spontanée | 2026-11-10 16:00 | reçue | fonction brute « Data scientist », Agadir | lettre | 1 haute |
| C1 | NACIRI Hajar (même candidat que S5) | concours, poste P1 | 2026-10-01 11:00 | éligible | — | CV, diplôme | — |
| O1 | ALAOUI Karim | offre O1 | 2026-11-05 12:00 | reçue | — | CV | — |

S6 : seconde candidature spontanée de HAMDI Salma, 2026-06-01 09:00, statut reçue, fonction
souhaitée « chef de service » (aucun autre souhait), pièce : CV.
Utilisateurs : `admin1@mafoa.test` (admin), `dir1@mafoa.test` (direction), `cons1@mafoa.test`
(consultant).

**Modèle et migration**

- **CA1** — Étant donné une base contenant des candidatures de concours et d'offre créées avant la
  migration (avec décisions, vérifications, pièces et anomalies), quand la migration est
  appliquée, alors chaque candidature à un poste a `type = concours`, chaque candidature à une
  offre a `type = offre`, et le nombre de candidatures, décisions, vérifications, pièces et
  anomalies ainsi que tous les statuts sont identiques avant et après.
- **CA2** — Étant donné la base migrée, quand on tente d'enregistrer (ORM ou SQL) une candidature
  `type = spontanee` avec un poste, `type = concours` sans poste, `type = offre` avec un poste et
  une offre, ou une candidature sans poste, sans offre et sans type, alors la base refuse chaque
  enregistrement (erreur d'intégrité).
- **CA3** — Étant donné une candidature créée par l'ORM avec un poste et sans type, alors son type
  vaut `concours` ; avec une offre et sans type, `offre` ; sans cible et sans type, l'enregistrement
  est refusé (jamais `spontanee` par défaut).
- **CA4** — Étant donné une candidature spontanée, quand on tente de lui donner le statut
  `eligible`, `non_eligible`, `a_verifier`, `preselectionnee`, `convoquee` ou `retenue`, alors la
  base refuse ; étant donné une candidature de concours ou d'offre, quand on tente le statut
  `vivier`, alors la base refuse.
- **CA5** — Étant donné une base contenant une candidature spontanée, quand la migration inverse
  est lancée, alors elle échoue avec un message explicite et aucune donnée n'est supprimée ;
  étant donné une base sans candidature spontanée, alors la migration inverse réussit et
  rétablit la contrainte `candidature_poste_xor_offre`.
- **CA6** — Étant donné la candidature S1, alors `str(S1)` vaut « HAMDI Salma → Candidature
  spontanée » et `S1.concours` est vide ; dans l'administration Django, la liste des
  candidatures propose un filtre « Type » et affiche « Candidature spontanée » pour S1.

**Contrat `SiteSource` et synchronisation**

- **CA7** — Étant donné une source fictive renvoyant S1 avec `type = "spontanee"` et ses souhaits,
  quand la synchronisation est lancée deux fois, alors une seule candidature spontanée existe
  pour ce `source_id`, au statut `recue`, sans poste, offre ni concours, avec ses souhaits ; le
  second passage la compte en « mise à jour ».
- **CA8** — Étant donné un DTO `type = "spontanee"` portant un `poste_source_id`, un DTO sans type
  ni cible, et un DTO `type = "pirate"`, quand la synchronisation est lancée, alors aucune de ces
  candidatures n'est importée, les autres le sont, et le rapport ainsi que l'entrée d'audit
  `sync` indiquent 3 candidatures refusées (identifiants seulement, sans nom).
- **CA9** — Étant donné un DTO **sans** champ `type` et avec un `poste_source_id` connu, alors la
  candidature est importée avec `type = concours` (compatibilité).
- **CA10** — Étant donné un DTO `type = "concours"` dont le `poste_source_id` est inconnu, quand la
  synchronisation est lancée, alors aucune candidature spontanée n'est créée pour ce
  `source_id` (la synchronisation échoue comme aujourd'hui, transaction annulée).
- **CA11** — Étant donné S2 importée comme spontanée et décidée « conservée au vivier », quand la
  source la renvoie ensuite avec `type = "offre"` et une offre connue, alors S2 reste spontanée,
  sans offre, au statut « conservée au vivier », sa décision est inchangée, et le rapport ainsi
  que l'entrée `sync` comptent un conflit de type portant l'identifiant interne et le
  `source_id` de S2. *(Règle 14 bis)* Étant donné une spontanée déjà importée, quand la source
  la renvoie avec un DTO incohérent (ex. `type = "spontanee"` et un `poste_source_id`), alors
  elle est inchangée, le DTO est compté refusé **et** en conflit (`motif = "dto_incoherent"`)
  dans le rapport et l'entrée `sync` ; un DTO incohérent inconnu de l'application n'est qu'un
  refus. Un test documente que l'immuabilité du type n'est pas garantie par la base.
- **CA12** — Étant donné S1 synchronisée, quand la source modifie son secteur souhaité, alors le
  secteur est mis à jour à la synchronisation suivante et le statut de S1 est inchangé.
- **CA13** — Étant donné un DTO spontané dont la fonction souhaitée est « Data scientist », alors
  `fonction_souhaitee` est vide et `fonction_souhaitee_brute` vaut « Data scientist ».
- **CA14** — Étant donné un DTO de concours portant des souhaits, alors la candidature est
  importée sans objet de souhaits et l'écart est compté dans le rapport.
- **CA15** — Étant donné les pièces CV et lettre de S1, quand la synchronisation est lancée, alors
  deux `Piece` rattachées à S1 sont créées avec empreinte SHA-256 et taille, sans copie du
  fichier.
- **CA16** — Étant donné l'entrée d'audit `sync` d'une synchronisation de la source fictive,
  alors ses métadonnées contiennent les compteurs par type (`concours`, `offre`, `spontanee`),
  `refusees` et `conflits_type`, et aucun nom, email, téléphone ni CIN.

**`FixtureSource`**

- **CA17** — Étant donné `FixtureSource` avec la graine par défaut, alors `list_candidatures()`
  contient exactement 6 candidatures `type = "spontanee"` dont les `source_id` commencent par
  `SP-`, sans poste ni offre ; l'une concerne un candidat ayant aussi une candidature de
  concours, et un même candidat en a deux.
- **CA18** — Étant donné `FixtureSource` avant et après cette story, avec la même graine, alors les
  candidats, candidatures et pièces existants (non spontanés) ont les mêmes `source_id`, les
  mêmes valeurs et les mêmes empreintes de PDF.
- **CA19** — Étant donné les candidatures spontanées fictives, alors au moins une a CV et lettre,
  une a un CV seul, une n'a pas de CV, une n'a aucun souhait, une a une fonction hors
  référentiel, et les consentements vrai, faux et inconnu sont représentés ; tous les emails
  sont en `example.com` / `example.org`, les CIN commencent par `ZZ`, et chaque PDF contient
  « DOCUMENT FICTIF » ; la lettre contient « Candidature spontanée ».
- **CA20** — Étant donné deux instances de `FixtureSource` avec la même graine, alors leurs
  candidatures spontanées et les octets de leurs PDF sont identiques.

**Moteur d'éligibilité**

- **CA21** — Étant donné S1, quand `run_eligibility(S1)` est appelé, alors une erreur dédiée est
  levée, aucune `VerificationEligibilite` n'est créée, le statut reste « reçue » et aucune
  entrée d'audit `update` n'est écrite.
- **CA22** — Étant donné une base contenant S1 à S6, C1 et O1, quand `sync_site --evaluate` est
  lancé sur la source fictive, alors la commande se termine sans erreur, évalue seulement les
  candidatures de concours et d'offre, et affiche le nombre de candidatures spontanées ignorées.

**Liste : accès et affichage**

- **CA23** — Étant donné `dir1` connecté avec TOTP, quand il ouvre la liste sans paramètre, alors
  la réponse est 200, le compteur indique « 6 candidatures affichées sur 6 », les lignes sont
  dans l'ordre S4, S5, S1, S2, S3, S6 (date décroissante) et ni C1 ni O1 n'apparaissent.
- **CA24** — Étant donné `admin1`, alors la réponse est 200 ; étant donné `cons1`, alors la page
  complète et le fragment (`HX-Request: true`) répondent 403 sans donnée de candidat et une
  entrée `access_denied` persiste pour chaque tentative ; étant donné un utilisateur non
  connecté, alors il est redirigé vers la connexion avec `next` ; un `POST` sur la liste répond
  405.
- **CA25** — Étant donné la liste, alors la ligne S1 affiche « HAMDI Salma » (lien vers sa fiche),
  « 02/11/2026 10:00 », « Ingénieur », « Eau et assainissement », « Agadir », « CV » avec
  2 pièces, le statut « Reçue » et aucune anomalie ; la ligne S4 affiche « — » pour la fonction,
  le secteur et le lieu et « Sans CV » ; la ligne S5 affiche « Data scientist » en texte
  secondaire et 1 anomalie de gravité haute.
- **CA26** — Étant donné S1 et S6 (même candidate, toutes deux non rejetées), alors leurs deux
  lignes portent l'indicateur « 2 dossiers spontanés » ; la ligne S3 (rejetée) n'affiche pas
  d'indicateur.
- **CA27** — Étant donné le jeu de référence, quand le HTML de la liste est inspecté, alors il ne
  contient ni email, ni téléphone, ni CIN, ni date de naissance, ni `Candidat.ville`, ni région,
  ni sexe, ni le message libre des candidats.
- **CA28** — Étant donné la navigation principale de `dir1` et de `admin1`, alors elle contient le
  lien « Candidatures spontanées », marqué `aria-current="page"` sur la liste.

**Liste : filtres, recherche, tri, pagination**

- **CA29** — Étant donné le jeu de référence, quand le filtre statut vaut « reçue », alors S4, S5,
  S1 et S6 sont listées ; avec « conservée au vivier » OU « rejetée », S2 et S3. Les options du
  filtre statut sont exactement : Reçue, En analyse, Conservée au vivier, Rejetée.
- **CA30** — Étant donné le filtre fonction = « ingénieur » OU « technicien », alors S1 et S2 sont
  listées ; avec « Non précisée », S4 et S5.
- **CA31** — Étant donné le filtre lieu souhaité = « Agadir », alors S1 et S5 sont listées ; étant
  donné le filtre secteur = « Électricité », alors seule S2 l'est ; les options de ces deux
  filtres sont les valeurs distinctes présentes en base, triées.
- **CA32** — Étant donné la période du 01/11/2026 au 10/11/2026, alors S1 et S5 sont listées
  (bornes incluses, y compris 16:00 le 10/11).
- **CA33** — Étant donné le filtre pièces = « sans CV », alors S4 et S5 sont listées ; étant donné
  anomalies = « avec anomalie non résolue », alors seule S5.
- **CA34** — Étant donné la recherche « hamdi », alors S1 et S6 sont listées ; « salma HAMDI »
  donne le même résultat ; « h » (1 caractère) n'applique aucun filtre ; une recherche
  « 0600 » ou une partie d'email ne trouve rien.
- **CA35** — Étant donné le tri « candidat croissant », alors l'ordre suit `nom_normalise` ; en
  décroissant, l'ordre est exactement inverse ; étant donné `tri=sexe`, `statut=eligible`,
  `fonction=pirate`, `page=abc` et `taille=1000`, alors la réponse est 200, ces valeurs sont
  ignorées et au plus 100 lignes sont affichées.
- **CA36** — Étant donné 120 candidatures spontanées de même date, quand on parcourt les pages de
  50 au tri par défaut, alors chacune apparaît exactement une fois.
- **CA37** — Étant donné 10 puis 1 000 candidatures spontanées (avec souhaits, pièces et
  anomalies), quand une page de 50 puis de 100 lignes est affichée, alors le nombre de requêtes
  SQL est identique dans tous les cas et borné par une constante fixée dans le test.
- **CA38** — Étant donné une requête HTMX avec un filtre, alors la réponse est le fragment
  « tableau + compteur + pagination » sans `<html>` ; la même URL sans `HX-Request` renvoie la
  page complète avec les mêmes lignes ; le bouton « Réinitialiser » rétablit les 6 lignes dans
  l'ordre par défaut.
- **CA39** — Étant donné un jeu de paramètres, quand la logique serveur de filtrage est appelée
  directement (hors vue), alors elle renvoie exactement l'ensemble listé par la vue, toutes
  pages confondues.
- **CA40** — Étant donné une base sans candidature spontanée, alors la liste affiche « Aucune
  candidature spontanée pour le moment. » et le compteur « 0 sur 0 » ; étant donné des filtres
  sans résultat, alors « Aucune candidature ne correspond à ces critères. » et le bouton
  « Réinitialiser ».

**Fiche**

- **CA41** — Étant donné `dir1` sur la fiche de S1, alors la réponse est 200 ; l'en-tête affiche
  « Candidature spontanée » et le statut « Reçue » ; le bloc « Souhaits du candidat » affiche
  Ingénieur, Eau et assainissement, Agadir, Immédiate, le message et « Consentement au vivier :
  Oui, le JJ/MM/AAAA » ; le fil d'Ariane et le lien de retour mènent à la liste des
  candidatures spontanées.
- **CA42** — Étant donné la fiche de S1, alors elle ne contient ni la section « Éligibilité », ni
  « Éligibilité non encore vérifiée », ni bandeau d'écart, ni emplacement de score, et contient
  « Candidature sans poste ni offre : aucune vérification d'éligibilité. » ; la fiche de C1
  (concours) conserve sa section éligibilité (non-régression KAN-11).
- **CA43** — Étant donné la fiche de S4 (sans souhait, consentement inconnu, sans pièce), alors la
  page est 200 et affiche « Non renseigné » pour chaque souhait et pour le consentement, et
  « Aucune pièce reçue ».
- **CA44** — Étant donné la fiche de S1 et la pièce CV de S1, quand `dir1` ouvre la visionneuse
  puis le contenu de la pièce, alors la pièce est servie comme pour toute candidature (KAN-12) et
  une entrée `view` `vue = "piece"` avec `candidature_id` de S1 est créée.
- **CA45** — Étant donné la fiche de S5, alors la section « Autres candidatures de ce candidat »
  liste C1 (concours, intitulé du poste P1, date, statut « Éligible », lien vers sa fiche) ;
  étant donné la fiche de S1, elle liste S6 ; étant donné S4, elle indique « Aucune autre
  candidature ».
- **CA46** — Étant donné la fiche de S1, alors elle affiche « Données conservées jusqu'au
  02/11/2028 » (24 mois, paramètre par défaut) ; avec le paramètre fixé à 12 mois dans le test,
  « 02/11/2027 ». La fiche affiche « Durée de conservation : 24 mois à compter de la candidature
  ou de sa dernière mise à jour sur le site (paramètre). »
- **CA47** — Étant donné S1 avec 30 pièces, 30 anomalies, 20 décisions et 25 autres candidatures
  du même candidat, alors le nombre de requêtes SQL de la fiche est identique (à une constante
  près fixée par le test) à celui de S1 avec une pièce, une anomalie, une décision et une autre
  candidature ; la section « Autres candidatures » en affiche 20 et le total 25.
- **CA48** — Étant donné un message libre contenant `<script>alert(1)</script>`, alors il est
  échappé sur la fiche et n'apparaît pas dans la liste.

**Décisions**

- **CA64** *(règle 30 bis)* — Étant donné S1 dont le consentement est « Non » (resp. inconnu),
  quand `dir1` décide « Conservée au vivier » sans confirmation, alors une demande de
  confirmation est renvoyée avec l'avertissement `consentement_refuse` (resp.
  `consentement_inconnu`), et ni décision, ni changement de statut, ni entrée d'audit ne sont
  enregistrés ; avec confirmation, la décision est enregistrée et l'entrée `decision` porte
  `avertissements_confirmes` et `consentement_vivier = "non"` (resp. `"inconnu"`).
- **CA65** *(règle 30 bis)* — Étant donné S1 (consentement « Oui »), « Conservée au vivier » est
  enregistrée sans avertissement et l'audit porte `consentement_vivier = "oui"` ; étant donné S4
  (consentement inconnu), « Rejetée » est enregistrée sans avertissement.
- **CA66** *(règle 30 bis)* — Étant donné la fiche, l'envoi de « Conservée au vivier » sans
  consentement établi affiche le libellé de l'avertissement et une case de confirmation non
  cochée ; l'envoi avec la case cochée enregistre la décision.

- **CA49** — Étant donné S1 (reçue) et `dir1`, quand il décide « Conservée au vivier » avec un
  motif valide, alors une `Decision` est créée, le statut devient « Conservée au vivier » et une
  entrée `decision` est écrite avec avant / après statut, motif et
  `metadata.type_candidature = "spontanee"`.
- **CA50** — Étant donné la fiche de S1 (reçue), alors les décisions proposées sont exactement
  « Conservée au vivier » et « Rejetée » ; pour S2 (vivier), exactement « Rejetée » ; pour S3
  (rejetée), exactement « Conservée au vivier ».
- **CA51** — Pour chaque statut d'une candidature spontanée et chaque valeur de décision (test
  paramétré), étant donné un appel direct à `record_decision`, alors seuls les couples de la
  table de la règle 29 sont acceptés ; les autres lèvent une erreur de transition, rien n'est
  enregistré, et la vue répond 400 avec une entrée `update` `metadata.refus =
  "transition_interdite"`.
- **CA52** — Étant donné C1 (concours, éligible) ou O1 (offre), quand la décision « vivier » est
  envoyée, alors elle est refusée et rien n'est enregistré ; la table KAN-13 reste inchangée pour
  ces candidatures (tests KAN-13 existants verts).
- **CA53** — Étant donné S3 (rejetée par une décision D0), quand on décide « Conservée au vivier »
  sans confirmation de révision, alors c'est refusé avec la demande de confirmation ; avec
  confirmation et motif valide, une nouvelle décision est créée, D0 est inchangée et l'audit
  porte `revision = true` et `decision_remplacee_id = D0`.
- **CA54** — Étant donné S1, quand une décision est envoyée avec un motif de moins de 10
  caractères, par `cons1`, sans jeton CSRF, ou avec un statut attendu périmé, alors elle est
  refusée selon les règles KAN-13 (400, 403, 403, 409) et rien n'est enregistré.
- **CA55** — Étant donné S5 (anomalie haute non résolue), quand on décide « Conservée au vivier »,
  alors aucun avertissement n'est demandé et la décision est acceptée.
- **CA56** — Étant donné l'action en lot de KAN-15 sur le poste P1 avec, en mode `ids`,
  l'identifiant de S1, alors le lot est refusé comme pour toute candidature hors poste et S1 est
  inchangée.

**Audit, tableau de bord, conformité, interface**

- **CA57** — Étant donné `dir1` affichant la liste filtrée (statut « reçue », recherche
  « hamdi »), page complète puis fragment HTMX, alors deux entrées `view` sont créées sans objet,
  avec `object_repr = "Liste : candidatures spontanées"`, `metadata.vue = "spontanees_liste"`,
  les filtres, la recherche, le tri, la page, la taille et le nombre de résultats ; une réponse
  403, 404, 405 ou une redirection n'en crée aucune.
- **CA58** — Étant donné la fiche de S1 ouverte par `dir1`, alors une entrée `view` `vue = "fiche"`
  avec `candidature_id` de S1 est créée et ni `object_repr` ni `metadata` ne contiennent
  « HAMDI », « Salma », l'email, le téléphone ou la CIN.
- **CA59** — Étant donné le jeu de référence, quand le sélecteur de comptage du tableau de bord est
  appelé, alors il renvoie total = 6, à traiter = 4, conservées au vivier = 1, rejetées = 1, et
  le nombre de candidatures reçues sur les 30 derniers jours (date de référence fixée dans le
  test), en un nombre de requêtes SQL constant.
- **CA60** — Étant donné le jeu de référence et la date de référence du 15/12/2026, quand le
  sélecteur « durée de conservation dépassée » est appelé avec une durée de 3 mois, alors il
  renvoie S3 et S6 seulement (S2, du 15/10/2026, n'est pas concernée) ; il ne supprime ni ne modifie aucune donnée.
  En limite de jour (candidature à 00 h 30 heure locale, fin de mois), une candidature est
  « dépassée » à une date de référence si et seulement si sa date de fin de conservation
  affichée est antérieure à la date locale de référence. Une durée < 1 mois est refusée au
  démarrage (`recruitment.E001`).
- **CA61** — Étant donné la liste des candidatures du poste P1 (KAN-10), alors le filtre statut ne
  propose pas « Conservée au vivier ».
- **CA62** — Étant donné la langue active `ar`, quand la liste et la fiche de S1 sont affichées,
  alors `<html>` porte `dir="rtl"`, les réponses sont 200, et les gabarits ajoutés ou modifiés
  n'utilisent aucune classe directionnelle figée (`ml-`, `mr-`, `pl-`, `pr-`, `text-left`,
  `text-right`, `left-`, `right-`).
- **CA63** — Étant donné la liste, alors le tableau possède une légende (`<caption>`), des en-têtes
  `scope="col"`, la colonne triée porte `aria-sort`, chaque groupe de filtres est un `fieldset`
  avec `legend`, la zone de résultats porte `aria-live="polite"`, et le statut comme la présence
  de CV sont lisibles en texte (pas seulement par la couleur).

## Cas limites et erreurs

- **Aucune candidature spontanée** (site sans ce flux, ou avant la première synchronisation) :
  CA40 ; tuile du tableau de bord à 0 (KAN-38).
- **Souhaits absents, partiels ou inconnus** : « — » dans la liste, « Non renseigné » sur la fiche
  (CA25, CA43) ; fonction hors référentiel conservée en libellé brut (CA13).
- **Message libre très long** : tronqué à 5 000 caractères à l'import avec mention « (tronqué) »
  sur la fiche ; jamais affiché dans la liste.
- **Candidature spontanée sans aucune pièce** : importée et affichée (« Sans CV », « Aucune pièce
  reçue ») ; la détection d'une pièce manquante relève de la story des anomalies.
- **Même candidat : candidature de concours et spontanée** : deux dossiers distincts, liés par la
  section « Autres candidatures » (CA45) ; aucune décision de l'un n'affecte l'autre.
- **Plusieurs spontanées du même candidat** : aucun refus ; indicateur (CA26).
- **Changement de type à la source** : conflit compté, dossier inchangé (CA11).
- **DTO incohérent** : refusé et compté (CA8).
- **Candidat inconnu** (`candidat_source_id` absent de la source) : comportement actuel de la
  synchronisation (échec), inchangé.
- **Suppression ou retrait d'une candidature sur le site** : la synchronisation actuelle ne gère
  aucune suppression ; inchangé dans cette story (QS7, KAN-31).
- **Volumes importants** : plusieurs milliers de candidatures spontanées par an ; index
  `(type, date_candidature)`, filtres et comptages en base, requêtes constantes (CA37) ; listes
  de valeurs distinctes de secteur et de lieu plafonnées à 100.
- **Accès concurrent** : deux décisions simultanées sur la même candidature spontanée →
  contrôle de concurrence KAN-13 (CA54) ; une synchronisation pendant une décision ne modifie
  jamais le statut.
- **Paramètres d'URL invalides ou malveillants** : ignorés, valeurs réaffichées échappées, aucune
  concaténation SQL (CA35).
- **Droits insuffisants** : CA24, CA54 ; fragments HTMX contrôlés comme les pages.
- **Fiche d'une candidature spontanée ouverte depuis le journal d'audit ou une anomalie** : même
  URL, même contrôle, même journalisation.

## Traçabilité et conformité

- **Journal d'audit** :
  - consultation de la liste (`view`, `vue = spontanees_liste`, filtres et recherche) ; refus
    (`access_denied`) ;
  - consultation de la fiche et des pièces : inchangée (KAN-14, KAN-12), `candidature_id` ;
  - décisions (`decision`, avant / après statut, motif, révision, `type_candidature`) ; refus de
    transition (`update`, `metadata.refus`) ;
  - synchronisation (`sync`, compteurs par type, refus et conflits, identifiants seulement).
- **Données personnelles** : identité, contacts, souhaits, message libre et pièces de personnes
  qui n'ont postulé à aucune offre précise. Visibles uniquement par `admin` et `direction` avec
  TOTP. Minimisation : la liste n'affiche que nom, prénom, date, souhaits professionnels,
  présence de CV, statut et anomalies (CA27) ; le message libre n'est que sur la fiche. Durée de
  conservation propre et configurable (24 mois par défaut, règle 36), purge et droits d'accès /
  de suppression par KAN-31 ; consentement affiché tel que reçu (règle 37). Aucune donnée réelle
  dans le dépôt : `FixtureSource` seulement (CA19).
- **IA** : aucune dans cette story. Une analyse de CV ou un score d'adéquation des candidatures
  spontanées (cas d'usage 5) et la recherche sémantique dans le vivier (vague 2) feront l'objet
  de stories dédiées, avec pseudonymisation et traçabilité IA. Les fichiers restent dans Spaces,
  lus à la demande.
- **Humain dans la boucle** : aucune décision automatique ; le moteur refuse explicitement les
  candidatures spontanées (CA21) ; « Conservée au vivier » et « Rejetée » sont toujours des
  décisions humaines motivées.
- **Biais** : aucun filtre, tri, colonne ni souhait importé fondé sur le sexe, l'âge, la région,
  la ville de résidence, la photo ou la situation familiale (règles 7, 22, 23 ; CA27, CA35). Le
  lieu de travail **souhaité** est une préférence professionnelle déclarée par le candidat,
  distincte de son origine (Q9).

## Hors périmètre

- Rattachement d'une candidature spontanée à une offre ou à un poste, et proposition de profils
  du vivier pour une offre : **vague 2**.
- Recherche sémantique ou plein texte dans le vivier (CV, message), embeddings : **vague 2**.
- Analyse IA du CV et score d'adéquation pour les candidatures spontanées (cas d'usage 5) :
  story dédiée.
- Détection des doublons et des pièces manquantes (cas d'usage 2) : story dédiée, qui inclura
  les candidatures spontanées (règle 33).
- Exports PDF/Excel des candidatures spontanées (KAN-37) : non prévus dans le MVP (Q7).
- Actions en lot sur la liste des candidatures spontanées.
- Purge effective, droit d'accès et de suppression : **KAN-31**.
- Tuile du tableau de bord et écran des offres d'emploi : **KAN-38**.
- Correspondance `DatabaseSource` / `ApiSource` et webhooks pour les candidatures spontanées :
  après réception du code du site (QS1, QS6).
- Gestion des suppressions côté site (QS7).
- Notification du candidat ou écriture vers le site.
- Traduction arabe effective des libellés (seule la compatibilité RTL est exigée).

## Questions ouvertes

Questions tranchées le 2026-11-23 (proposition par défaut retenue) :

- [x] **Q1 — Type de candidature : champ explicite ou déduit ?** *Proposition par défaut* : champ
  `type` explicite, contrainte de cohérence en base, jamais de déduction implicite du type
  « spontanée » (règles 1 à 3). *Conséquence* : une migration de données (CA1) ; protège contre
  la transformation silencieuse d'une candidature à cible inconnue. *Alternative* : modèle
  séparé `CandidatureSpontanee` ; écarté car pièces, anomalies, décisions, visionneuse et
  journal sont rattachés à `Candidature`.
- [x] **Q2 — Statut « Conservée au vivier »** : nouvelle valeur de statut ou réutilisation d'un
  statut existant (ex. « retenue ») ? *Proposition par défaut* : nouvelle valeur `vivier`,
  limitée aux candidatures spontanées par contrainte (règles 4 et 5). *Conséquence* : choix
  ajouté à `Statut` et `Decision`, filtre KAN-10 adapté (CA61). Réutiliser « retenue »
  fausserait les statistiques et les listes de résultats.
- [x] **Q3 — Décisions possibles** : *Proposition par défaut* : « Conservée au vivier » et
  « Rejetée », avec retour « rejetée → vivier » en révision (règle 29) ; pas de décision de
  sélection. *Alternative* : aucune décision sur les spontanées (simple consultation) ; perdrait
  la qualification du vivier.
- [x] **Q4 — Rattachement à une offre** : *Proposition par défaut* : vague 2 (règle 31).
- [x] **Q5 — Un seul dossier spontané actif par candidat ?** *Proposition par défaut (côté
  application)* : aucune contrainte d'unicité ; indicateur « N dossiers spontanés » et section
  « Autres candidatures » (règles 8, 27). *Conséquence* : pas d'échec de synchronisation si le
  site accepte plusieurs dépôts. Le comportement réel du site reste à confirmer (QS3).
- [x] **Q6 — Durée de conservation** : *Proposition par défaut* : propre aux candidatures
  spontanées, configurable, 24 mois à compter de la date de candidature (ou de la dernière mise
  à jour à la source si disponible), appliquée par KAN-31 ; à valider avec un juriste (loi
  09-08). *Alternative* : même durée que les autres candidatures.
- [x] **Q7 — Exports (KAN-37)** : *Proposition par défaut* : hors périmètre ; aucun client public
  n'attend de liste de candidatures spontanées, et la minimisation plaide contre. La logique de
  filtrage réutilisable (règle 25) permettra un export ultérieur, qui suivra la règle 9 de
  KAN-14.
- [x] **Q8 — Tri par défaut de la liste** : *Proposition par défaut* : date décroissante (les plus
  récentes d'abord : le vivier se consulte par fraîcheur), différent de KAN-10 (date croissante,
  traitement d'un concours).
- [x] **Q9 — Filtre « lieu de travail souhaité »** : est-il compatible avec l'interdiction de
  critères fondés sur la région ? *Proposition par défaut* : oui, car il s'agit d'une préférence
  professionnelle déclarée par le candidat (où il veut travailler), jamais de sa ville ou région
  de résidence, qui restent non affichées et non filtrables. *Alternative* : afficher le lieu
  souhaité sans le rendre filtrable.
- [x] **Q10 — Actions en lot sur les spontanées** : *Proposition par défaut* : non dans cette
  story (règle 32).
- [x] **Q11 — Tableau de bord** : *Proposition par défaut* : KAN-39 fournit le sélecteur (règle
  41), KAN-38 affiche la tuile et décide si le compteur global « candidatures » distingue les
  types.
- [x] **Q12 — Libellé de la décision négative** : « Rejetée » (statut commun) ou « Non retenue »
  (plus courtois pour une démarche spontanée) ? *Proposition par défaut* : statut commun
  `rejetee` avec son libellé « Rejetée » partout (une seule valeur, un seul libellé, tests
  simples). *Alternative* : libellé « Non retenue » pour les seules spontanées, au prix d'un
  libellé dépendant du type.
- [x] **Q13 — Moteur sur une candidature spontanée** : erreur explicite ou ignorance silencieuse ?
  *Proposition par défaut* : erreur dédiée dans `run_eligibility` ; les appelants (commande
  `--evaluate`) filtrent en amont (règle 20). Rend tout appel erroné visible en test.
- [x] **Q14 — Accès de la direction** : *Proposition par défaut* : `admin` et `direction`, comme
  les autres candidatures.
- [x] **Q15 — Section « Autres candidatures de ce candidat »** : *Proposition par défaut* : sur la
  fiche spontanée uniquement, 20 au plus, intitulés et statuts seulement (règle 27).
  *Alternative* : sur toutes les fiches (utile aussi aux concours) : à traiter avec la story des
  doublons.

Questions en attente du code source du site :

- [ ] **QS1 — Stockage des candidatures spontanées sur le site** : même table que les candidatures
  (cible vide) ou modèle distinct ? Espace des identifiants (risque de collision de `source_id`
  entre deux tables) ? *Proposition par défaut* : `source_id` préfixé (`SP-<id>`) si la table
  est distincte. **À confirmer avec le code source du site.**
- [ ] **QS2 — Champs disponibles** : fonction, secteur, ville souhaitée, disponibilité, message,
  autres (prétentions salariales, type de contrat souhaité) ? Correspondance des fonctions avec
  `Fonction`. *Proposition par défaut* : champs de la règle 7 ; tout champ supplémentaire non
  sensible ajouté à l'objet de souhaits par une story de suivi ; aucun champ sur le sexe, l'âge,
  la situation familiale ou la photo n'est importé. **À confirmer avec le code source du site.**
- [ ] **QS3 — Un dossier mis à jour ou plusieurs dépôts ?** Le site permet-il plusieurs
  candidatures spontanées par compte, ou un dossier unique mis à jour ? Fournit-il une date de
  dernière mise à jour ? *Proposition par défaut* : traiter chaque dépôt comme une candidature
  (règle 8) ; si le site met à jour un dossier unique, la date de mise à jour sert de point de
  départ de la conservation (règle 36). **À confirmer avec le code source du site.**
- [ ] **QS4 — Consentement** : le site recueille-t-il un consentement explicite à la conservation
  dans le vivier (case, texte, date) ? *Proposition par défaut* : importer la valeur et la date
  si elles existent, sinon « Non renseigné » ; KAN-31 décidera d'une durée réduite sans
  consentement. **À confirmer avec le code source du site (et avec un juriste).**
- [ ] **QS5 — Rattachement côté site** : le site peut-il transformer une candidature spontanée en
  candidature à une offre (ou l'inverse) ? *Proposition par défaut* : conflit compté, dossier
  inchangé dans l'application (règle 14), traitement humain. **À confirmer avec le code source
  du site.**
- [ ] **QS6 — Pièces dans Spaces** : chemins et types des pièces des candidatures spontanées
  (CV, lettre, autres) ? *Proposition par défaut* : mêmes règles que les autres pièces (règle
  17). **À confirmer avec le code source du site.**
- [ ] **QS7 — Retrait par le candidat** : un candidat peut-il retirer ou supprimer sa candidature
  spontanée sur le site, et comment cela se voit-il (suppression physique, statut) ?
  *Proposition par défaut* : hors de cette story ; à traiter avec la synchronisation des
  suppressions et KAN-31. **À confirmer avec le code source du site.**
