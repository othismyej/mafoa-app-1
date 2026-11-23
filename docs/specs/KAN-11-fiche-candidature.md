# KAN-11 — Fiche candidature

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-11
- **Epic** : E2 — Écrans concours et éligibilité
- **Statut** : validée le 2026-11-23 par le Product Owner (réponses par défaut retenues pour toutes les questions ouvertes ; à revoir quand l'accès aux données et aux modèles du site sera disponible, voir KAN-24 et KAN-25)

## Contexte

La direction de Mafoa doit pouvoir comprendre et justifier chaque dossier, notamment en cas de
recours sur un e-Concours public. L'écran 3 de `CLAUDE.md` §7 (« Fiche candidature ») réunit sur
une seule page tout ce que l'application sait d'une candidature.

Existant dans le code (à réutiliser, pas à re-spécifier) :

- `apps/recruitment/models.py` :
  - `Candidat` : `nom`, `prenom`, `email`, `telephone`, `date_naissance`, `cin`, `ville`, et deux
    champs `sexe` et `region` **réservés au contrôle des biais** (vague 2), jamais affichés comme
    critère ;
  - `Candidature` : `candidat`, `poste_concours` **ou** `offre` (contrainte XOR), `concours`
    (dénormalisé), `date_candidature`, `statut` (9 valeurs : reçue, en analyse, éligible, non
    éligible, à vérifier, présélectionnée, convoquée, retenue, rejetée), `donnees_declarees`
    (JSON : `formation` = liste de `{intitule, niveau, specialite, etablissement,
    annee_obtention}` ; `experiences` = liste de `{debut, fin, employeur, poste}`, ou absent si
    non renseigné), `source_id` / `source_synced_at` ;
  - `Piece` : `type` (CV, diplôme, attestation de travail, lettre, autre), `nom_fichier`,
    `content_type`, `taille`, `sha256`, `recue_le`, `storage_key`.
- `apps/evaluation/models.py` :
  - `VerificationEligibilite` (historisée, tri `-created_at`) : `resultat`, `details` (liste de
    `{critere, libelle, verdict, raison, attendu, constate}`), `justification`, `sources`,
    `moteur_version`, `created_at` ;
  - `Anomalie` : `type`, `gravite` (info, moyenne, haute), `description`, `piece`,
    `candidature_liee` (doublon), `resolue`, `resolue_par`, `resolue_le`, `created_at` ;
  - `Decision` (ajout seul, motif obligatoire) : `auteur`, `decision`, `motif`, `created_at`.
- `apps/accounts/permissions.py` : `RoleRequiredMixin` / `role_required` avec `DIRECTION_ROLES`
  (`admin`, `direction`).
- `apps/audit/services.py` : `log(action, actor, obj, before, after, request, metadata)` ;
  `AuditAction.VIEW` existe déjà.
- `templates/base.html` : gabarit Tailwind + HTMX, `dir="rtl"` géré via `LANGUAGE_BIDI`.

Aucune vue ni gabarit de fiche candidature n'existe encore (seul le tableau de bord est en place).

## User story

En tant que membre de la direction, je veux une fiche par candidature montrant l'identité du
candidat, ses données déclarées (formation, expériences), le verdict d'éligibilité critère par
critère avec ses raisons, les anomalies et l'historique des décisions, afin de comprendre et
justifier chaque dossier.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Lecture complète de la fiche. |
| direction | Lecture complète de la fiche. |
| consultant, client (vague 2, non attribuables) | Refusé : 403. |
| non connecté | Redirection vers la page de connexion (`LoginRequiredMiddleware`). |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP (`MFARequiredMiddleware`). |
| compte désactivé (`is_active = False`) | Refusé (pas de session possible ; à défaut 403). |

La vérification du rôle se fait côté serveur sur la vue de la fiche **et** sur chaque fragment
HTMX qu'elle charge (aucun fragment ne doit être accessible sans contrôle de rôle).

## Règles métier

1. **Une fiche par candidature**, accessible par une URL stable fondée sur l'identifiant interne
   de la candidature (par exemple `/candidatures/<id>/`). L'identifiant `source_id` du site n'est
   pas utilisé dans l'URL.
2. **En-tête** : nom (en majuscules) et prénom du candidat, statut courant de la candidature
   (libellé français), date de candidature, cible :
   - poste de concours : intitulé du concours, organisation, référence et intitulé du poste ;
   - offre d'emploi : intitulé de l'offre et organisation (ou « Confidential Company » si
     `entreprise_confidentielle`).
   Un lien de retour mène à la liste des candidatures du poste (ou de l'offre).
3. **Identité et contacts** affichés : nom, prénom, CIN, date de naissance, email, téléphone,
   ville. Champ vide : afficher « Non renseigné ».
4. **Données interdites à l'écran** : `sexe`, `region`, et tout âge calculé ne sont **jamais**
   affichés sur la fiche (ni en clair, ni dans un attribut HTML, ni dans un fragment HTMX). La
   date de naissance n'est affichée que comme élément d'identification (rapprochement CIN /
   doublons), sans calcul d'âge.
5. **Données déclarées — formation** : une ligne par entrée de `donnees_declarees.formation`,
   avec intitulé, niveau, spécialité, établissement, année d'obtention, dans l'ordre du JSON.
   Clé absente ou liste vide : message « Aucune formation déclarée ».
6. **Données déclarées — expériences** : une ligne par entrée de
   `donnees_declarees.experiences`, avec employeur, poste, date de début, date de fin (« en
   cours » si fin vide alors que début renseigné). Les dates sont affichées au format
   `JJ/MM/AAAA`. Distinguer :
   - clé `experiences` absente : « Expérience non renseignée » ;
   - liste vide : « Aucune expérience déclarée » ;
   - date manquante ou illisible : afficher « Date manquante » / la valeur brute marquée
     « Date illisible », sans faire échouer la page.
   La fiche n'effectue **aucun calcul** d'expérience : la durée retenue est celle constatée par
   le moteur (règle 8).
7. **Pièces** : liste des pièces de la candidature (type, nom du fichier, type MIME, taille
   lisible, date de réception). Aucune ouverture du fichier ni appel à Spaces dans cette story :
   l'affichage de la liste n'utilise que les métadonnées en base. Les pièces signalées par une
   anomalie non résolue portent un indicateur. Aucune pièce : « Aucune pièce reçue ».
   L'emplacement de la visionneuse (KAN-12) est prévu mais vide.
8. **Verdict d'éligibilité** : la **dernière** `VerificationEligibilite` (la plus récente par
   `created_at`) est affichée avec :
   - le résultat global (éligible / non éligible / à vérifier) ;
   - un tableau critère par critère, dans l'ordre de `details` : libellé, verdict, attendu,
     constaté, raison ;
   - la version du moteur et la date de la vérification ;
   - les sources (`sources`), en libellé lisible (ex. « Données déclarées »).
   Le verdict « non éligible » d'un critère est toujours accompagné de sa raison. Les couleurs ne
   sont jamais le seul porteur d'information (libellé texte obligatoire).
9. **Historique des vérifications** : s'il existe plusieurs vérifications, les précédentes sont
   accessibles (repliées ou en fragment HTMX) avec date, version du moteur et résultat global.
10. **Écart verdict / statut** : si une décision humaine existe et que le résultat de la dernière
    vérification diffère de la dernière décision d'éligibilité ou du statut courant, un bandeau
    informatif l'indique (« Le moteur indique … ; la décision humaine du … reste en vigueur »).
    Aucun statut n'est modifié par l'affichage.
11. **Anomalies** : liste de toutes les anomalies de la candidature, non résolues d'abord puis
    par date décroissante, avec type, gravité, description, pièce concernée, date de détection ;
    pour une anomalie résolue : par qui et quand. Pour un doublon (`candidature_liee`), un lien
    vers la fiche de la candidature liée. Un compteur d'anomalies non résolues figure dans
    l'en-tête. La résolution d'une anomalie n'est **pas** dans cette story.
12. **Historique des décisions** : toutes les `Decision` de la candidature, de la plus récente à
    la plus ancienne, avec décision (libellé), motif complet, auteur (nom complet ou email),
    date et heure. La plus récente est marquée « en vigueur ». Aucune décision : « Aucune
    décision enregistrée ». Le formulaire de décision (KAN-13) s'insère dans cette section mais
    n'est pas livré ici.
13. **Lecture seule** : la fiche n'effectue aucune écriture métier (aucun changement de statut,
    aucun lancement du moteur ni de l'IA). Seule l'écriture de journal de consultation (KAN-14)
    est autorisée.
14. **Score sur grille** : hors de cette story (epic E3) ; un emplacement « Score : non
    disponible » peut être prévu.
15. **i18n** : tous les libellés passent par `{% translate %}` / `gettext` ; la mise en page
    utilise des propriétés logiques (ex. `ms-`/`me-`, `text-start`) pour fonctionner en RTL.
16. **Performance** : le nombre de requêtes SQL de la fiche est borné et ne dépend pas du nombre
    de pièces, d'anomalies, de vérifications ou de décisions (usage de `select_related` /
    `prefetch_related`).

