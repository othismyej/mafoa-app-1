# KAN-25 — Cartographie des objets publics de mafoamanagement.com

Relevé du 27/11/2026, ~40 requêtes sur des pages publiques uniquement. Aucune page connectée, aucun CV ni pièce téléchargés. Les pages `/e-concours/result/<uuid>/` redirigent vers la connexion (302 → `/accounts/login/?next=…`) et n'ont pas été consultées. Aucune donnée personnelle de candidat n'est reproduite ici.

Correspondance : modèles de `apps/recruitment/models.py` (dépôt mafoa-app-1). Les noms de champs du site entre `code` viennent des paramètres GET des filtres (django-filter) et des formulaires, donc probablement des vrais noms de champs. Les autres sont déduits de l'affichage.

## 0. Indices techniques

| Indice | Observation | Conclusion |
|---|---|---|
| Filtres `title__icontains`, `location__icontains`, `bundle__etablissement`, `?page=N` | listes des offres et des concours | django-filter + pagination Django (10 par page) |
| `/accounts/signup/` : `email`, `email2`, `password1`, `password2`, `captcha_0`/`captcha_1`, image `/captcha/image/<hash>/` | formulaire d'inscription | django-allauth, connexion par email seul (sans username), email saisi deux fois, django-simple-captcha |
| `Content-Language: fr`, `Vary: Accept-Language, Cookie`, `data-langcode="fr"` | en-têtes | LocaleMiddleware actif ; seul `fr` est visible |
| `sitemap.xml` ne contient que `https://example.com/` | sitemap | django.contrib.sitemaps + Sites framework laissé sur `example.com` (à signaler au développeur) |
| `robots.txt` vide | — | — |
| `class="ck-content"` | descriptions | django-ckeditor-5 : descriptions en HTML riche |
| Statiques et médias sur `mafoamanagement.fra1.digitaloceanspaces.com/static/…` et `/media/…` | — | django-storages (S3) sur DigitalOcean Spaces confirmé |
| `Server: nginx/1.24.0 (Ubuntu)`, HSTS preload, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` | en-têtes | serveur Ubuntu derrière nginx (gunicorn probable), réglages de sécurité Django activés |
| Postuler : `GET /user/condidature/?apply=<pk offre>` ou `?concour=<slug poste>` | boutons « Postuler » | app Django `user`, vue `condidature` (orthographe du site). Candidature réservée aux connectés |
| Préfixes Spaces : `media/company_logos/`, `media/concour_bundles/`, `media/concour_avis/<slug poste>/<uuid>.pdf`, `media/logos/` | images et liens | `upload_to` probables des `FileField`/`ImageField` |

## 1. Référentiels (listes de valeurs)

### Secteur (`sector`) : modèle à part, clé entière
40 valeurs, identifiants 1 à 54 avec des trous (5, 14, 18, 22, 24, 25, 27, 28, 31, 32, 40, 46 à 48 absents, donc supprimés ou masqués). Exemples : `1` Aéronautique / Spatial, `9` BTP / Génie Civil, `23` Informatique / Telecom / Internet / Multimédia, `29` RH / Recrutement / Intérim, `30` Service public / Administration, `35` Transport / Messagerie / Logistique, `50` Autres services, `51` Hydraulique, `54` Gros Œuvres. Liste complète : 1 Aéronautique / Spatial · 2 Agence pub / Marketing Direct · 3 Agriculture / Environnement · 4 Agroalimentaire · 6 Audiovisuel · 7 Automobile / Motos / Cycles · 8 Banque / Assurance · 9 BTP / Génie Civil · 10 Centre d'appel · 11 Chimie / Parachimie / Peintures · 12 Comptabilité/ Finance / Audit · 13 Conseil / Etudes · 15 Edition / Imprimerie · 16 Enseignement / Formation · 17 Energie / Pétrole / Gaz · 19 Ferroviaire · 20 Hôtellerie / Restauration · 21 Immobilier / Promoteur / Agence · 23 Informatique / Telecom / Internet / Multimédia · 26 Pharmacie / Santé · 29 RH / Recrutement / Intérim · 30 Service public / Administration · 33 Textile / Cuir · 34 Tourisme / Voyage / Loisirs · 35 Transport / Messagerie / Logistique · 36 Ameublement / Décoration · 37 Presse / Communication / Evénementiel · 38 Cosmétique / Parfumerie / Luxe · 39 Electronique / Electricité / Electromécanique / Mécanique · 41 Import / Export / Négoce · 42 Juridique / Notariat · 43 Plasturgie / Métallurgie / Sidérurgie · 44 Nettoyage / Sécurité / Gardiennage · 45 Offshoring / Nearshoring · 49 Autres Industries · 50 Autres services · 51 Hydraulique · 52 Architecture / Design Intérieur · 53 Geographie · 54 Gros Œuvres.
Chez nous : `secteur` est un `CharField` libre (150). On peut stocker le libellé, mais il faut garder l'id du site pour la synchronisation.

### Fonction (`function`) : modèle à part, clé entière 40 à 52
| id site | Libellé site | Notre `Fonction` |
|---|---|---|
| 40 | Directeur (trice) | `directeur` |
| 41 | Chef (fe) de département | `chef_departement` |
| 42 | Chef (fe) de division | `chef_division` |
| 43 | Chef (fe) de service | `chef_service` |
| 44 | Responsable | `responsable` |
| 45 | Ingénieur (e) | `ingenieur` |
| 46 | Architecte | `architecte` |
| **47** | **Cadre** | **absent** |
| **48** | **Employé (e)** | **absent** (utilisé par l'offre publiée en ce moment) |
| 49 | Technicien (ne) | `technicien` |
| 50 | Agent | `agent` |
| 51 | Ouvrier (e) | `ouvrier` |
| 52 | Ingénieur / Cadre | `ingenieur_cadre` |

### Niveau d'études (`required_education_level`) : choix texte (le code brut fuit parfois dans l'affichage)
| Code site | Libellé | `parse_niveau()` actuel | Remarque |
|---|---|---|---|
| `SANS` | SANS | `None` | aucune exigence ; pour un **poste** cela doit valoir « pas de minimum », pas « inconnu » |
| `QUALIFICATION_AVANT_BAC` | Qualification avant Bac | **`BAC` (0)** | **faux : niveau inférieur au Bac** |
| `NIVEAU_BAC` | Niveau Bac | **`BAC` (0)** | « niveau » = Bac non obtenu : inférieur au Bac |
| `Bac` | Bac | `BAC` | ok |
| `Bac+2` | Bac +2 | `BAC_2` | ok |
| `NIVEAU_BAC+2` | Niveau Bac +2 | **`BAC_2`** | « niveau » = Bac+2 non validé : inférieur au Bac+2 |
| `Bac+3` | Bac +3 | `BAC_3` | ok |
| `Bac+5` | Bac +5 | `BAC_5` | ok |
| `Plus Bac+5` | Plus Bac +5 | `BAC_5` | acceptable (au-dessus de Bac+5, sans Doctorat explicite) |
| *(vide)* | affiché « None » | `None` | champ nullable (2 postes de SRM-LS 2026 Agents) |
Pas de `Bac+4` ni de `Doctorat` sur le site. Il faut un mapping explicite code site → `NiveauEtudes` dans la couche d'intégration, avec un niveau « infra-Bac » ou un traitement « à vérifier », au lieu de l'analyse en texte libre.

### Offres : type de contrat (`job_type`), mode de travail (`remote_work`), expérience (`experience_level`)
| Champ site | Codes → libellés | Chez nous |
|---|---|---|
| `job_type` | `CDI`, `CDD`, `INTERNSHIP` → Stage, `INTERIME` → Intérim | `OffreEmploi.type_contrat` CharField libre : prévoir des choix |
| `remote_work` | `NO` → Présentiel, `YES` → Télétravail, `HYBRID` → Hybride | `ModeTravail` (`presentiel`/`teletravail`/`hybride`) : mapping 1-1 |
| `experience_level` | `0` Aucune expérience, `<1` Moins d'un an, `1-2`, `3-5`, `5-10`, `>10` | **tranches, pas des années** → à convertir en `experience_min/max_annees` (ex. `3-5` → 3/5, `>10` → 10/None). Les bornes se recoupent (5 dans `3-5` et dans `5-10`) |

### Statut et étapes de concours
- Statut affiché d'un poste : « Postulation Terminée » (seule valeur vue, tous les postes sont clos). Il est probablement **calculé** à partir de la date limite ; l'état ouvert n'a pas été vu.
- **Étapes** (frise chronologique, une date chacune, une marquée « Étape Actuelle ») : `Candidature`, `Convocation pour l'examen écrit`, `Convocation pour l'entretien oral`, `Résultats`. Les postes 2026 récents n'ont que « Candidature ». Ceux de RADEEL/Dakhla 2025 ont les 4 étapes.
- **Publications de résultats** (liste séparée, compteur « Résultats N ») : objets à **clé UUID**, URL `/e-concours/result/<uuid>/` **protégée par connexion**. Libellés vus : « Liste Des Convoqués pour l'examen écrit », « Liste des candidats convoqués pour l'oral », « Résultats définitifs », « Résultats définitifs par ordre de mérite ». Chacune a une date. Une même publication (même UUID) est rattachée au poste ; on ne sait pas si elle l'est au poste ou au concours.

## 2. Établissement (organisation publique) — `bundle__etablissement`

| Champ site | Exemple | Type probable | Notre modèle |
|---|---|---|---|
| id | `9b5a58bc-bfce-4a31-90b2-ab87169d6fd8` | UUID (pk) | `Organisation.source_id` (CharField 100 : ok) |
| nom | Société Régionale Multiservices Laâyoune – Sakia El Hamra | CharField | `Organisation.nom` |
| logo (probable) | `media/company_logos/SRM_LS.jpg` | ImageField | absent (pas nécessaire) |
| nombre de postes (page « Nos Établissements Partenaires ») | TTA : 151 = 20+20+111 | calculé (somme des concours actifs) | — |
| type public/privé | non affiché | ? | `Organisation.type` : à déduire (tout établissement de concours = public) |
| ville | non affichée | ? | `Organisation.ville` |

Établissements listés : SRM Dakhla-Oued Eddahab, SRM Tanger-Tétouan-Al Hoceima, SRM Laâyoune–Sakia El Hamra, La SRM Rabat-Salé-Kénitra, SRM Souss-Massa, RADEEL Larache, **« TEST COMPANY »** (aucun poste public : donnée de test en production).
La section « Informations Sur L'Établissement » de la fiche poste est vide : aucun champ public (adresse, description…).

## 3. Concours (« concour bundle ») — `/e-concours/<slug>/`

| Champ site | Exemple | Type probable | Notre `Concours` |
|---|---|---|---|
| slug | `srm-ls-2026-a`, `srm-tta-2026-r3` | SlugField saisi à la main, **non régénéré** (`srm-tta-2026-r3` porte le titre « SRM-TTA 2026 (2) ») | `slug` |
| titre | SRM-LS 2026 (Agents) | CharField | `intitule` |
| etablissement | → Établissement | FK (UUID) | `organisation` |
| nombre de postes | 117 | **entier stocké** (117 affiché, somme des postes = 116 ; TTA 2026 (2) : 20 contre 19) | `nombre_postes_total` est **calculé** chez nous |
| description | « Pour accompagner ses activités, la SRM-LS envisage… NB : La candidature ne peut être effectuée que pour un seul poste [et une seule affectation à la fois]. » | texte riche | `description` |
| logo | `media/concour_bundles/SRM_LS_2.jpg` | ImageField (`upload_to="concour_bundles/"`) | absent |
| visible / actif | 7 concours de 2026 listés ; ceux de 2025 (RADEEL, Dakhla) et RSK 01/2026 ont des postes mais pas de page concours listée | BooleanField probable (`is_active`/`published`) | `statut` (5 valeurs) : rien d'équivalent côté site |
| dates | non affichées au niveau concours | ? (portées par les postes) | `date_publication`, `date_limite` : à dériver des postes (min/max) |
| identifiant interne | non exposé (accès par slug) | ? | `source_id` : utiliser le slug tant que la pk est inconnue |

Le dossier `concour_bundles/` ne contient, publiquement, que des logos. L'avis de concours est rattaché au **poste** (voir ci-dessous), pas au concours.

## 4. Poste de concours (« concour ») — `/concour/<slug>/`

Forme de l'URL : `<slug de l'établissement>-<slug du titre>-<suffixe à 4 chiffres>`, ex. `societe-regionale-multiservices-laayoune-sakia-el-hamra-juriste-direction-affaires-juridiques-1777`. **Le suffixe n'est pas la pk** : `/concour/x-1777/` renvoie 404, la recherche se fait sur le slug complet. C'est probablement un nombre aléatoire ajouté pour l'unicité. Le bouton « Postuler » transmet le **slug** (`?concour=<slug>`). Environ 420 postes publiés depuis avril 2025 (42 pages de 10).

