# KAN-12 — Visionneuse des pièces dans la fiche candidature

- **Ticket** : https://othmane-smyej.atlassian.net/browse/KAN-12
- **Epic** : E2 — Écrans concours et éligibilité
- **Statut** : validée le 2026-11-23 par le Product Owner (réponses par défaut retenues pour toutes les questions ouvertes ; à revoir quand l'accès aux fichiers réels du site sera disponible, voir KAN-24 et KAN-25)

## Contexte

Pour vérifier un verdict d'éligibilité (diplôme, spécialité, années d'expérience) ou une
anomalie, la direction doit pouvoir lire les pièces du dossier sans quitter la fiche
candidature (écran 3 de `CLAUDE.md` §7 : « pièces (visionneuse) »). Le brief impose que les
fichiers **restent dans Spaces et soient lus à la demande, sans copie permanente**
(`CLAUDE.md` §4), que la base et le stockage du site ne soient **jamais accessibles depuis le
navigateur**, et que chaque consultation de dossier soit journalisée (§5).

Existant dans le code (à réutiliser, pas à re-spécifier) :

- `apps/recruitment/models.py` — `Piece` : `candidature` (FK, `CASCADE`), `type` (cv, diplome,
  attestation_travail, lettre, autre), `nom_fichier`, `storage_key` (clé Spaces ou
  `fixture://…`), `content_type` (déclaré par le site, non vérifié), `taille` (octets, peut être
  vide), `sha256` (peut être vide), `recue_le`. **Constat** : dans les fixtures, `nom_fichier`
  contient le nom du candidat (ex. `cv_alaoui.pdf`) ; il ne doit donc jamais figurer dans le
  journal d'audit.
- `integrations/site_source/base.py` — `SiteSource.read_piece(storage_key) -> bytes` (lecture à
  la demande, contenu complet en mémoire) ; `get_source()` choisit l'implémentation selon
  `settings.SITE_SOURCE`. `DatabaseSource` et `ApiSource` héritent de `UnavailableSource` : leur
  `read_piece` lève `NotImplementedError`.
- `integrations/site_source/fixture_source.py` — `FixtureSource.read_piece` génère un **PDF
  texte déterministe** (reportlab, `invariant=1`) pour une clé `fixture://candidatures/<A-xxxx>/<type>.pdf`,
  lève `FileNotFoundError` pour une clé inconnue. Toutes les pièces fictives sont des PDF ; il
  n'existe pas d'image fictive.
- `integrations/site_source/sync.py` — à la synchronisation, `sha256` et `taille` sont calculés
  en lisant chaque pièce (si `compute_hashes=True`), puis le contenu est jeté. **Constat** : une
  nouvelle synchronisation **réécrit** `sha256` sans signaler de changement (voir Q12, hors
  périmètre).
- `apps/evaluation/models.py` — `Anomalie` : `candidature`, `piece`, `type` (6 valeurs, dont
  `autre`), `gravite`, `description`, `resolue`…
- `apps/recruitment/views.py` — `candidature_detail` (KAN-11) précharge les pièces triées par
  `type`, `id` et journalise `AuditAction.VIEW` avec `object_repr="Candidature #<id>"`.