## Critères d'acceptation

- **CA1** — Étant donné un utilisateur `direction` connecté avec TOTP et une candidature
  existante, quand il ouvre la fiche, alors la réponse est 200 et contient nom, prénom, CIN,
  email, téléphone, ville et date de naissance du candidat, le statut courant en libellé français
  et la date de candidature.
- **CA2** — Étant donné un utilisateur `admin`, quand il ouvre la même fiche, alors la réponse
  est 200 avec le même contenu.
- **CA3** — Étant donné un utilisateur non connecté, quand il demande la fiche, alors il est
  redirigé vers la page de connexion avec le paramètre `next`.
- **CA4** — Étant donné un utilisateur au rôle `consultant` ou `client`, quand il demande la
  fiche ou l'un de ses fragments HTMX, alors la réponse est 403 et aucune donnée du candidat
  n'est renvoyée.
- **CA5** — Étant donné un identifiant de candidature inexistant, quand un utilisateur
  `direction` demande la fiche, alors la réponse est 404.
- **CA6** — Étant donné un candidat dont `sexe` = « F » et `region` = « Souss-Massa » (valeurs
  fictives), quand la fiche est affichée, alors ni la valeur de sexe, ni la région, ni les
  libellés « Sexe » / « Région », ni un âge calculé n'apparaissent dans le HTML de la fiche ni
  dans ses fragments.