| Champ site | Exemple | Type probable | Notre `PosteConcours` |
|---|---|---|---|
| slug | voir ci-dessus | SlugField unique | `slug` (max 255 : les slugs SRM-SM dépassent 150 caractères, ok) |
| bundle | → concours | FK (`bundle`) | `concours` |
| titre | Juriste (Direction Affaires Juridiques) | CharField ; **l'affectation (direction, province) est dans le titre entre parenthèses** | `intitule` ; `ville` : à extraire du titre (« Direction Provinciale SMARA ») |
| secteur | Service public / Administration (parfois Informatique, Transport, Autres services chez RADEEL) | FK Secteur | `secteur` (texte) |
| fonction | Agent, Responsable | FK Fonction (40-52) | `fonction` (choix ; 47 et 48 manquent) |
| niveau requis | Bac +3, `QUALIFICATION_AVANT_BAC`, vide | choix texte nullable | `niveau_etudes_min` (mapping à écrire, voir §1) |
| nombre de postes | 4 | PositiveInteger | `nombre_postes` |
| mode de travail | Présentiel | choix `remote_work` | `mode_travail` |
| date de publication | 26/10/2026 | DateField (ou DateTime) | `date_publication` |
| date limite | 10/11/2026 **23:00** (heure absente sur les postes RADEEL 2025) | **DateTimeField** | `date_limite` est un **DateField** chez nous : l'heure se perd (impact sur le calcul d'expérience à la date limite : faible ; sur la clôture : réel) |
| description | « Licence en Droit Privé ou équivalent » / « Bac+5 en SI, data sciences… avec expérience de 3 à 5 ans. » | texte riche | `description` ; **les critères (spécialité, expérience) sont en texte libre** → `specialites_acceptees`, `experience_min/max_annees` à extraire (IA + validation humaine) ou à saisir |
| missions | « Voir l'avis de recrutement joint » (presque partout) | texte riche | `missions` |
| conditions | idem | texte riche | **absent** (→ `autres_criteres` ?) |
| documents requis | idem | texte riche | **absent** : utile pour l'anomalie « pièce manquante » (cas d'usage 2) |
| avis de concours | `media/concour_avis/<slug>/<uuid>.pdf` (2026) ou `media/concour_avis/Décision_de_recrutement.pdf` (2025, nom d'origine) | FileField, `upload_to` dynamique | **absent** : la vraie source des critères est ce PDF (à lire par l'app) |
| statut | Postulation Terminée | calculé (date limite) | rien côté poste chez nous |
| étapes | Candidature 26/10/2026 (étape actuelle) | modèle enfant (libellé de type choix + date + indicateur « actuelle ») | **absent** |
| résultats | « Liste des candidats convoqués pour l'oral » 03/09/2026 → `/e-concours/result/<uuid>/` | modèle enfant à pk UUID (titre, date, fichier ou liste ; accès réservé) | **absent** (correspond à nos exports, cas d'usage 4) |
| règle | « Vous ne pouvez postuler qu'à un seul poste dans ce concours » (+ « une seule affectation à la fois » pour TTA/SM) | contrainte applicative | `uniq_candidature_un_poste_par_concours` : ok |
| référence | aucune visible | — | `reference` : pas de source sur le site |

