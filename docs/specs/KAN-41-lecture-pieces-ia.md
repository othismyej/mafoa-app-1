# KAN-41 — Lecture des pièces par l'IA (CV, diplômes, attestations) et éligibilité sur pièces

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-41
- **Epic** : E3 — Lecture des pièces et IA
- **Statut** : validée le 2026-11-27 (PO : avancer ; propositions par défaut retenues pour toutes
  les questions ouvertes). Amendée le même jour après le relevé des modèles du site
  (`docs/site/modeles-admin.md`, KAN-25) et la précision du PO sur les formats acceptés
  (règles 1 bis, 4 bis, 9 bis, 22 bis ; CA26 à CA31 ; Q9 à Q13).

## Contexte

Cas d'usage n° 1 et n° 2 du MVP (`CLAUDE.md` §2) : vérifier l'éligibilité **sur pièces** et non
plus seulement sur les données déclarées par le candidat, en lisant automatiquement les CV,
diplômes et attestations de travail (PDF texte et scans).

Aujourd'hui le moteur d'éligibilité (`apps/evaluation/eligibility/`) ne voit que
`Candidature.donnees_declarees` : une candidature peut être classée « éligible » alors que le
diplôme joint est d'un niveau inférieur, ou que l'attestation couvre moins d'années que déclaré.

Existant à réutiliser (pas à re-spécifier) :

- `ai/client.py` : `AIClient.structured()` (sortie validée par un schéma Pydantic,
  pseudonymisation, journal `AppelIA`, transport injectable — simulé dans les tests) ;
  `anthropic_transport` (SDK `anthropic`, `messages.parse`) ;
- `ai/pseudonymize.py` : jetons `[NOM_n]`, `[CIN_n]`, `[TEL_n]`, `[EMAIL_n]` et restauration ;
- `ai/models.py` : `AppelIA` (tâche, modèle, prompt et version, entrée, sortie, statut, erreur,
  durée, jetons) ; `ai/prompts/` : prompts versionnés `<nom>.v<N>.md`, `load_prompt` ;
- `ai/schemas.py` : `ExtractionCV` (générique, prototype) ;
- `apps/documents/models.py` : `Extraction` (pièce, méthode `pdf_texte` / `vision` / `manuelle`,
  statut, texte, données JSON, modèle, version de prompt, confiance, erreur, lien `appel_ia`) —
  aucun service ne la remplit encore ;
- `apps/recruitment/pieces.py` (KAN-12) : `detect_type`, `empreinte`, `controler_empreinte`,
  `signaler_empreinte_differente` ; `SiteSource.read_piece(storage_key, max_bytes, timeout)` ;
- `apps/evaluation/services.py` : `profil_from_declared`, `criteres_from_cible`,
  `run_eligibility` (écrit `VerificationEligibilite.sources = [{"type": "donnees_declarees"}]`) ;
  le moteur `evaluate()` est déterministe et ne rend « non éligible » que sur données certaines ;
- `apps/recruitment/presenters.SOURCE_LABELS` prévoit déjà la source « Pièces extraites » ;
- `integrations/site_source/sync.py` : `sync_from_source` (empreintes, rapport, audit `sync`) ;
- fiche candidature (KAN-11, KAN-12, KAN-13), journal (KAN-14), charte (`docs/design/charte.md`).

Ce que l'on sait du site réel (relevé de l'admin, formulaires vides, KAN-25) :

- une candidature (`ConcourApplication`) n'a qu'**un seul champ fichier « Documents »**, qui
  accepte **n'importe quel type de fichier** : très probablement un PDF combiné (CV + diplôme +
  attestations), parfois une image, un document Word ou une archive. Le profil candidat
  (`CandidateProfile`) porte en plus un fichier « CV » distinct ;
- le site ne stocke **aucune expérience datée** (ni employeur ni dates) : seulement une tranche
  « niveau d'expérience » dans le profil. Sur la candidature : diplôme actuel (texte libre), date
  de diplôme, spécialité (référentiel), mention, établissement. **Les années d'expérience datées
  ne peuvent donc venir que des pièces lues par l'IA** : c'est le cœur de cette story ;
- le profil contient civilité, photo, date de naissance, adresse, région : jamais extraits ni
  envoyés à l'IA (règle 10).

Coordination :