- **CA7** — Étant donné une candidature à un poste de concours, quand la fiche est affichée,
  alors l'en-tête affiche l'intitulé du concours, l'organisation, la référence et l'intitulé du
  poste, et un lien vers la liste des candidatures de ce poste.
- **CA8** — Étant donné une candidature à une offre `entreprise_confidentielle = True`, quand la
  fiche est affichée, alors l'en-tête affiche l'intitulé de l'offre et « Confidential Company »
  au lieu du nom de l'organisation.
- **CA9** — Étant donné `donnees_declarees` avec deux formations, quand la fiche est affichée,
  alors chaque formation apparaît avec intitulé, niveau, spécialité, établissement et année
  d'obtention, dans l'ordre du JSON.
- **CA10** — Étant donné `donnees_declarees` avec deux expériences dont une sans date de fin,
  quand la fiche est affichée, alors chaque expérience montre employeur, poste, début et fin au
  format `JJ/MM/AAAA`, et l'expérience sans fin affiche « en cours ».
- **CA11** — Étant donné `donnees_declarees` = `{}` (aucune clé), quand la fiche est affichée,
  alors la page est 200 et affiche « Aucune formation déclarée » et « Expérience non
  renseignée » ; étant donné `experiences` = `[]`, alors elle affiche « Aucune expérience
  déclarée ».
- **CA12** — Étant donné une expérience dont `debut` est `null` et une autre dont `debut` vaut
  `"2020-13-45"`, quand la fiche est affichée, alors la page est 200, la première affiche « Date
  manquante » et la seconde affiche la valeur brute marquée « Date illisible ».
- **CA13** — Étant donné une candidature avec trois pièces (CV, diplôme, attestation), quand la
  fiche est affichée, alors les trois pièces sont listées avec type, nom du fichier, taille
  lisible (ex. « 120 Ko ») et date de réception, et aucun appel au stockage de fichiers (Spaces /
  `boto3`) n'est effectué (vérifié par un mock qui échoue s'il est appelé).