## 5. Offre d'emploi — `/job/<slug>-<n>/`

Une seule offre publiée : `/job/employe-e-de-bureau-40895/`. Le suffixe `40895` **n'est pas la pk** : le bouton « Postuler » envoie `?apply=20`, et `/job/x-40895/` comme `/job/employe-e-de-bureau-20/` renvoient 404. La recherche se fait sur le slug complet. Le compteur « 2,770+ offres » de l'accueil est du marketing.

| Champ site | Exemple | Type probable | Notre `OffreEmploi` |
|---|---|---|---|
| pk | 20 | entier | `source_id` |
| slug | `employe-e-de-bureau-40895` | SlugField + suffixe aléatoire | `slug` |
| titre (`title`) | Employé (e) de Bureau | CharField | `intitule` |
| entreprise | logo `static/global/img/confidential.jpg`, alt « Confidential Company » | FK Entreprise + booléen « confidentielle » (ou entreprise générique) | `organisation` + `entreprise_confidentielle` |
| description de l'entreprise | « Cabinet de conseil RH de la Place » (texte anonymisé) | TextField sur l'offre ou l'entreprise | absent |
| lieu (`location`) | Marrakech - Maroc | CharField libre (ville + pays ; le filtre dit « Région ») | `ville` (pays à séparer) |
| `job_type` | CDI | choix | `type_contrat` |
| `remote_work` | NO → Présentiel | choix | `mode_travail` |
| `experience_level` | `1-2` « De 1 à 2 ans d'experience » | choix (tranche) | `experience_min/max_annees` (conversion) |
| niveau d'études (« Education ») | Bac +3 | même choix que les concours probablement | `niveau_etudes_min` |
| `sector` | RH / Recrutement / Intérim | FK Secteur | `secteur` |
| `function` | Employé (e) | FK Fonction | `fonction` (**valeur 48 absente**) |
| salaire | « - » | CharField/Decimal nullable | `salaire` |
| nombre de postes | 1 | entier | `nombre_postes` |
| description | « nous recherchons un profil de Bac+2 ou Bac+3 en gestion/ Informatique/ Commerce… 1 à 3 ans… » | texte riche | `fiche_de_poste` |
| date de publication | 2026-01-13 (ISO dans le script) | DateField | `date_publication` |
| date limite | `""` dans le script du compte à rebours | DateField nullable | `date_limite` |
| statut | non affiché ; l'offre reste listée sans date limite | booléen actif probable | `statut` (ouverte/fermée/pourvue) |
| compétences | non affichées | — | `competences` : pas de source |