- `templates/recruitment/candidature_detail.html` — bloc « Pièces » (liste des métadonnées,
  indicateur d'anomalie) et emplacement vide `<div id="visionneuse" data-kan12></div>`.
- `apps/accounts/permissions.py` — `role_required(*DIRECTION_ROLES)`.
- `apps/audit/services.py` — `log(action, actor, obj, before, after, request, actor_repr,
  object_repr, metadata)`.
- `config/settings/base.py` — `X_FRAME_OPTIONS = "DENY"`, CSP globale avec
  `frame-ancestors 'none'`, `object-src 'none'`, `default-src 'self'` (pas de `frame-src`
  explicite, donc `frame-src` hérite de `'self'`), `SECURE_CONTENT_TYPE_NOSNIFF = True`,
  `ATOMIC_REQUESTS = True`. `NoStoreForAuthenticatedMiddleware` ajoute déjà
  `Cache-Control: no-store, private` aux réponses des utilisateurs connectés.

**Conséquence technique structurante** : avec la configuration actuelle, une pièce servie par
l'application **ne peut pas** être affichée dans une `<iframe>` de la fiche (`X-Frame-Options:
DENY` et `frame-ancestors 'none'` sur la réponse de la pièce), et `<object>` / `<embed>` sont
interdits par `object-src 'none'` sur la fiche. La spec impose donc : affichage par `<iframe>`
(autorisé côté fiche par `frame-src 'self'`) et assouplissement **limité à la seule vue de
contenu** (`X-Frame-Options: SAMEORIGIN`, `frame-ancestors 'self'`), la politique globale
restant inchangée.

## User story

En tant que membre de la direction, je veux consulter les pièces d'un dossier (CV, diplôme,
attestation, lettre) directement depuis la fiche, lues à la demande depuis la source sans copie
permanente, afin de vérifier un verdict sans changer d'outil.

## Rôles et permissions

| Rôle | Accès |
|---|---|
| admin | Consulter (aperçu) et télécharger toute pièce d'une candidature. |
| direction | Idem admin. |
| consultant, client (vague 2, non attribuables) | Refusé : 403, aucun octet de la pièce renvoyé, aucun appel à la source. |
| non connecté | Redirection vers la connexion (`LoginRequiredMiddleware`), aucun appel à la source. |
| connecté sans TOTP actif | Redirection vers l'activation du TOTP (`MFARequiredMiddleware`). |
| compte désactivé | Refusé (pas de session possible ; à défaut 403). |

Le rôle est vérifié côté serveur sur **les deux** vues de la story (fragment de visionneuse et
contenu de la pièce), avant tout accès à la base de la pièce ou à la source.

## Règles métier

### Vues et URL

1. **Deux vues `GET` uniquement** (toute autre méthode, y compris `HEAD` et `POST` : 405) :
   - **fragment de visionneuse** : `/candidatures/<pk>/pieces/<piece_pk>/` — renvoie le fragment
     HTML inséré dans `#visionneuse` ; il n'utilise que les métadonnées en base et **n'appelle
     jamais la source** ;
   - **contenu de la pièce** : `/candidatures/<pk>/pieces/<piece_pk>/contenu/` — lit la pièce
     via `SiteSource` et renvoie ses octets ; paramètre `?telechargement=1` pour un
     téléchargement (règle 14).
   Les identifiants sont les identifiants internes ; ni `source_id`, ni `storage_key`, ni URL
   Spaces n'apparaissent dans une URL, un attribut HTML ou une réponse.
2. **Appartenance** : la pièce doit appartenir à la candidature de l'URL
   (`Piece.candidature_id == pk`). Sinon : 404, sans appel à la source (règle 22 pour la trace).
   Candidature ou pièce inexistante : 404.
3. **Pas d'URL présignée** : le navigateur ne reçoit jamais d'URL directe vers Spaces (ni
   présignée, ni publique). Toute lecture passe par la vue de contenu, qui applique permissions,
   contrôles et journalisation (voir Q1).

### Lecture à la demande, sans copie permanente

4. La vue de contenu obtient la source par `get_source()` et appelle
   `read_piece(piece.storage_key)`. Le contenu est tenu **en mémoire** le temps de la requête :
   aucune écriture sur disque (ni fichier temporaire, ni `MEDIA_ROOT`), aucune mise en cache
   applicative (Redis, cache Django), aucun stockage en base du contenu. Seules les métadonnées
   existantes de `Piece` sont lues.
5. **Lecture partielle non prise en charge** : l'en-tête `Range` est ignoré, la réponse est
   toujours complète (200) avec `Accept-Ranges: none`. Une consultation = une requête = une
   entrée de journal.
6. **Taille maximale** : `PIECE_VIEWER_MAX_BYTES` (variable d'environnement, défaut
   20 Mo = 20 971 520 octets).
   - Si `Piece.taille` est connue et dépasse le maximum : refus **sans lire la source**.
   - Si `Piece.taille` est vide : lecture, puis refus si le contenu lu dépasse le maximum (le
     contenu est aussitôt libéré).
   - Refus : statut 422, message « Pièce trop volumineuse pour la visionneuse (taille : X Mo ;
     maximum : 20 Mo). » ; le téléchargement est soumis à la même limite.
   L'interface `SiteSource.read_piece` reçoit un paramètre optionnel `max_bytes` que les futures
   implémentations (`DatabaseSource`, `ApiSource`, lecture Spaces) devront respecter pour ne pas
   charger un fichier trop gros ; `FixtureSource` peut l'ignorer.
7. **Délai de lecture** : `PIECE_SOURCE_TIMEOUT` (défaut 10 s) est transmis aux implémentations
   réseau futures ; un dépassement est traité comme « source indisponible » (règle 18).

### Types de fichiers et en-têtes

8. **Type réel détecté sur les octets** (signature), jamais déduit du seul `content_type`
   déclaré ni de l'extension :
   - PDF : `%PDF-` présent dans les 1 024 premiers octets ;
   - PNG : commence par `89 50 4E 47 0D 0A 1A 0A` ;
   - JPEG : commence par `FF D8 FF`.
   Ces trois types sont **affichables** dans la visionneuse. Tout autre contenu (DOCX, TIFF,
   HEIC, WebP, GIF, SVG, HTML, texte, archive…) n'est **jamais** servi en ligne : il n'est
   proposé qu'au téléchargement (voir Q5).
9. **Choix du mode d'affichage dans le fragment** (sans lire la source) d'après le
   `content_type` déclaré : `application/pdf` → `<iframe>` ; `image/png` ou `image/jpeg` →
   `<img>` ; autre ou vide → message « Aperçu non disponible pour ce type de fichier » et bouton
   « Télécharger ». La vue de contenu reste seule juge de la sécurité (règle 10).
10. **Contenu en ligne non affichable** : si un aperçu en ligne est demandé et que le type
    détecté n'est pas affichable (ex. `content_type` déclaré PDF mais octets HTML), la vue
    répond 415 avec le message « Aperçu impossible : le contenu ne correspond pas à un type
    affichable. Utilisez « Télécharger ». » ; aucun octet de la pièce n'est renvoyé.
11. **En-têtes de la réponse de contenu (succès)** :
    - `Content-Type` = type détecté (`application/pdf`, `image/png`, `image/jpeg`) ; en
      téléchargement d'un type non affichable : `application/octet-stream` ;
    - `Content-Disposition: inline` (aperçu) ou `attachment` (téléchargement), avec un nom de
      fichier assaini (règle 13) sous les formes `filename="…"` (ASCII) et
      `filename*=UTF-8''…` ;
    - `Content-Length` exact ;
    - `X-Content-Type-Options: nosniff` ;
    - `Cache-Control: no-store, private` (et pas d'`ETag` ni de `Last-Modified`) ;
    - `X-Frame-Options: SAMEORIGIN` (dérogation à `DENY` limitée à cette vue) ;
    - `Content-Security-Policy` propre à cette vue : `default-src 'none'; frame-ancestors
      'self'; base-uri 'none'; form-action 'none'`, complétée de `sandbox` **pour les images
      uniquement** (Chromium refuse d'afficher un PDF dans un contexte `sandbox`) ;
    - `Cross-Origin-Resource-Policy: same-origin` ;
    - `Referrer-Policy: same-origin` (global, inchangé).
12. **Politique globale inchangée** : toutes les autres réponses (fiche, fragment, autres pages)
    conservent `X-Frame-Options: DENY` et `frame-ancestors 'none'`. La fiche n'utilise ni
    `<object>` ni `<embed>` (interdits par `object-src 'none'`) ; l'`<iframe>` n'a pas
    d'attribut `sandbox` (incompatible avec le lecteur PDF de Chromium).
13. **Nom de fichier** : `nom_fichier` de la pièce, débarrassé des chemins (`/`, `\`), des
    caractères de contrôle (dont CR/LF), des guillemets et ramené à 150 caractères ; extension
    remplacée par celle du type détecté (`.pdf`, `.png`, `.jpg`) si elle ne correspond pas ;
    nom vide après assainissement : `piece-<piece_pk>.<ext>` (voir Q14).
14. **Téléchargement** (`?telechargement=1`) : autorisé aux mêmes rôles pour tous les types
    (`Content-Disposition: attachment`), soumis aux règles 6, 15 à 18 ; journalisé avec
    `mode = telechargement` (voir Q8). Toute autre valeur du paramètre est traitée comme un
    aperçu.

### Intégrité (empreinte SHA-256)

15. Si `Piece.sha256` est renseignée, l'empreinte SHA-256 du contenu lu est calculée et
    comparée (insensible à la casse).
    - **Conforme** : la pièce est servie normalement.
    - **Différente** : l'**aperçu en ligne est refusé** (statut 409, message « Le fichier a été
      modifié à la source depuis sa réception (empreinte différente). Une anomalie a été
      enregistrée. Vous pouvez le télécharger pour l'examiner. ») ; le **téléchargement reste
      possible** ; une anomalie est enregistrée (règle 16). Voir Q2.
    - **Empreinte absente** : la pièce est servie ; le fragment affiche « Empreinte non
      disponible : intégrité non vérifiée » ; l'empreinte n'est **pas** renseignée à cette
      occasion (voir Q13).
    `Piece.sha256` n'est jamais modifiée par la visionneuse.
16. **Anomalie d'empreinte** : nouveau type `Anomalie.Type.EMPREINTE_DIFFERENTE` (« Empreinte de
    pièce différente », migration de choix ; voir Q3), gravité **haute**, rattachée à la pièce et
    à la candidature, description sans donnée nominative : « Le contenu lu à la source le
    JJ/MM/AAAA HH:MM ne correspond pas à l'empreinte enregistrée. Empreinte attendue : <64 hex>.
    Empreinte constatée : <64 hex>. » Idempotence : aucune nouvelle anomalie si une anomalie de
    ce type (résolue ou non) existe déjà pour la même pièce avec la même empreinte constatée ;
    la vérification et la création se font sous verrou de la ligne `Piece`
    (`select_for_update`) pour éviter les doublons en cas de consultations simultanées. La
    création est journalisée (`AuditAction.CREATE`, objet = anomalie, acteur = « système
    (contrôle d'empreinte) », `metadata` = `piece_id`, `candidature_id`,
    `declenche_par_user_id`). L'anomalie n'a **aucun effet** sur le statut de la candidature ni
    sur l'éligibilité (aucune élimination automatique) ; elle apparaît dans la section
    Anomalies et comme indicateur sur la pièce (KAN-11).
17. Si la pièce porte une anomalie `empreinte_differente` non résolue, le fragment de
    visionneuse affiche un bandeau d'attention (« Le fichier a été modifié à la source depuis sa
    réception ») avant tout chargement du contenu.

### Erreurs de la source

18. Les erreurs sont **renvoyées** (et non levées) pour que l'entrée de journal survive à
    `ATOMIC_REQUESTS` ; aucune ne produit de 500 ni n'expose de trace, de `storage_key` ou de
    message technique :
    - **source indisponible** (`NotImplementedError` de `UnavailableSource`, erreur réseau,
      délai dépassé, erreur Spaces autre que « clé absente », toute autre exception de la
      source) : 503, message « La source des pièces est momentanément indisponible. Réessayez
      dans quelques instants. » ; l'erreur technique est consignée dans les journaux applicatifs
      (sans contenu ni nom de candidat) ;
    - **fichier introuvable à la source** (`FileNotFoundError`, clé Spaces absente) : 404,
      message « Fichier introuvable à la source. » (aucune anomalie automatique, voir Q9) ;
    - **fichier vide** (0 octet) : 422, message « Le fichier reçu est vide. ».
    En mode aperçu, ces messages sont rendus par une petite page HTML sans script
    (`frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`) pour s'afficher lisiblement dans
    l'`<iframe>`.

### Visionneuse dans la fiche (HTMX)

19. **Ouverture** : chaque pièce de la liste porte un bouton « Consulter » (`hx-get` du fragment,
    cible `#visionneuse`) ; son `href` de repli pointe vers le contenu de la pièce dans un nouvel
    onglet (`target="_blank" rel="noopener"`), pour un fonctionnement sans JavaScript. **Aucune
    pièce n'est chargée automatiquement** à l'affichage de la fiche : la fiche continue de ne
    faire aucun appel à la source (KAN-11, CA13).
20. **Contenu du fragment** :
    - en-tête : type de pièce (libellé), nom du fichier (dans un `<bdi>` pour le bidi), taille
      lisible, date de réception, position « Pièce 2 sur 4 », indicateur d'anomalie le cas
      échéant, mention d'empreinte absente (règle 15) ou bandeau (règle 17) ;
    - aperçu : `<iframe>` (PDF) avec `title` « Aperçu : <type de pièce> » et une hauteur d'au
      moins 60 % de la fenêtre, ou `<img>` (image) avec `alt` « <type de pièce>, pièce n sur N »
      et largeur maximale 100 %, ou message « Aperçu non disponible » (règle 9) ;
    - actions : « Pièce précédente », « Pièce suivante » (HTMX, désactivées ou absentes aux
      extrémités), « Ouvrir dans un nouvel onglet », « Télécharger », « Fermer » (vide
      `#visionneuse`).
21. **Navigation** : l'ordre est celui de la liste des pièces de la fiche (`type`, puis `id`) ;
    précédente / suivante ne proposent que des pièces de la **même** candidature. Après
    chargement, le focus clavier est placé sur le titre du fragment ; l'`aria-live="polite"` de
    la zone annonce le changement de pièce. Le nombre de requêtes SQL du fragment est borné et
    ne dépend pas du nombre de pièces.

### Journalisation (point d'accroche partagé avec KAN-14)

22. **Chaque réponse de la vue de contenu** après contrôle du rôle et de l'existence de la
    candidature et de la pièce produit **exactement une** entrée :
    `log(AuditAction.VIEW, obj=piece, request=request, object_repr=f"Pièce #{piece.pk}
    (Candidature #{candidature.pk})", metadata={...})` avec `metadata` :
    `candidature_id`, `piece_id`, `type_piece` (code), `mode` (`apercu` | `telechargement`),
    `resultat` (`servie` | `trop_volumineuse` | `source_indisponible` | `introuvable` | `vide` |
    `type_non_affichable` | `empreinte_differente`), `empreinte` (`conforme` | `differente` |
    `non_verifiee` | absent si non lue), `type_detecte` (si lu), `taille` (octets, si lue).
    Cet appel unique est le point d'accroche que KAN-14 exploitera (écran du journal des
    consultations) sans modifier la structure de la vue.
23. **Refus journalisés** (voir Q7) : un 403 pour rôle insuffisant (utilisateur connecté) et une
    pièce existante demandée sous une autre candidature (règle 2) produisent une entrée
    `AuditAction.VIEW` sans objet, `object_repr="Pièce #<piece_pk> (Candidature #<pk>)"`,
    `metadata.refus` = `droits` | `piece_hors_candidature`. La réponse 403 est renvoyée (et non
    levée) pour que la trace survive à `ATOMIC_REQUESTS`, comme dans KAN-13.
24. **Aucune donnée nominative dans le journal** : ni nom, prénom, CIN, email, téléphone du
    candidat, ni `nom_fichier`, ni `storage_key`, ni contenu ou extrait de la pièce, dans
    `object_repr`, `metadata`, `before` ou `after`.
25. Le **fragment de visionneuse n'est pas journalisé** (il n'affiche que des métadonnées déjà
    présentes sur la fiche journalisée) ; seule la lecture du contenu l'est (voir Q6).

### Interface

26. **i18n et RTL** : libellés via `{% translate %}` / `gettext` ; propriétés logiques
    uniquement (`ms-`/`me-`, `start-`/`end-`, `text-start`) ; les flèches de navigation portent
    `.flip-rtl` ; en arabe, « précédente » reste la pièce d'ordre inférieur. Le contenu de la pièce
    n'est pas mis en miroir.
27. **Mobile** (< 768 px) : la visionneuse occupe toute la largeur de la carte, l'aperçu garde
    une hauteur d'au moins 60 % de la fenêtre, les boutons ont une cible ≥ 40 px, et « Ouvrir
    dans un nouvel onglet » est mis en avant (certains navigateurs mobiles affichent mal un PDF
    dans une `<iframe>`).
28. **Charte** (`docs/design/charte.md`) : aucun script ni gestionnaire d'événement en ligne,
    aucun attribut `style` ; erreurs et bandeaux au ton « attention » (jamais rouge) ; le
    designer peut adapter l'emplacement et la taille de la visionneuse sans changer le
    comportement (voir Q10).

## Critères d'acceptation

Les tests utilisent `FixtureSource` ou une source simulée (`get_source` remplacé par un double
de test) qui renvoie des octets fictifs (PDF généré, PNG/JPEG de 1 × 1 pixel, HTML, octets
aléatoires) ou lève l'exception voulue. Aucune donnée réelle.

**Permissions et appartenance**

- **CA1** — Étant donné un utilisateur `direction` connecté avec TOTP et une candidature fictive
  dont la pièce CV a pour clé `fixture://candidatures/A-0001/cv.pdf`, quand il demande le
  contenu de cette pièce, alors la réponse est 200, `Content-Type: application/pdf` et le corps
  commence par `%PDF-` et est identique à `FixtureSource.read_piece` pour cette clé.
- **CA2** — Étant donné un utilisateur `admin`, quand il demande le même contenu, alors le
  résultat est identique.
- **CA3** — Étant donné un utilisateur non connecté, quand il demande le fragment ou le contenu,
  alors il est redirigé vers la connexion avec `next`, et la source n'est pas appelée (double de
  test qui échoue s'il est appelé).
- **CA4** — Étant donné un utilisateur au rôle `consultant` ou `client`, quand il demande le
  fragment ou le contenu, alors la réponse est 403, le corps ne contient aucun octet de la pièce
  ni son nom de fichier, la source n'est pas appelée, et (pour le contenu) une entrée
  `JournalAudit` `view` avec `metadata.refus = "droits"` est créée.
- **CA5** — Étant donné une pièce de la candidature A demandée sous l'URL de la candidature B,
  quand un utilisateur `direction` demande le fragment ou le contenu, alors la réponse est 404,
  la source n'est pas appelée, et une entrée `view` avec `metadata.refus =
  "piece_hors_candidature"` est créée pour la demande de contenu.
- **CA6** — Étant donné un identifiant de candidature ou de pièce inexistant, alors fragment et
  contenu répondent 404 sans appel à la source.
- **CA7** — Étant donné une requête `POST` ou `HEAD` sur le fragment ou le contenu, alors la
  réponse est 405 et la source n'est pas appelée.

**En-têtes et types**

- **CA8** — Étant donné un aperçu PDF servi (CA1), alors la réponse porte `Content-Disposition`
  commençant par `inline` avec `filename=` et `filename*=UTF-8''`, `Content-Length` égal à la
  taille du corps, `X-Content-Type-Options: nosniff`, un `Cache-Control` contenant `no-store`,
  `X-Frame-Options: SAMEORIGIN`, `Accept-Ranges: none`, `Cross-Origin-Resource-Policy:
  same-origin`, une CSP contenant `default-src 'none'` et `frame-ancestors 'self'` et **ne
  contenant pas** `sandbox`, et aucun en-tête `ETag` ni `Last-Modified`.
- **CA9** — Étant donné une pièce dont la source renvoie un PNG (resp. un JPEG) fictif, quand
  l'aperçu est demandé, alors la réponse est 200 avec `Content-Type: image/png` (resp.
  `image/jpeg`), `Content-Disposition: inline` et une CSP contenant `sandbox`.
- **CA10** — Étant donné une pièce déclarée `application/pdf` dont la source renvoie
  `<html><script>alert(1)</script></html>`, quand l'aperçu est demandé, alors la réponse est 415,
  son corps ne contient pas `<script>alert(1)` et l'entrée de journal porte `resultat =
  "type_non_affichable"` ; quand le téléchargement est demandé, alors la réponse est 200 avec
  `Content-Type: application/octet-stream` et `Content-Disposition: attachment`.
- **CA11** — Étant donné une pièce dont la source renvoie un document Word fictif (octets
  commençant par `PK`), quand le fragment est affiché avec `content_type` déclaré
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, alors il ne contient
  ni `<iframe>` ni `<img>` d'aperçu, affiche « Aperçu non disponible pour ce type de fichier » et
  un lien « Télécharger » vers le contenu avec `telechargement=1`.
- **CA12** — Étant donné un `nom_fichier` fictif `../cv "test"\r\nX-Inject: 1.pdf`, quand le
  contenu est servi, alors `Content-Disposition` ne contient ni `/`, ni `\`, ni CR/LF, ni
  guillemet non échappé dans le nom, et aucun en-tête `X-Inject` n'est présent ; étant donné un
  `nom_fichier` `cv_test.docx` dont le contenu est un PDF, alors le nom proposé se termine par
  `.pdf`.
- **CA13** — Étant donné une requête de contenu avec l'en-tête `Range: bytes=0-99`, alors la
  réponse est 200 avec le corps complet (pas de 206).
- **CA14** — Étant donné la fiche candidature et le fragment de visionneuse, alors leurs
  réponses conservent `X-Frame-Options: DENY` et une CSP contenant `frame-ancestors 'none'` ;
  le HTML de la fiche et du fragment ne contient ni `<object`, ni `<embed`, ni attribut
  `sandbox` sur l'`<iframe>`.

**Lecture à la demande**

- **CA15** — Étant donné une consultation de contenu réussie, alors aucun fichier n'est créé
  sur disque (vérifié en simulant `tempfile` et l'écriture du stockage par défaut, qui échouent
  s'ils sont appelés), aucune clé n'est ajoutée au cache Django, et les champs de `Piece`
  (`sha256`, `taille`, `updated_at`) sont inchangés.
- **CA16** — Étant donné une fiche candidature et un fragment de visionneuse affichés, alors la
  source n'est jamais appelée (double de test qui échoue s'il est appelé) ; seule la vue de
  contenu l'appelle, une fois par requête.
- **CA17** — Étant donné une pièce de `storage_key` `fixture://candidatures/A-0001/cv.pdf`,
  alors ni la fiche, ni le fragment, ni aucune réponse d'erreur ne contiennent la chaîne
  `fixture://` ni `digitaloceanspaces.com`.

**Taille**

- **CA18** — Étant donné `PIECE_VIEWER_MAX_BYTES = 1000` et une pièce dont `taille = 5000`,
  quand l'aperçu ou le téléchargement est demandé, alors la réponse est 422 avec le message
  « Pièce trop volumineuse pour la visionneuse », la source n'est pas appelée, et le journal
  porte `resultat = "trop_volumineuse"`.
- **CA19** — Étant donné `PIECE_VIEWER_MAX_BYTES = 1000`, une pièce sans `taille` et une source
  qui renvoie 5 000 octets, alors la réponse est 422 et aucun octet de la pièce n'est renvoyé.
- **CA20** — Étant donné une source qui renvoie 0 octet, alors la réponse est 422 avec « Le
  fichier reçu est vide » et le journal porte `resultat = "vide"`.

**Empreinte**

- **CA21** — Étant donné une pièce dont `sha256` est l'empreinte (en majuscules) du contenu
  renvoyé, alors l'aperçu est servi (200) et le journal porte `empreinte = "conforme"`.
- **CA22** — Étant donné une pièce dont `sha256` diffère de l'empreinte du contenu renvoyé, quand
  l'aperçu est demandé, alors la réponse est 409 avec le message « Le fichier a été modifié à la
  source depuis sa réception », aucun octet de la pièce n'est renvoyé, une `Anomalie` de type
  `empreinte_differente`, gravité `haute`, rattachée à la pièce est créée avec les empreintes
  attendue et constatée dans sa description, une entrée `create` (objet = anomalie) et une
  entrée `view` (`resultat = "empreinte_differente"`) sont journalisées, et `Piece.sha256`
  ainsi que `Candidature.statut` sont inchangés.
- **CA23** — Étant donné la situation de CA22, quand le téléchargement est demandé, alors la
  réponse est 200 avec `Content-Disposition: attachment` et le journal porte `empreinte =
  "differente"`.
- **CA24** — Étant donné la situation de CA22, quand l'aperçu est demandé trois fois (y compris
  après résolution de l'anomalie) avec le même contenu, alors il n'existe qu'une seule anomalie
  `empreinte_differente` pour la pièce ; quand la source renvoie ensuite un autre contenu
  différent, alors une seconde anomalie est créée.
- **CA25** — Étant donné une pièce sans `sha256`, alors l'aperçu est servi (200), le journal
  porte `empreinte = "non_verifiee"`, `Piece.sha256` reste vide, et le fragment affiche
  « Empreinte non disponible : intégrité non vérifiée ».
- **CA26** — Étant donné une pièce portant une anomalie `empreinte_differente` non résolue, quand
  le fragment est affiché, alors il contient le bandeau « Le fichier a été modifié à la source
  depuis sa réception ».

**Erreurs de la source**

- **CA27** — Étant donné `SITE_SOURCE = "database"` (`DatabaseSource`, non disponible), ou une
  source qui lève `ConnectionError` ou `TimeoutError`, quand le contenu est demandé, alors la
  réponse est 503 avec le message « La source des pièces est momentanément indisponible », sans
  trace technique ni nom de classe dans le corps, et une entrée `view` avec `resultat =
  "source_indisponible"` existe en base après la requête.
- **CA28** — Étant donné une pièce dont la clé est inconnue de `FixtureSource`
  (`FileNotFoundError`), quand le contenu est demandé, alors la réponse est 404 avec « Fichier
  introuvable à la source », aucune anomalie n'est créée et le journal porte `resultat =
  "introuvable"`.

**Visionneuse (fragment)**

- **CA29** — Étant donné une candidature avec trois pièces (attestation, CV, diplôme), quand la
  fiche est affichée, alors chaque pièce porte un bouton « Consulter » avec `hx-get` vers son
  fragment, `hx-target="#visionneuse"` et un `href` vers son contenu avec `target="_blank"` et
  `rel="noopener"`, et `#visionneuse` est vide.
- **CA30** — Étant donné la même candidature, quand le fragment de la pièce PDF de rang 2 est
  demandé avec `HX-Request: true`, alors il contient une `<iframe>` dont `src` est l'URL de
  contenu de cette pièce et dont `title` commence par « Aperçu », la mention « Pièce 2 sur 3 »,
  des liens « Pièce précédente » et « Pièce suivante » vers les fragments des pièces de rang 1 et
  3, un lien « Ouvrir dans un nouvel onglet » (`target="_blank"`) et un lien « Télécharger »
  (`telechargement=1`).
- **CA31** — Étant donné la première (resp. dernière) pièce, alors le fragment ne propose pas de
  lien actif « Pièce précédente » (resp. « Pièce suivante »).
- **CA32** — Étant donné une pièce déclarée `image/jpeg`, alors le fragment contient un `<img>`
  dont `src` est l'URL de contenu et dont `alt` est non vide et ne contient pas le nom du
  candidat.
- **CA33** — Étant donné deux candidatures A (2 pièces) et B (2 pièces), alors les liens
  précédente / suivante du fragment d'une pièce de A ne pointent jamais vers une pièce de B.
- **CA34** — Étant donné une candidature avec 50 pièces, quand le fragment d'une pièce est
  affiché, alors le nombre de requêtes SQL est identique (à une constante près fixée par le
  test) à celui d'une candidature avec 2 pièces.
- **CA35** — Étant donné un `nom_fichier` contenant `<script>alert(1)</script>.pdf`, alors le
  fragment l'affiche échappé et ne contient aucune balise `<script>` correspondante.

**Journalisation**

- **CA36** — Étant donné un aperçu réussi par un utilisateur `direction` (CA1), alors exactement
  une entrée `JournalAudit` est créée avec `action = "view"`, `actor` = l'utilisateur, objet = la
  pièce (`content_type` de `Piece`, `object_id` = id de la pièce), `object_repr = "Pièce #<id>
  (Candidature #<id>)"`, l'adresse IP, le navigateur et `metadata` contenant `candidature_id`,
  `piece_id`, `type_piece = "cv"`, `mode = "apercu"`, `resultat = "servie"`, `empreinte`,
  `type_detecte = "application/pdf"` et `taille`.
- **CA37** — Étant donné un téléchargement réussi, alors l'entrée porte `mode =
  "telechargement"`.
- **CA38** — Étant donné un candidat fictif « ALAOUI Salma », CIN `ZZ100037`, email
  `salma.alaoui1@example.com`, et sa pièce `cv_alaoui.pdf`, quand le contenu est consulté (succès,
  erreur ou refus), alors aucune entrée `JournalAudit` créée par la requête ne contient, dans
  `object_repr`, `metadata`, `before` ou `after`, les chaînes `alaoui`, `salma`, `ZZ100037`,
  `example.com`, `cv_alaoui` ni `fixture://` (insensible à la casse).
- **CA39** — Étant donné une requête sur le fragment de visionneuse (succès), alors aucune
  entrée `JournalAudit` n'est créée.

**Interface**

- **CA40** — Étant donné la langue active `ar`, quand la fiche puis le fragment sont affichés,
  alors l'élément `<html>` de la fiche porte `dir="rtl"`, les réponses sont 200, le nom du
  fichier est dans un élément `<bdi>`, et les flèches de navigation portent la classe
  `flip-rtl`.
- **CA41** — Étant donné le gabarit du fragment, alors il ne contient ni attribut `style`, ni
  attribut `on…=` (gestionnaire en ligne), ni `<script>` en ligne, ni classe `ml-`, `mr-`,
  `pl-`, `pr-`, `left-`, `right-`, `text-left`, `text-right`.
- **CA42** — Vérification manuelle (documentée dans la PR, captures à l'appui) : sur Chrome,
  Firefox, Edge et Safari récents, en bureau et en largeur mobile (375 px), le CV fictif PDF
  s'affiche dans la visionneuse, la navigation entre pièces fonctionne, « Ouvrir dans un nouvel
  onglet » affiche la pièce, et la console ne signale aucune violation CSP sur la fiche. Si un
  navigateur refuse l'aperçu avec la CSP de la règle 11, l'ajustement retenu est le plus petit
  possible, documenté dans la PR et soumis au relecteur conformité.

## Cas limites et erreurs

- **Candidature sans pièce** : pas de bouton « Consulter » ; « Aucune pièce reçue » (KAN-11).
- **Une seule pièce** : ni précédente ni suivante ; « Pièce 1 sur 1 ».
- **`content_type` déclaré vide** : fragment en mode « Aperçu non disponible » + téléchargement ;
  le téléchargement sert le type détecté s'il est affichable, sinon `application/octet-stream`.
- **`content_type` déclaré image mais contenu PDF** (ou l'inverse) : le contenu est servi avec le
  type **détecté** ; dans un `<img>`, un PDF ne s'affiche pas : le fragment propose toujours
  « Ouvrir dans un nouvel onglet » et « Télécharger ».
- **PDF dont `%PDF-` n'est pas en tête** mais dans les 1 024 premiers octets : affichable
  (tolérance usuelle des lecteurs) ; au-delà : non affichable.
- **SVG ou HTML déclarés en image** : jamais servis en ligne (règle 8) ; téléchargement en
  `application/octet-stream`.
- **Pièce supprimée entre l'affichage de la fiche et le clic** (synchronisation, purge CNDP) :
  404 ; le fragment ou l'iframe affiche le message d'erreur.
- **Synchronisation en cours** pendant la consultation : la pièce lue est celle de la source au
  moment de la requête ; une divergence d'empreinte déclenche la règle 15.
- **Consultations simultanées** de la même pièce par plusieurs utilisateurs : une entrée de
  journal par requête ; au plus une anomalie par empreinte constatée (verrou, règle 16).
- **Rechargement de l'iframe** (F5, retour arrière) : nouvelle requête, nouvelle entrée de
  journal (comportement voulu : chaque lecture est tracée).
- **Volumes** : 50 pièces (CA34) ; pièce de taille maximale : mémoire bornée par
  `PIECE_VIEWER_MAX_BYTES` × nombre de requêtes simultanées par processus (à surveiller avec les
  sources réelles).
- **Source lente** : délai `PIECE_SOURCE_TIMEOUT` → 503 (pour les sources réseau futures).
- **Session expirée** pendant la consultation : la requête de contenu est redirigée vers la
  connexion ; dans l'iframe, la page de connexion ne s'affiche pas (`X-Frame-Options: DENY`) et
  l'utilisateur recharge la fiche.
- **Droits insuffisants** sur l'une ou l'autre vue appelée directement : 403 (CA4).
- **Concours « Terminé »** : la consultation reste permise (recours).

## Traçabilité et conformité

- **Journal d'audit** : chaque lecture de contenu (aperçu ou téléchargement, succès ou erreur)
  produit une entrée `view` sur l'objet `Piece` avec un libellé sans nom « Pièce #id
  (Candidature #id) » et des métadonnées techniques (règle 22). Les refus de droits et les
  incohérences pièce / candidature sont tracés (règle 23). La création d'une anomalie
  d'empreinte produit une entrée `create`. L'écran de consultation de ces entrées relève de
  **KAN-14** ; KAN-12 fournit le point d'accroche (appel unique à `log` dans la vue de contenu).
  Le journal reste en ajout seul (modèle, admin, trigger PostgreSQL existants).
- **Données personnelles** : le contenu des pièces (CV, diplômes, attestations, lettres) contient
  des données personnelles et parfois des photos ; il n'est montré qu'aux rôles `admin` et
  `direction`. Minimisation : lecture à la demande, aucune copie permanente (ni disque, ni cache,
  ni base), `Cache-Control: no-store`, aucune URL directe vers Spaces, aucun nom, CIN, email,
  téléphone, `nom_fichier` ou `storage_key` dans le journal. Le téléchargement crée une copie
  sur le poste de l'utilisateur : il est tracé distinctement (`mode = telechargement`) ; la
  politique de poste de travail (stockage des fichiers téléchargés) relève de Mafoa (voir Q8).
- **Loi 09-08 / CNDP** : la finalité reste le traitement du recrutement ; une pièce purgée
  (durée de conservation) n'est plus accessible (404). Le transfert hors Maroc (Spaces à
  Francfort) est inchangé par cette story.
- **IA** : aucune. La visionneuse n'envoie aucun contenu à l'API Claude ; l'extraction
  (texte, vision) relève de l'epic E3.
- **Humain dans la boucle** : la visionneuse est une aide à la vérification ; l'anomalie
  d'empreinte ne modifie ni le statut, ni l'éligibilité, et n'élimine personne.
- **Biais** : la visionneuse n'ajoute aucun critère ; une photo éventuellement présente sur un
  CV est montrée telle quelle à l'humain mais n'est ni extraite, ni stockée, ni utilisée comme
  critère. Aucun texte alternatif ne décrit la personne (CA32).
- **Sécurité** : permissions côté serveur sur les deux vues, type détecté sur les octets,
  `nosniff`, CSP restrictive sur le contenu, dérogation au cadrage limitée à `SAMEORIGIN` pour
  la seule vue de contenu, aucun contenu actif servi en ligne (HTML, SVG). Risque résiduel
  accepté : un PDF piégé est rendu par le lecteur PDF du navigateur (voir Q11).

## Hors périmètre

- Écran de consultation du journal des consultations et filtres (KAN-14).
- Extraction du texte des pièces, OCR, vision Claude, surlignage d'extraits dans la visionneuse
  (epic E3).
- Annotations, zoom ou rotation applicatifs, lecteur PDF intégré (pdf.js) ou conversion des
  pages en images côté serveur.
- Aperçu des formats Office, TIFF, HEIC, WebP, GIF (téléchargement seulement).
- Résolution des anomalies (story dédiée).
- Correction de la synchronisation qui réécrit `sha256` sans signalement (Q12, ticket à créer).
- Téléchargement groupé (ZIP) des pièces d'un dossier ou d'un poste ; exports.
- Implémentation de la lecture Spaces (`DatabaseSource` / `ApiSource`, epic E7) : seule
  l'extension de l'interface (`max_bytes`, délai) est prévue ici.
- Détection d'usages anormaux (nombre de consultations par utilisateur) : vague ultérieure.

## Questions ouvertes

Toutes tranchées le 2026-11-23 : la proposition par défaut est retenue pour chaque question.

- [x] **Q1 — Servir les pièces par l'application ou par URL présignée Spaces ?** *Proposition par
  défaut* : par l'application (proxy), car une URL présignée contourne permissions et journal,
  peut être partagée pendant sa validité et expose le bucket. *Conséquence* : charge mémoire et
  réseau sur l'application, bornée par la taille maximale (Q4).
- [x] **Q2 — Empreinte différente : bloquer l'aperçu ou afficher avec un bandeau ?**
  *Proposition par défaut* : bloquer l'aperçu en ligne (409), laisser le téléchargement, créer
  une anomalie haute. Le document ne correspond plus à celui reçu et évalué : l'humain doit le
  savoir avant de s'y fier. *Alternative* : afficher avec un bandeau, plus fluide mais un
  utilisateur pressé peut passer à côté.
- [x] **Q3 — Nouveau type d'anomalie ou type « autre » ?** *Proposition par défaut* : nouveau type
  `empreinte_differente` (migration de choix sans impact sur les données), pour pouvoir filtrer
  et compter. *Alternative* : `autre` avec description normalisée, sans migration.
- [x] **Q4 — Taille maximale** : *Proposition par défaut* : 20 Mo, configurable par
  `PIECE_VIEWER_MAX_BYTES`. Un scan de diplôme dépasse rarement 10 Mo. *Conséquence* : une pièce
  plus lourde n'est ni affichable ni téléchargeable depuis l'application (à demander au site).
- [x] **Q5 — Types affichables** : *Proposition par défaut* : PDF, PNG, JPEG ; tout le reste en
  téléchargement. *Alternative* : ajouter WebP et GIF (risque faible) ; TIFF et HEIC ne sont pas
  pris en charge par la plupart des navigateurs.
- [x] **Q6 — Journaliser l'ouverture du fragment de visionneuse ?** *Proposition par défaut* :
  non ; seule la lecture du contenu est une consultation de pièce (évite les doublons dans le
  journal). *Alternative* : journaliser aussi le fragment (`metadata.vue = "fragment"`).
- [x] **Q7 — Journaliser les refus (403, pièce hors candidature) ?** *Proposition par défaut* :
  oui, comme KAN-13 (signal d'un usage anormal), sans données nominatives.
- [x] **Q8 — Téléchargement autorisé à la direction ?** *Proposition par défaut* : oui pour
  `admin` et `direction`, tracé distinctement. *Alternative* : réserver le téléchargement à
  `admin`, la direction n'ayant que l'aperçu (limite les copies hors de l'outil).
- [x] **Q9 — Fichier introuvable à la source : créer une anomalie automatiquement ?**
  *Proposition par défaut* : non (l'erreur peut être passagère : synchronisation, clé
  renommée) ; message et journal uniquement. La détection des pièces manquantes relève de
  l'epic E3. *Alternative* : anomalie « pièce manquante » de gravité moyenne.
- [x] **Q10 — Emplacement de la visionneuse** : la carte « Pièces » est dans la colonne étroite
  de droite. *Proposition par défaut* : conserver l'emplacement `#visionneuse` existant, hauteur
  ≥ 60 % de la fenêtre, avec « Ouvrir dans un nouvel onglet » pour une lecture confortable ; le
  designer peut passer la visionneuse en pleine largeur sans changer le comportement.
- [x] **Q11 — Lecteur PDF natif du navigateur ou conversion côté serveur ?** *Proposition par
  défaut* : lecteur natif (simple, rapide) avec CSP restrictive ; risque résiduel accepté
  (PDF piégé interprété par le lecteur du navigateur). *Alternative* : conversion des pages en
  images côté serveur (plus sûr, plus coûteux, vague ultérieure).
- [x] **Q12 — La synchronisation réécrit `sha256` à chaque passage** (constat). *Proposition par
  défaut* : hors KAN-12 ; créer un ticket pour que la synchronisation ne remplace plus une
  empreinte existante mais signale le changement (même anomalie `empreinte_differente`).
  *Conséquence* : d'ici là, la vérification de KAN-12 ne détecte que les modifications
  survenues depuis la dernière synchronisation.
- [x] **Q13 — Renseigner `sha256` vide lors de la première consultation ?** *Proposition par
  défaut* : non ; l'empreinte de référence doit venir de la réception (synchronisation), sinon
  un fichier déjà modifié deviendrait la référence. La visionneuse reste en lecture seule sur
  `Piece`.
- [x] **Q14 — Nom du fichier proposé au téléchargement** : `nom_fichier` du site (qui contient le
  nom du candidat) ou nom neutre ? *Proposition par défaut* : `nom_fichier` assaini, plus utile
  à l'utilisateur ; il n'est jamais écrit dans le journal. *Alternative* : nom neutre
  `candidature-<id>-<type>.<ext>`.