- **CA14** — Étant donné une candidature sans pièce, quand la fiche est affichée, alors le
  message « Aucune pièce reçue » apparaît.
- **CA15** — Étant donné deux vérifications d'éligibilité successives (« à vérifier » puis
  « éligible »), quand la fiche est affichée, alors le verdict global affiché est « Éligible »
  (la plus récente), avec la version du moteur et la date, et la vérification précédente est
  accessible dans l'historique des vérifications.
- **CA16** — Étant donné une vérification dont `details` contient les critères niveau d'études,
  spécialité et expérience, quand la fiche est affichée, alors un tableau présente pour chacun
  le libellé, le verdict en texte, l'attendu, le constaté et la raison, dans l'ordre de
  `details`.
- **CA17** — Étant donné une candidature sans aucune vérification d'éligibilité, quand la fiche
  est affichée, alors la section éligibilité affiche « Éligibilité non encore vérifiée » et la
  page est 200.
- **CA18** — Étant donné une candidature au statut « convoquée » par décision humaine et une
  vérification plus récente au résultat « non éligible », quand la fiche est affichée, alors un
  bandeau signale l'écart et le statut affiché reste « Convoquée ».
- **CA19** — Étant donné une anomalie non résolue de gravité haute et une anomalie résolue,
  quand la fiche est affichée, alors l'anomalie non résolue apparaît en premier avec type,
  gravité et description, l'anomalie résolue indique son auteur et sa date de résolution, et
  l'en-tête indique « 1 anomalie non résolue ».
- **CA20** — Étant donné une anomalie « doublon de candidat » liée à une autre candidature,
  quand la fiche est affichée, alors un lien mène à la fiche de la candidature liée.
- **CA21** — Étant donné une anomalie rattachée à une pièce, quand la fiche est affichée, alors
  la pièce concernée porte un indicateur d'anomalie dans la liste des pièces.
- **CA22** — Étant donné trois décisions successives sur une candidature, quand la fiche est
  affichée, alors elles sont listées de la plus récente à la plus ancienne avec décision, motif
  complet, auteur et date-heure, et seule la plus récente est marquée « en vigueur ».
- **CA23** — Étant donné une candidature sans décision, quand la fiche est affichée, alors le
  message « Aucune décision enregistrée » apparaît.
- **CA24** — Étant donné une candidature, quand la fiche est affichée (GET), alors aucun objet
  `Candidature`, `VerificationEligibilite`, `Decision`, `Anomalie` ou `Score` n'est créé ni
  modifié, et aucune tâche Celery ni appel IA n'est déclenché.
- **CA25** — Étant donné une candidature avec 50 pièces, 50 anomalies, 20 vérifications et 20
  décisions, quand la fiche est affichée, alors le nombre de requêtes SQL est identique (à une
  constante près fixée par le test) à celui d'une candidature avec une pièce, une anomalie, une
  vérification et une décision.
- **CA26** — Étant donné la langue active `ar`, quand la fiche est affichée, alors l'élément
  `<html>` porte `dir="rtl"` et la page est 200 (le contenu non traduit reste en français).
- **CA27** — Étant donné un motif de décision ou une description d'anomalie contenant
  `<script>alert(1)</script>`, quand la fiche est affichée, alors le texte est échappé et aucune
  balise `<script>` correspondante n'est présente dans le HTML.

## Cas limites et erreurs

- `donnees_declarees` mal formé (ex. `formation` n'est pas une liste, entrée sans clé attendue) :
  la page reste 200, les champs absents affichent « Non renseigné ».
- `details` de vérification vide ou critère sans `attendu` / `constate` : cellules « — », pas
  d'erreur.
- Valeur de `statut` ou de `verdict` inconnue (donnée importée incohérente) : affichage de la
  valeur brute, pas d'erreur 500.
- Candidat présent dans plusieurs candidatures (autres concours ou offres) : hors périmètre de
  l'affichage, sauf lien via une anomalie de doublon.
- Candidature liée par une anomalie supprimée (`candidature_liee` = NULL) : afficher « candidature
  liée indisponible ».
- Auteur d'une décision désactivé : son nom reste affiché (pas de suppression d'utilisateur
  grâce à `PROTECT`).