## 6. Comptes candidats, candidature, candidature spontanée

- **Inscription** (`/accounts/signup/`, « Bienvenue dans notre CVthèque ») : uniquement `email`, `email2` (confirmation), `password1`, `password2` (8 caractères min., validateur de similarité avec l'email) et un captcha. **Aucun champ d'identité à l'inscription** : nom, CIN, téléphone, date de naissance, formation et expériences sont saisis ensuite dans le profil (« Mon Profil »), non visible sans connexion.
- **Candidature à une offre** : `GET /user/condidature/?apply=<pk offre>`. **À un poste de concours** : `GET /user/condidature/?concour=<slug poste>`. Le formulaire (« remplir le formulaire et télécharger si nécessaire les documents requis », étape 3 sur `/e-concours/`) est derrière la connexion. Il n'existe aucun formulaire public.
- **Candidature spontanée** (`/candidature-spontanee/`) : page d'information seulement. Les boutons « Déposer Votre Candidature » et « Mon Profil » mènent à `/accounts/signup/`. **La candidature spontanée semble être le profil CVthèque lui-même** : il n'y a ni formulaire dédié, ni champs souhaits visibles. Cela touche l'hypothèse de `DetailsSpontanee` (KAN-39, QS2).
- **Pièces demandées** : « Documents Requis » renvoie à l'avis PDF du poste. La liste n'est pas structurée sur le site.

## 7. Écarts

### Présent sur le site, absent chez nous
1. Référentiels `Secteur` et `Fonction` avec ids entiers (nous : texte et choix). Il manque les fonctions **Cadre (47)** et **Employé (e) (48)**.
2. Codes de niveau `SANS`, `QUALIFICATION_AVANT_BAC`, `NIVEAU_BAC`, `NIVEAU_BAC+2`, `Plus Bac+5`, et la valeur nulle. `parse_niveau` en classe trois de façon **trop favorable** (avant-Bac → Bac, « niveau » → diplôme obtenu). C'est à corriger par un mapping explicite, sinon risque d'éligibilité erronée.
3. Poste : `conditions`, `documents requis`, **avis PDF** (`concour_avis/`), **étapes** datées avec étape courante, **publications de résultats** (UUID).
4. Concours : logo, nombre de postes **saisi** (différent de la somme), indicateur de visibilité.
5. Date limite **avec heure** (DateTime) sur les postes.
6. Offre : tranche `experience_level`, description d'entreprise, lieu « ville - pays », pk distincte du suffixe du slug.
7. Établissement : identifiant **UUID**.

### Présent chez nous, sans source publique sur le site
1. `PosteConcours.reference`, `PosteConcours.ville` (l'affectation n'est que dans le titre), `specialites_acceptees`, `experience_min/max_annees`, `autres_criteres` : tout cela est en texte libre dans la description ou dans l'avis PDF.
2. `Concours.statut` (5 états), `Concours.date_publication/date_limite`.
3. `Organisation.type`, `Organisation.ville`.
4. `OffreEmploi.competences`, `OffreEmploi.statut`.
5. Tout `Candidat`, `Candidature`, `Piece`, `DetailsSpontanee` : rien de public (normal).

### Conséquences pour `SiteSource`
- Clé de rapprochement : la pk des offres (entier) est exposée via `?apply=`, celle des postes ne l'est pas (on n'a que le slug). Le `source_id` des postes et des concours sera **le slug** tant que la pk n'est pas connue. Il faudra une migration de clés lors du passage à `DatabaseSource`.
- `FixtureSource` doit refléter ces formats : slugs avec suffixe aléatoire, codes de niveau du site, tranches d'expérience, établissements à UUID.