- **KAN-42** (en parallèle) : détection des anomalies (doublons, pièces manquantes,
  incohérences de dates et de noms). KAN-41 **ne crée aucune anomalie** (hormis l'anomalie
  d'empreinte existante de KAN-12) et expose une API de lecture des extractions (règle 30).
- **KAN-43** (spec en parallèle) : analyse d'adéquation CV / fiche de poste pour les offres.
  Elle pourra réutiliser l'extraction de CV de KAN-41 (règle 30) ; KAN-41 ne calcule aucun score.

## User story

En tant que membre de la direction de Mafoa, je veux que l'application lise les CV, diplômes et
attestations de travail joints aux candidatures, en extraie les formations et expériences, et
confronte ces données à la déclaration du candidat et aux critères du poste, afin de vérifier
l'éligibilité sur pièces plus vite, en gardant la main sur tout cas douteux.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Voit la section « Lecture des pièces » de la fiche ; lance « Lire les pièces » et « Relire ». |
| direction | Idem admin. |
| consultant, client (vague 2, non attribuables) | Refusé : 403 sur le lancement et sur le fragment d'état ; chaque refus est tracé en `access_denied` (KAN-14 règle 8). Aucune tâche n'est créée. |
| non connecté | Redirection vers la connexion ; rien n'est journalisé. |
| processus automatique (synchronisation, tâches Celery) | Met en file et exécute les lectures, relance le moteur d'éligibilité ; **jamais** de décision, jamais de changement de statut d'une candidature déjà décidée. |

Contrôle de rôle côté serveur sur chaque vue et chaque fragment HTMX.

## Règles métier

### A. Pièces lues et pipeline

1. **Types lus.** Seules les pièces de type **CV**, **Diplôme**, **Attestation de travail** et **Dossier combiné** (règle 1 bis) sont
   lues. Les lettres de motivation et pièces « Autre » ne le sont pas (aucune donnée utile à
   l'éligibilité ; minimisation). Les candidatures **spontanées** ne sont pas lues dans cette
   story (Q3).
1 bis. **Dossier combiné.** Une pièce de type **« Dossier combiné »** (`Piece.Type.DOSSIER`,
   destiné au champ « Documents » du site) est lue comme un ensemble : l'IA **classe les pages**
   en segments (CV, diplôme, attestation de travail, autre — pages consécutives), extrait les
   données de chaque segment avec le schéma de son type et **cite la page source** dans chaque
   extrait (« p. 3 : … »). Un segment « autre » (relevé de notes, copie de CIN…) n'est décrit que
   par sa nature, sans aucune donnée. Le cas « une pièce = un document » (CV, diplôme, attestation)
   reste inchangé. Les pièces « Lettre » et « Autre » ne sont pas lues (Q11).
2. **Lecture à la demande.** Le contenu est lu via `SiteSource.read_piece` avec une taille
   maximale (`AI_PIECE_MAX_BYTES`, 10 Mo par défaut) et le délai `PIECE_SOURCE_TIMEOUT`. Il n'est
   jamais écrit sur disque ni en cache ; seul le **texte extrait** et les données structurées sont
   conservés (`CLAUDE.md` §4).
3. **Contrôle d'empreinte.** L'empreinte SHA-256 du contenu lu est calculée. Si `Piece.sha256`
   est connue et diffère, la lecture échoue (`empreinte_differente`) et l'anomalie d'empreinte de
   KAN-12 est enregistrée (`signaler_empreinte_differente`, au plus une par empreinte constatée).
   Aucun appel IA n'est fait sur un contenu non conforme.
4. **Choix de la méthode** selon le type **détecté sur les octets** (`detect_type`), jamais sur le
   type déclaré :
   - **PDF avec texte** (au moins `AI_TEXTE_MIN_CARACTERES` = 80 caractères non blancs extraits
     par `pypdf`) → méthode `pdf_texte` : le texte extrait est envoyé à Claude (pseudonymisé si
     `AI_PSEUDONYMIZE`) ;
   - **PDF sans texte** (scan) → méthode `vision` : le PDF est envoyé comme bloc `document` ;
   - **image PNG ou JPEG** → méthode `vision` : bloc `image` ;
   - tout autre contenu → voir la règle 4 bis (DOCX, TIFF lus ; archives et autres formats non lus).
4 bis. **Formats** (le champ « Documents » accepte tout fichier ; type réel détecté sur les
   octets, jamais sur l'extension ni le type déclaré) :
   - **PDF** : texte (`pypdf`, un repère « === Page N === » par page) ou vision (règle 4) ; un PDF
     qui **mêle** pages texte et pages numérisées part en vision si elle est permise ; sinon seules
     les pages texte sont lues et les pages numérisées sont signalées « non lues » sur la fiche ;
   - **PNG, JPEG** : vision ; **TIFF** (souvent multipage) : chaque page est convertie localement
     en PNG (Pillow) puis envoyée en vision ;
   - **DOCX** : texte extrait **localement** (`python-docx` : paragraphes et tableaux), sans appel
     réseau, puis envoyé comme du texte (pseudonymisé) ; contrôle préalable des tailles
     décompressées annoncées (20 Mo au plus, taux de compression ≤ 100) contre les bombes de
     décompression ;
   - **ZIP, RAR, 7z, GZIP, DOC (Word 97-2003), HEIC et tout format inconnu** : **jamais
     décompressés ni convertis** dans cette story ; lecture « ignorée » avec le motif « Format non
     lu automatiquement : à traiter par un humain (archive ZIP…) », visible sur la fiche. KAN-42
     pourra en faire une anomalie à partir de ce statut (Q9, Q10).
5. **Vision et pseudonymisation.** Une image ou un scan ne peut pas être pseudonymisé (le nom, la
   CIN, la photo y figurent). Quand `AI_PSEUDONYMIZE` est actif, la vision n'est permise que si
   `AI_VISION_NON_PSEUDONYMISEE=true` (désactivé par défaut, Q1) ; sinon la lecture est ignorée
   (`vision_non_autorisee`) et la fiche indique « Scan : lecture IA désactivée (pseudonymisation
   impossible) — vérification humaine ». L'appel vision est journalisé avec
   `pseudonymise = false` (règle 15).
6. **Limites, sans troncature silencieuse.** Sont ignorées sans appel IA, avec un message clair :
   pièce plus grande que `AI_PIECE_MAX_BYTES` (taille connue ou constatée), image de plus de 5 Mo
   (limite de l'API), PDF de plus de `AI_PDF_MAX_PAGES` pages (20), texte extrait de plus de
   `AI_TEXTE_MAX_CARACTERES` caractères (60 000). Le texte n'est jamais tronqué avant envoi.
7. **Prompts versionnés par type** (`ai/prompts/`) : `lecture_cv.v1.md`,
   `lecture_diplome.v1.md`, `lecture_attestation.v1.md`. Toute modification d'un prompt crée un
   nouveau fichier `v2` (jamais de modification en place d'une version publiée).
8. **Schémas de sortie Pydantic par type** (`ai/schemas.py`), avec `extra = "forbid"` :
   - **CV** (`LectureCV`) : `nom_titulaire` ; `formations[]` (intitulé, niveau tel qu'écrit,
     spécialité, établissement, année d'obtention, extrait) ; `experiences[]` (employeur,
     intitulé du poste, début, fin, en cours, extrait) ; `competences[]` ; `confiance` ;
     `remarques[]` ;
   - **Diplôme** (`LectureDiplome`) : `nom_titulaire`, `intitule`, `niveau`, `specialite`,
     `etablissement`, `date_obtention`, `annee_obtention`, `extrait`, `confiance`, `remarques[]` ;
   - **Attestation de travail** (`LectureAttestation`) : `nom_titulaire`, `employeur`,
     `periodes[]` (poste, début, fin, en cours, extrait), `date_attestation`, `confiance`,
     `remarques[]` ;
   - **Dossier combiné** (`LectureDossier`) : `segments[]` (`type_document`, `page_debut`,
     `page_fin`, un seul bloc `cv` / `diplome` / `attestation` selon le type, `description` pour
     « autre »), `confiance`, `remarques[]`. Prompt `lecture_dossier.v1.md`.
   Les dates sont des chaînes ISO (`AAAA-MM-JJ`) ou nulles ; leur interprétation est faite par le
   code (règle 20). `confiance` est un nombre entre 0 et 1.
9. **Validation et rejet.** Toute sortie qui ne valide pas le schéma (type inattendu, champ
   obligatoire absent, confiance hors de [0, 1], **champ supplémentaire**) est rejetée : la lecture
   passe en échec « Réponse de l'IA invalide », l'appel est journalisé en erreur, aucune donnée
   n'est enregistrée. Un refus du modèle (`stop_reason = refusal`) donne un échec « Lecture
   refusée par l'IA ».
9 bis. **Contrôle du découpage** (déterministe, après validation du schéma) : pages comprises
    entre 1 et le nombre de pages du document, segments sans chevauchement, bloc renseigné
    conforme au type du segment. Sinon : échec « Réponse de l'IA invalide » (« Découpage du dossier
    incohérent »), aucune donnée enregistrée.
10. **Aucune donnée sensible extraite.** Les schémas ne contiennent ni civilité, ni sexe, ni âge, ni date de
    naissance, ni photo, ni situation familiale, ni nationalité, ni région ou adresse ; les
    prompts l'interdisent explicitement ; un tel champ dans la réponse la fait rejeter (règle 9).
    `nom_titulaire` est conservé (rapprochement de nom pour KAN-42) ; en mode pseudonymisé il est
    reçu sous forme de jetons et ré-identifié côté serveur.
11. **Confiance.** La confiance est celle déclarée par le modèle pour la pièce (0 à 1). Elle est
    affichée en trois niveaux : « élevée » (≥ 0,85), « moyenne » (≥ `AI_CONFIANCE_MIN`, 0,7 par
    défaut), « faible » (< `AI_CONFIANCE_MIN`). Une confiance faible n'est jamais une raison de
    non-éligibilité (règle 23).

### B. Exécution, idempotence, déclenchement

12. **Une tâche Celery par pièce** (`documents.lire_piece`). À la mise en file, une `Extraction`
    « en attente » est créée pour la pièce ; la tâche la complète (terminée, échec ou ignorée).
    Une pièce qui a déjà une lecture en attente de moins de 30 minutes n'est pas remise en file
    (pas de doublon) ; au-delà, la lecture en attente est considérée comme perdue et remplacée.
13. **Idempotence par empreinte et version de prompt.** Une lecture n'est pas refaite si une
    extraction **terminée** existe pour la même pièce, la même empreinte du contenu et le même
    prompt (nom et version) : ni appel IA, ni nouvelle extraction. « Relire » (règle 17) force une
    nouvelle lecture ; les extractions précédentes sont conservées (historique).
14. **Extraction courante.** L'extraction courante d'une pièce est sa dernière extraction terminée
    dont l'empreinte est égale à `Piece.sha256` (ou quelconque si l'empreinte de la pièce est
    inconnue). Une extraction dont l'empreinte ne correspond plus (pièce modifiée à la source) est
    **obsolète** : affichée comme telle et ignorée par l'éligibilité.
15. **Journal de chaque appel IA** (`AppelIA`, via `AIClient`) : tâche (`lecture_cv`,
    `lecture_diplome`, `lecture_attestation`), modèle (`AI_MODEL`), prompt et version, entrée
    envoyée (texte pseudonymisé si `AI_PSEUDONYMIZE` ; pour la vision, description de la pièce
    jointe — type, empreinte, taille — **sans le contenu**), sortie validée, statut, erreur,
    durée, jetons d'entrée et de sortie. L'`Extraction` référence son `AppelIA`.
16. **Clé absente.** Si `ANTHROPIC_API_KEY` n'est pas configurée : aucune mise en file, aucun
    appel ; la fiche affiche « IA non configurée » et le bouton est désactivé ; un envoi direct du
    formulaire renvoie le même message. Les tests n'appellent jamais l'API réelle (transport
    simulé, clé vide dans `config/settings/test.py`).
17. **Bouton sur la fiche** (admin, direction ; POST avec CSRF) :
    - « Lire les pièces » : met en file les pièces lisibles **non encore lues** pour leur
      empreinte et la version de prompt courante ;
    - « Relire » : met en file toutes les pièces lisibles, en forçant une nouvelle lecture.
    Le retour indique le nombre de pièces mises en file et ignorées (déjà lues, en cours).
18. **Après synchronisation.** Si l'IA est configurée et `AI_LECTURE_APRES_SYNC=true` (défaut),
    les pièces lisibles **créées** ou dont l'**empreinte a changé** lors d'une synchronisation sont
    mises en file (après validation de la transaction de synchronisation).
19. **File indisponible.** Si la tâche ne peut pas être mise en file (broker indisponible), la
    lecture en attente passe en échec « File de traitement indisponible » ; rien n'est perdu
    silencieusement.

### C. Éligibilité sur pièces

20. **Profil extrait** (déterministe). À partir des extractions courantes de la candidature :
    - diplômes = diplômes lus + formations des CV lus ;
    - périodes d'expérience = périodes des attestations + expériences des CV ; une date
      illisible devient « manquante » ; « en cours » s'arrête à la date de référence ; les
      chevauchements sont fusionnés par `compute_experience` (aucun double comptage).
    Le calcul des années et la comparaison des niveaux restent ceux du moteur existant.
21. **Critères alimentés.** Niveau d'études et spécialité sont alimentés par les diplômes et CV
    lus ; l'expérience par les attestations et CV lus. Un critère sans pièce lue correspondante
    reste évalué sur le seul déclaré.
22. **Combinaison prudente, critère par critère.** Le moteur est exécuté deux fois (déclaré,
    pièces). Pour chaque critère alimenté par des pièces :
    - déclaré et pièces **concordent** (même verdict) et toutes les pièces utilisées ont une
      confiance suffisante → le verdict est conservé ;
    - sinon (verdicts différents, ou confiance faible) → **« à vérifier »**, avec la raison
      (« Écart entre le déclaré (Éligible) et les pièces (Non éligible) : … » ou « Confiance
      faible de la lecture de la pièce n° … »).
    Le critère « Autres critères » est inchangé.
22 bis. **Déclaré muet sur un critère** (cas général pour l'expérience : le site ne stocke aucune
    expérience datée ; aussi pour le niveau si aucun diplôme n'est déclaré) : les pièces sont la
    **seule source**. Le critère est « éligible » si les pièces le montrent avec une confiance
    suffisante (raison « Établi sur les pièces n° … (aucune donnée déclarée) ») ; tout autre
    résultat (pièces insuffisantes, incomplètes ou confiance faible) donne **« à vérifier »**,
    jamais « non éligible » (Q12). Le détail porte `source = pieces`. La tranche « niveau
    d'expérience » du profil n'est pas utilisée ici (conversion en années : KAN-44).
23. **Jamais de non-éligibilité sur la seule base de l'IA.** Un critère n'est « non éligible » que
    si le déclaré l'est **et** que les pièces le confirment avec une confiance suffisante, ou que
    le déclaré l'est et qu'aucune pièce n'alimente ce critère (comportement actuel). Une pièce lue
    ne fait jamais passer un critère déclaré éligible ou à vérifier en non éligible.
24. **Sources enregistrées.** `VerificationEligibilite.sources` liste `{"type":
    "donnees_declarees"}` puis, pour chaque pièce utilisée, `{"type": "extraction", "piece_id",
    "extraction_id", "piece_type", "prompt_version", "confiance"}`. Le détail de chaque critère
    porte en plus `source` (`declare`, `declare_et_pieces` ou `pieces`), `verdict_declare`,
    `verdict_pieces`, `pieces` (identifiants), `references` (« n° 12 », « n° 14 p. 3-4 ») et
    `confiance` (minimum). Pour un dossier combiné, une source par segment utilisé, avec
    `pages` et le type du segment. La version enregistrée est
    `1.1.0` (moteur inchangé + combinaison sur pièces) ; sans pièce lue, la vérification est
    identique à aujourd'hui (`sources` = déclaré seul, version `1.0.0`).
25. **Relance automatique.** Quand la dernière lecture en attente d'une candidature se termine,
    `run_eligibility` est relancé une fois pour cette candidature (candidature de concours ou
    d'offre). Il ne reclasse jamais une candidature qui a une décision humaine et ne crée jamais
    de `Decision` (règles existantes de `run_eligibility`).
26. **Écarts déclaré ↔ pièce** (affichage, informatif). Calculés de façon déterministe :
    niveau le plus élevé déclaré ≠ lu, aucune spécialité lue identique à une spécialité déclarée
    (comparaison normalisée), différence d'années d'expérience ≥ 0,5 an. Ils sont affichés sur la
    fiche même quand les verdicts concordent ; ils ne créent pas d'anomalie (KAN-42).

### D. Affichage sur la fiche

27. **Section « Lecture des pièces (IA) »** dans la colonne principale, après « Éligibilité »,
    pour les candidatures de concours et d'offre. Pour chaque pièce lisible :
    - type et nom de fichier, statut de lecture : « Non lue », « Lecture en cours », « Lue le … »
      (méthode, modèle, version du prompt), « Échec : … », « Ignorée : … », « Obsolète (pièce
      modifiée depuis la lecture) » ;
    - confiance (badge élevée / moyenne / faible) ;
    - données extraites : formations / diplôme (intitulé, niveau, spécialité, établissement,
      année) et expériences / périodes (poste, employeur, dates), avec l'extrait cité ;
    - remarques de l'IA.
    Puis un encadré « Écarts entre le déclaré et les pièces » (règle 26) ou « Aucun écart
    constaté ». Mention permanente : « Données extraites par IA, à vérifier sur la pièce. »
28. **Fragment d'état** (HTMX, `GET`) : tant qu'une lecture est en attente, la section se
    rafraîchit toutes les 5 secondes **sans afficher de données personnelles** (statuts
    seulement) ; une fois tout terminé, elle propose « Actualiser la fiche ». Ce fragment n'est pas
    journalisé comme consultation (il ne montre aucun contenu de dossier, comme le fragment de la
    visionneuse, KAN-12 Q6).
29. Les sources de la vérification d'éligibilité affichent « Données déclarées » puis
    « Pièce n° N (Diplôme) » pour chaque pièce utilisée.

### E. API exposée (KAN-42, KAN-43)

30. Module `apps/documents/services.py`, fonctions sans effet de bord ni appel IA :
    - `extractions_de(candidature) -> list[Extraction]` : extractions **courantes** (règle 14),
      une par pièce lue ;
    - `documents_lus(candidature)` / `documents_de(extraction) -> list[DocumentLu]` : documents
      lus, pièce simple ou segment de dossier (`piece_id`, `extraction_id`, `type`, `pages`,
      `donnees`, `confiance`, `reference`) ;
    - `profil_extrait(candidature) -> ProfilExtrait` : `profil` (`ProfilCandidat` du moteur),
      `diplomes_pieces` / `experience_pieces` (identifiants des pièces utilisées par critère),
      `confiance_*` par critère, `noms_titulaires` (`{piece_id: [noms]}`, plusieurs pour un
      dossier), `documents`, `extractions` ;
    - `ecarts_declare_pieces(candidature) -> list[Ecart]` (règle 26).
    Les données d'une extraction sont lisibles avec le schéma de son type
    (`schema_pour(piece_type).model_validate(extraction.donnees)`).

## Critères d'acceptation

- **CA1** — Étant donné un CV PDF texte (FixtureSource), quand il est lu, alors une `Extraction`
  « terminée », méthode `pdf_texte`, contient le texte extrait, les données validées par
  `LectureCV`, la confiance, le modèle, la version de prompt et le lien vers l'`AppelIA` ; le texte
  envoyé à l'IA ne contient ni le nom, ni la CIN, ni l'email, ni le téléphone du candidat.
- **CA2** — Diplôme, attestation et dossier combiné PDF texte sont lus avec leur prompt (`lecture_diplome`,
  `lecture_attestation`) et leur schéma ; une lettre de motivation n'est jamais lue.
- **CA3** — Une image PNG ou JPEG, ou un PDF sans texte, est lue par vision quand
  `AI_VISION_NON_PSEUDONYMISEE=true` : le transport reçoit un bloc `image` ou `document` ;
  l'`AppelIA` porte `pseudonymise = false` et son entrée ne contient pas le contenu de la pièce.
- **CA4** — Avec `AI_PSEUDONYMIZE=true` et la vision non autorisée, un scan est « ignoré »
  (`vision_non_autorisee`) sans aucun appel IA ; la fiche l'indique.
- **CA5** — Une sortie invalide (confiance hors bornes, champ manquant, champ supplémentaire
  « sexe ») est rejetée : lecture en échec « Réponse de l'IA invalide », `AppelIA` en erreur,
  aucune donnée enregistrée. Un refus du modèle donne un échec « Lecture refusée par l'IA ».
- **CA6** — Même pièce, même empreinte, même version de prompt : une deuxième lecture (bouton ou
  synchronisation) ne fait aucun appel ; « Relire » fait un nouvel appel et conserve l'ancienne
  extraction.
- **CA7** — Une extraction faite avec une autre version de prompt n'empêche pas la lecture avec la
  version courante.
- **CA8** — Clé API absente : le bouton est désactivé avec « IA non configurée » ; un POST renvoie
  ce message, ne crée ni extraction ni `AppelIA` ; la synchronisation ne met rien en file.
- **CA9** — Pièce trop volumineuse (taille enregistrée ou constatée), texte trop long, PDF trop
  long : lecture ignorée avec le motif, aucun appel, aucune troncature.
- **CA10** — Pièce introuvable, source indisponible : échec avec motif, aucun appel. Empreinte
  différente : échec, anomalie d'empreinte KAN-12 créée (une seule), aucun appel.
- **CA11** — « Lire les pièces » (admin et direction) met en file une tâche par pièce lisible ; une
  pièce déjà en cours n'est pas dupliquée ; le message indique les nombres.
- **CA12** — Consultant ou client : 403, entrée `access_denied`, aucune tâche. GET sur l'URL de
  lancement : 405. Candidature spontanée : refus 400 « non disponible ».
- **CA13** — Le lancement est journalisé (`ai_call`, `operation = lecture_pieces`, mode `lire` ou
  `relire`, identifiants des pièces, sans nom) ; chaque lecture terminée, en échec ou ignorée est
  journalisée (`ai_call` sur l'extraction, statut, méthode, `appel_ia_id`), au nom de
  l'utilisateur ou de « système (lecture après synchronisation) ».
- **CA14** — Après une synchronisation, les pièces lisibles créées ou à empreinte modifiée sont
  mises en file (IA configurée) ; une pièce inchangée ne l'est pas.
- **CA15** — Déclaré et pièces concordants avec confiance suffisante : verdict conservé ; `sources`
  = déclaré + pièces ; version `1.1.0`.
- **CA16** — Déclaré Bac+5, diplôme lu Bac+3, poste Bac+5 : critère et verdict « à vérifier »
  (jamais « non éligible »), raison mentionnant l'écart.
- **CA17** — Confiance faible d'une pièce utilisée : critères concernés « à vérifier ».
- **CA18** — Déclaré non éligible, pièces éligibles : « à vérifier ». Déclaré non éligible et pièces
  concordantes (confiance suffisante) : « non éligible ».
- **CA19** — Sans extraction courante (aucune, en échec, obsolète) : vérification identique à
  aujourd'hui (déclaré seul, version `1.0.0`).
- **CA20** — Quand la dernière lecture en attente d'une candidature se termine, une vérification
  est créée ; une candidature décidée n'est pas reclassée ; aucune `Decision` n'est créée.
- **CA21** — La fiche affiche, par pièce, le statut de lecture, la confiance, les données extraites
  et extraits, et les écarts déclaré ↔ pièce ; les sources de l'éligibilité nomment les pièces.
- **CA22** — Le fragment d'état ne contient aucune donnée personnelle, n'écrit aucune entrée de
  consultation, et se rafraîchit tant qu'une lecture est en attente.
- **CA23** — Les schémas n'ont aucun champ sexe, âge, date de naissance, photo, situation
  familiale, nationalité, région ou adresse ; les prompts interdisent ces informations.
- **CA24** — `extractions_de`, `profil_extrait` et `ecarts_declare_pieces` renvoient les données
  courantes sans appel IA ni écriture.
- **CA25** — La section est traduisible (catalogue `ar` à jour) et n'utilise que des propriétés
  logiques (RTL).

- **CA26** — Dossier combiné PDF (CV p. 1, diplôme p. 2, attestation p. 3, relevé p. 4) : le texte
  envoyé porte un repère par page ; `documents_de` renvoie quatre segments avec leurs pages ; les
  sources de la vérification citent les pages (le segment « autre » n'est pas utilisé) ; la fiche
  affiche chaque segment (« Diplôme · page 2 ») et « Pièce n° N p. 2 (Diplôme) ».
- **CA27** — Segment hors des pages du document ou bloc incohérent avec son type : échec
  « Réponse de l'IA invalide », aucune donnée.
- **CA28** — PDF mêlant pages texte et page numérisée : sans vision, lecture texte et pages
  numérisées signalées non lues ; avec vision permise, tout le PDF part en vision.
- **CA29** — Déclaré sans expérience (cas du site) : attestation lue montrant l'expérience
  exigée → critère « éligible » (`source = pieces`) ; expérience insuffisante ou confiance faible
  → « à vérifier » ; jamais « non éligible ».
- **CA30** — Déclaré sans diplôme, diplôme lu de niveau insuffisant → « à vérifier ».
- **CA31** — Formats : DOCX lu localement en texte (méthode « Word ») ; DOCX dont la taille
  décompressée est excessive refusé ; TIFF multipage converti en PNG pour la vision ; ZIP, RAR,
  7z, DOC, HEIC, inconnu → « Format non lu automatiquement » avec le format détecté, sans appel ni
  décompression, visible sur la fiche, y compris avec une extension trompeuse.

## Cas limites et erreurs

- Pièce sans `storage_key` valide pour la source → « introuvable ».
- Réponse de l'IA tronquée (`max_tokens`) ou sans contenu exploitable → échec « Réponse de l'IA
  invalide ».
- Erreur réseau, délai, quota (429), erreur serveur : le SDK réessaie (3 fois) ; au-delà, échec
  « IA momentanément indisponible » ; « Relire » permet de recommencer.
- Clé invalide (401) → échec « IA non configurée ou clé refusée ».
- Date au format inattendu (« 2019 », « mars 2019 ») : le prompt demande `AAAA-MM-JJ` ; toute
  valeur non ISO est traitée comme manquante par le code (→ « à vérifier » si elle compte).
- Pièce lue puis supprimée à la source : l'extraction reste tant que la pièce existe dans l'app ;
  suppression en cascade avec la pièce (purge KAN-31).
- Plusieurs CV ou diplômes : tous sont lus et cumulés.
- Deux tâches qui terminent en même temps la dernière lecture d'une candidature : la relance est
  faite sous verrou de la candidature ; au pire deux vérifications identiques.

## Traçabilité et conformité

- **Journal d'audit** : lancement (`ai_call`, candidature, mode, identifiants des pièces) ; chaque
  lecture (`ai_call` sur l'extraction : statut, méthode, code d'erreur, `appel_ia_id`) ;
  reclassement par le moteur (existant). Jamais de nom, CIN, email, téléphone ni nom de fichier
  dans les métadonnées. La fiche reste journalisée par `log_consultation` (KAN-14).
- **Traçabilité IA** : `AppelIA` pour chaque appel (règle 15) ; `Extraction` garde modèle,
  prompt, version, empreinte lue et lien vers l'appel ; prompts versionnés dans le dépôt.
- **Pseudonymisation** (`AI_PSEUDONYMIZE`, activée par défaut) : texte envoyé sans nom, prénom,
  CIN, téléphone ni email ; restauration côté serveur. Vision non pseudonymisable : désactivée
  par défaut quand la pseudonymisation est active (règle 5, Q1).
- **Transfert hors du Maroc** : l'appel à l'API Claude reste soumis à la validation CNDP avant
  mise en production avec des données réelles (`CLAUDE.md` §6) ; seules des données fictives sont
  utilisées en développement et en test.
- **Humain dans la boucle** : l'IA extrait ; le code calcule ; un écart ou un doute donne « à
  vérifier » ; aucune décision automatique ; « non éligible » jamais sur la seule base de l'IA.
- **Explicabilité** : chaque donnée extraite est accompagnée de l'extrait cité ; chaque critère
  indique ses sources et les pièces utilisées.
- **Biais** : aucune donnée de sexe, âge, photo, situation familiale ou région extraite (règle 10).
- **Clé API** : lue dans `ANTHROPIC_API_KEY` (variable d'environnement, `.env` jamais commité).

## Hors périmètre

- Détection d'anomalies (doublons, pièces manquantes, incohérences de dates et de noms) : KAN-42.
- Score d'adéquation CV / fiche de poste, points forts, questions d'entretien : KAN-43.
- Score sur grille (cas d'usage n° 3).
- Lecture des candidatures spontanées et recherche dans le vivier (vague 2).
- Saisie manuelle ou correction des données extraites (méthode `manuelle` prévue dans le modèle).
- Décompression des archives et lecture des formats DOC et HEIC (Q9, Q10).
- Import du champ « Documents » et du CV du profil depuis le site réel (`DatabaseSource`,
  `ApiSource`, Q11).
- Lecture en lot depuis la liste des candidatures (possible ensuite via KAN-15).
- OCR local (Tesseract) pour pseudonymiser les scans avant envoi (Q1, piste vague 2).

## Questions ouvertes

- [x] **Q1 — Vision et pseudonymisation** : un scan ne peut pas être pseudonymisé. *Proposition
  par défaut* : vision désactivée quand `AI_PSEUDONYMIZE=true`, sauf
  `AI_VISION_NON_PSEUDONYMISEE=true` (à décider avec la validation CNDP) ; les scans non lus sont
  signalés « vérification humaine ».
- [x] **Q2 — Règle de combinaison** : tout écart de verdict entre déclaré et pièces, ou toute
  confiance faible, donne « à vérifier » (y compris quand les pièces seraient plus favorables que
  le déclaré). *Proposition par défaut* retenue.
- [x] **Q3 — Candidatures spontanées** : *Proposition par défaut* : non lues dans cette story
  (finalité vivier, vague 2).
- [x] **Q4 — Seuil de confiance** : *Proposition par défaut* : 0,7 (`AI_CONFIANCE_MIN`),
  configurable.
- [x] **Q5 — Lecture automatique après synchronisation** : *Proposition par défaut* : oui
  (`AI_LECTURE_APRES_SYNC=true`), seulement pour les pièces nouvelles ou modifiées.
- [x] **Q6 — Relance automatique de l'éligibilité après lecture** : *Proposition par défaut* :
  oui, une fois par candidature quand ses lectures sont terminées (classement, pas décision).
- [x] **Q7 — Limites** : *Proposition par défaut* : 10 Mo par pièce, 20 pages par PDF en vision,
  60 000 caractères de texte, 5 Mo par image (limite API) ; au-delà : vérification humaine.
- [x] **Q9 — Archives (ZIP, RAR, 7z…) dans le champ « Documents »** : *Proposition par défaut* :
  ne jamais décompresser dans cette story (bombes de décompression, fichiers imbriqués, formats
  piégés) ; statut « Format non lu automatiquement », traitement humain ; anomalie par KAN-42.
  Une décompression contrôlée (taille, profondeur, nombre de fichiers) pourra faire l'objet d'une
  story dédiée.
- [x] **Q10 — DOC (Word 97-2003) et HEIC** : *Proposition par défaut* : non lus (pas de
  bibliothèque standard sûre sans dépendance système) ; DOCX lu localement avec `python-docx` ;
  TIFF converti avec Pillow.
- [x] **Q11 — Correspondance avec le site** : *Proposition par défaut* : le fichier « Documents »
  d'une candidature est importé en `Piece` de type « Dossier combiné » ; le fichier « CV » du
  profil, s'il est importé, en type « CV » (à câbler dans `DatabaseSource` / `ApiSource` quand
  l'accès sera ouvert). Les pièces « Autre » ne sont pas lues.
- [x] **Q12 — Éligible sur pièces seules** : quand le déclaré est muet (expérience datée absente
  du site), *Proposition par défaut* : « éligible » permis si les pièces le montrent avec une
  confiance suffisante ; « non éligible » jamais sans vérification humaine.
- [x] **Q13 — Tranche « niveau d'expérience » du profil** : *Proposition par défaut* : non
  utilisée par KAN-41 ; sa conversion en années et sa confrontation aux pièces relèvent de KAN-44.
- [x] **Q8 — Modèle** : *Proposition par défaut* : `AI_MODEL` (défaut `claude-opus-5-5`), effort
  `AI_EFFORT` (défaut `medium`), repli serveur en cas de refus (`AI_FALLBACKS=true`) ; un
  changement de modèle ne relance pas les lectures (« Relire » si besoin).