- Synchronisation du site en cours pendant la consultation : la fiche montre l'état en base au
  moment de la requête ; la date `source_synced_at` est affichée (« Données du site synchronisées
  le … »).
- Volumes importants : voir CA25 ; motifs et descriptions longs sont affichés en entier (retour à
  la ligne), sans troncature silencieuse.
- Droits insuffisants sur un fragment HTMX appelé directement : 403 (CA4).

## Traçabilité et conformité

- **Journal d'audit** : chaque ouverture de la fiche doit être journalisée (`AuditAction.VIEW`,
  objet = candidature, acteur, IP, navigateur). Cette journalisation est **livrée par KAN-14** :
  KAN-11 en dépend. Tant que KAN-14 n'est pas fusionné, la fiche ne doit pas être activée sur
  des données réelles. Le point d'accroche (appel unique au service d'audit dans la vue) doit
  être prévu pour que KAN-14 n'ait pas à modifier la structure de la vue.
- **Données personnelles** : identité et contacts du candidat, formation, expériences,
  métadonnées des pièces, motifs de décision. Affichés uniquement aux rôles `admin` et
  `direction`. Minimisation : `sexe`, `region` et âge jamais affichés ; pas de photo ; le
  contenu des pièces n'est pas affiché dans cette story. En-têtes `Cache-Control: no-store` sur
  la fiche et ses fragments pour éviter la mise en cache de données personnelles.
- **IA** : aucun appel IA dans cette story. Les données affichées proviennent des déclarations
  du candidat et du moteur déterministe. Lorsque des extractions IA existeront (E3), leur
  affichage fera l'objet d'une story distincte.
- **Biais** : aucun critère fondé sur le sexe, l'âge, la région, la photo ou la situation
  familiale n'est affiché ni suggéré (CA6).
- **Explicabilité** : chaque verdict affiche ses raisons, l'attendu et le constaté (CA16).

## Hors périmètre

- Visionneuse des pièces et ouverture des fichiers Spaces (KAN-12).
- Journalisation des consultations (KAN-14, dépendance).
- Formulaire et règles de décision (KAN-13).
- Résolution des anomalies, relance du moteur d'éligibilité depuis la fiche.
- Données extraites des pièces par l'IA et score sur grille (epic E3).
- Navigation « candidature précédente / suivante » et actions en lot (écran liste).
- Exports PDF/Excel de la fiche.
- Traduction arabe effective des libellés (seule la compatibilité RTL est exigée).

## Questions ouvertes

Toutes tranchées le 2026-11-23 : la proposition par défaut est retenue pour chaque question.

- [x] **Q1 — Date de naissance affichée ?** Elle sert à l'identification (CIN, doublons) mais
  permet de déduire l'âge, critère interdit. *Proposition par défaut* : l'afficher (sans âge
  calculé), car elle est nécessaire pour vérifier l'identité en cas de recours. *Conséquence
  alternative* : la masquer par défaut avec un bouton « Afficher » journalisé, ce qui alourdit
  KAN-14.
- [x] **Q2 — Fiche des candidatures à une offre privée** dans cette story ? *Proposition par
  défaut* : oui, même écran (en-tête adapté, CA8), puisque le modèle est commun ; les éléments
  propres au recrutement privé (score d'adéquation, CU5) viendront plus tard.
- [x] **Q3 — Activation avant KAN-14** : peut-on fusionner KAN-11 avant KAN-14 ? *Proposition par
  défaut* : oui, la fusion est permise tant que seules des données fictives sont chargées ;
  la mise en production avec données réelles exige KAN-14.
- [x] **Q4 — CIN et téléphone en clair** pour tous les membres de la direction ? *Proposition
  par défaut* : oui (outil réservé au top management) ; sinon prévoir un masquage partiel
  (`AB12****`).
- [x] **Q5 — Historique des vérifications** : toutes les vérifications ou seulement les N
  dernières ? *Proposition par défaut* : toutes, chargées à la demande en fragment HTMX.