## 8. Ce qui reste inconnu (admin ou code source seulement)

**Candidatures** (app `user`, vue `condidature`)
1. Nom du modèle et champs : lien poste ou offre, date, statut interne, champs du formulaire, réponses à des questions éventuelles.
2. Liste des statuts internes (présélection, convoqué à l'écrit ou à l'oral, retenu…) et lien avec les publications de résultats.
3. Mise en œuvre de « un seul poste par concours » : contrainte en base ou contrôle dans la vue ? Que signifie « une seule affectation à la fois » ?
4. Possibilité de modifier ou retirer une candidature avant la date limite.

**Comptes et profils candidats (CVthèque)**
5. Modèle de profil : identité (nom, prénom, CIN, date de naissance, sexe, ville/région, téléphone), formations (diplôme, spécialité, établissement, dates), expériences (employeur, poste, dates). Ces champs sont la base de `donnees_declarees` et du calcul d'expérience.
6. Candidature spontanée : simple profil complet, ou objet distinct ? Champs « souhaits » (fonction, secteur, lieu, disponibilité), consentement, date de mise à jour (QS2 à QS4 de KAN-39).
7. Comptes allauth : vérification de l'email obligatoire ? Lien `User` ↔ profil (1-1 ?).

**Pièces**
8. Modèle(s) de pièce : rattachement au profil ou à la candidature, types (CV, diplôme, attestation, CIN…), formats et tailles acceptés, obligatoires ou non selon le poste.
9. Chemins dans Spaces (`upload_to` des CV et pièces), ACL (publiques ou privées / URL signées), noms de fichiers.

**Concours et postes**
10. Pk réelles des postes et des concours, nom des modèles (`Concour`, `ConcourBundle`, `Etablissement` ?), règle de génération du suffixe de slug.
11. Champ de visibilité ou statut des concours ; calcul du statut « Postulation terminée » ; fuseau des dates limites (23:00 affiché : 23:59 local ou 00:00 UTC ?).
12. Modèle des étapes (liste de types fixe ?) et des résultats (fichier PDF ou liste nominative générée ? rattachement poste ou concours ?), contenu des pages `/e-concours/result/<uuid>/`.
13. Champs non affichés : spécialités, expérience min./max. structurées (existent-elles ?), référence de poste, ville d'affectation.
14. Nombre de postes du concours saisi à la main (117 contre 116 : erreur de saisie ou poste masqué ?).

**Offres et entreprises**
15. Modèle Entreprise (nom réel, logo `company_logos/`, secteur), mécanisme « Confidential Company » (booléen ou entreprise fictive), statut ou expiration des offres, historique des offres (pk 20 mais compteur public « 2 770+ »).

**Infrastructure**
16. Version de Django et d'allauth, SGBD et hébergement, fuseau `TIME_ZONE`, configuration du Sites framework (`example.com`), présence de DRF.
17. Le bucket Spaces autorise-t-il le listing public ? Non testé ici (refusé par la politique de la session). À vérifier avec le développeur : si c'est le cas, les CV seraient énumérables.
