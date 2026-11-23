# Charte graphique — Mafoa AI

> Source de vérité de l'identité visuelle de l'application (story KAN-32). Toute évolution
> visuelle met à jour ce document, `tailwind.config.js` et `frontend/app.css`.

## 1. Intention

Une application interne **élégante, sobre et rassurante**, que l'on reconnaît immédiatement
comme appartenant à Mafoa Management (site mafoamanagement.com) : fond blanc et gris très
clair, anthracite pour le texte, **rouge Mafoa en accent** (actions principales, élément actif,
liserés), typographies Poppins et Roboto.

Trois règles d'or :

1. **Le rouge est un accent, jamais un statut.** Aucun statut métier, aucune erreur n'utilise
   le rouge de marque : « non éligible » ou « rejetée » ne doivent pas ressembler à un bouton.
2. **Le texte porte le sens** : couleur et pastille ne font que renforcer un libellé toujours
   visible (WCAG 1.4.1).
3. **Explicabilité visible** : verdicts, raisons, extraits et sources sont lisibles sans clic.

## 2. Palette

### 2.1 Marque

| Token Tailwind | Valeur | Usage |
|---|---|---|
| `brand-600` | `#d01c2b` | Bouton principal, élément de navigation actif, liseré supérieur des cartes d'en-tête, focus |
| `brand-700` | `#b11825` | Survol du bouton principal, liens, sur-titres |
| `brand-800` | `#a61622` | État pressé, texte sur `brand-50` |
| `brand-50` / `brand-100` / `brand-200` | `#fdf3f4` / `#fae1e3` / `#f3b9be` | Fond de l'élément actif, filtre coché, sélection |
| `brand-logo` | `#bc3735` | Rouge du logo (référence ; ne pas utiliser pour l'interface) |

### 2.2 Neutres (anthracite, gris purs du site)

| Token | Valeur | Usage |
|---|---|---|
| `ink-50` | `#f8f9fa` | Fond de page (« light » du site), en-têtes de tableaux |
| `ink-100` | `#f1f3f5` | Fonds secondaires, survol de ligne, badges neutres |
| `ink-200` | `#e4e6e9` | Bordures de cartes et séparateurs (décoratifs) |
| `ink-300` | `#cfd2d6` | Bordures de pastilles de filtre |
| `ink-400` | `#8e9095` | Bordure des champs de saisie (≥ 3:1, WCAG 1.4.11) |
| `ink-500` | `#7a7b7e` | Gris du logo : icônes et décor uniquement, **jamais pour du texte courant** |
| `ink-600` | `#5c5d61` | Texte secondaire, libellés, aides |
| `ink-700` | `#474747` | Anthracite du logo |
| `ink-800` | `#424242` | Texte courant (« secondary » du site) |
| `ink-900` | `#262626` | Titres, valeurs clés |

### 2.3 Statuts métier (palettes Tailwind, distinctes du rouge)

Les badges sont produits par `templates/components/badge_statut.html` (classe `badge badge-<code>`)
ou `templates/components/verdict.html`.

| Statut / verdict | Classe | Couleurs (texte / fond / bordure) | Intention |
|---|---|---|---|
| Reçue | `badge-recue` | `ink-700` / `ink-100` / `ink-200` | Neutre, rien n'est encore fait |
| En analyse | `badge-en_analyse` | `sky-800` / `sky-50` / `sky-200` | Traitement en cours |
| Éligible | `badge-eligible` | `emerald-800` / `emerald-50` / `emerald-200` | Favorable |
| Non éligible | `badge-non_eligible` | `stone-800` / `stone-200` / `stone-300` | Défavorable, sobre (pas de rouge) |
| À vérifier | `badge-a_verifier` | `amber-800` / `amber-50` / `amber-300` | Attention humaine requise |
| Présélectionnée | `badge-preselectionnee` | `indigo-800` / `indigo-50` / `indigo-200` | Étape franchie |
| Convoquée | `badge-convoquee` | `violet-800` / `violet-50` / `violet-200` | Étape franchie (oral) |
| Retenue | `badge-retenue` | blanc / `emerald-700` (plein) | Issue finale favorable |
| Rejetée | `badge-rejetee` | blanc / `stone-700` (plein) | Issue finale défavorable |
| Conservée au vivier | `badge-vivier` | `teal-800` / `teal-50` / `teal-200` | Candidature spontanée qualifiée (KAN-39) : neutre-positif, distinct des statuts de concours |
| Non évaluée (verdict) | `badge-non_evaluee` | `ink-600` / blanc / `ink-300` pointillé | Absence de verdict |
| Décision en vigueur | `badge-en_vigueur` | `emerald-800` / `emerald-50` | Marqueur d'historique |

Statuts de concours : `badge-brouillon` (pointillé), `badge-ouvert` (émeraude),
`badge-cloture` (neutre), `badge-en_evaluation` (ciel), `badge-termine` (pierre).

Statuts d'offre d'emploi (KAN-38) : `badge-ouverte` (émeraude), `badge-fermee` (neutre),
`badge-pourvue` (pierre).

### 2.4 Gravité des anomalies

| Gravité | Classe | Couleurs | Liseré de carte |
|---|---|---|---|
| Haute | `badge-haute` | `orange-900` / `orange-100` | `border-s-orange-500` |
| Moyenne | `badge-moyenne` | `yellow-800` / `yellow-50` | `border-s-yellow-500` |
| Information | `badge-info` | `ink-700` / `ink-100` | `border-s-ink-400` |

Une anomalie résolue passe en neutre (`badge-info`, fond `ink-50`).

### 2.5 Alertes

| Ton | Classe | Couleurs |
|---|---|---|
| Succès | `alert-success` | `emerald-900` / `emerald-50` |
| Information | `alert-info` | `sky-900` / `sky-50` |
| Attention / erreur | `alert-warning` | `amber-900` / `amber-50` |

Il n'y a **pas** d'alerte rouge : une erreur de saisie est une demande d'attention, pas une
alarme, et le rouge reste celui de la marque.

## 3. Accessibilité (contrastes calculés, WCAG 2.1)

Calcul selon la formule de luminance relative WCAG. Seuils : 4,5:1 texte courant (AA),
3:1 grands textes et composants d'interface.

| Couple | Ratio | Résultat |
|---|---|---|
| `brand-600` sur blanc (liens, texte rouge) | 5,41 | AA |
| Blanc sur `brand-600` (bouton principal) | 5,41 | AA |
| Blanc sur `brand-700` (survol) | 6,94 | AA |
| Blanc sur `brand-800` (pressé) | 7,61 | AA |
| `brand-700` sur `brand-50` | 6,38 | AA |
| `brand-600` sur `ink-50` | 5,13 | AA |
| `ink-900` sur blanc | 15,13 | AA |
| `ink-800` sur blanc / sur `ink-50` | 10,05 / 9,53 | AA |
| `ink-600` sur blanc / `ink-50` / `ink-100` | 6,58 / 6,24 / 5,91 | AA |
| `ink-500` sur blanc (`#7a7b7e`) | 4,23 | **Échec texte** → réservé aux icônes et au décor (≥ 3:1) |
| `ink-400` (bordure de champ) sur blanc | 3,19 | AA composant |
| Reçue `ink-700` / `ink-100` | 8,35 | AA |
| En analyse `sky-800` / `sky-50` | 7,09 | AA |
| Éligible `emerald-800` / `emerald-50` | 7,29 | AA |
| Non éligible `stone-800` / `stone-200` | 12,08 | AA |
| À vérifier `amber-800` / `amber-50` | 6,84 | AA |
| `amber-700` sur blanc (date illisible) | 5,02 | AA |
| Présélectionnée `indigo-800` / `indigo-50` | 8,88 | AA |
| Convoquée `violet-800` / `violet-50` | 8,19 | AA |
| Retenue blanc / `emerald-700` | 5,48 | AA |
| Rejetée blanc / `stone-700` | 10,27 | AA |
| Conservée au vivier `teal-800` / `teal-50` | 7,27 | AA |
| Gravité haute `orange-900` / `orange-100` | 8,18 | AA |
| Gravité moyenne `yellow-800` / `yellow-50` | 6,62 | AA |
| Alerte info `sky-900` / `sky-50` | 8,87 | AA |
| Alerte attention `amber-900` / `amber-50` | 8,75 | AA |

Autres règles :

- Taille de texte minimale 12 px pour les métadonnées (`text-xs`), 14 px pour le texte courant.
- **Focus clavier toujours visible** : contour 2 px `brand-600`, décalé de 2 px (`:focus-visible`).
- Cibles cliquables ≥ 40 px de haut (`min-h-10`) pour la navigation, les boutons et le menu ;
  36 px pour les pastilles de filtre et les petits boutons.
- Lien d'évitement « Aller au contenu » en tête de chaque page.
- `prefers-reduced-motion` : transitions désactivées.

## 4. Typographie

| Rôle | Police | Graisse | Taille |
|---|---|---|---|
| Titre de page (`.page-title`) | Poppins | 600 | 24 px (28 px ≥ 768 px) |
| Sur-titre (`.page-eyebrow`) | Poppins | 600, majuscules, interlettrage large | 12 px, `brand-700` |
| Titre de section (`.section-title`) | Poppins | 600 | 16–18 px |
| Chiffre clé (`.stat-value`) | Poppins | 600 | 30 px |
| Texte courant | Roboto | 400 / 500 | 14 px (`text-sm`) |
| Métadonnées (`.meta`, `.hint`) | Roboto | 400 | 12 px, `ink-600` |
| En-têtes de tableau | Roboto | 600, majuscules | 12 px, `ink-600` |

Les chiffres des tableaux et des dates utilisent `tabular-nums`.

Polices **auto-hébergées** dans `static/fonts/` (woff2, sous-ensemble latin) :
Poppins 400/500/600/700 (SIL Open Font License 1.1) et Roboto 400/500/700 (Apache 2.0, version
diffusée par `@fontsource/roboto` 5.1.0). Les licences sont copiées à côté des fichiers.
Pour l'arabe, la pile de repli est `Noto Sans Arabic, Segoe UI, Tahoma` (polices système) :
le choix d'une police arabe auto-hébergée est à faire avec la vague « ar ».

## 5. Espacements, rayons, ombres

- Grille de 4 px (échelle Tailwind). Gouttières de page : 16 px (mobile), 24 px, 32 px (≥ 1024 px).
  Largeur de contenu maximale : 1280 px (`max-w-page`).
- Espacement vertical entre blocs : 24 px (`gap-6` / `space-y-6`) ; entre sections majeures : 40 px.
- Rayons : `rounded-card` 12 px (cartes, tableaux, alertes), `rounded-control` 8 px (boutons,
  champs), `rounded-full` (badges, pastilles, avatars).
- Ombres : `shadow-card` (très légère, cartes posées sur `ink-50`) et `shadow-raised`
  (menus, carte de connexion).

## 6. Composants

Les classes de composants sont définies dans `frontend/app.css` ; les fragments réutilisables
sont dans `templates/components/`.

| Composant | Classes / gabarit | Règles |
|---|---|---|
| Bouton principal | `.btn-primary` | Un seul par écran ou par formulaire. Rouge plein, texte blanc. |
| Bouton secondaire | `.btn-secondary` | Bordure `ink-400`, fond blanc. Réinitialiser, Précédente/Suivante. |
| Bouton discret | `.btn-ghost` | Actions tertiaires. |
| Petit bouton | `.btn-sm` | Combiné avec les précédents (pagination). |
| Badge de statut | `components/badge_statut.html` (`code`, `label`), `.badge .badge-<code>` | Pastille + libellé ; `.badge-plain` retire la pastille (gravité, compteur). |
| Verdict | `components/verdict.html` (`code`, `label`) | Éligible / Non éligible / À vérifier / Non évaluée. |
| Carte | `.card`, `.card-header`, `.card-body`, `.card-accent` | Blanche sur `ink-50` ; `.card-accent` = liseré rouge 4 px en haut, réservé à l'en-tête d'un objet (concours, candidature). |
| Chiffre clé | `components/stat.html` (`label`, `value`, `hint`, `icon`, `tone`, `href`, `cta`) | `tone="attention"` passe la carte en ambre si la valeur est non nulle. Avec `href`, toute la carte est un lien unique (`.stat-link`, survol discret, focus rouge) et `cta` affiche un appel « Voir … → » ; une carte qui contient plusieurs liens (ventilation) n'est pas elle-même un lien (KAN-38). |
| En-tête de page | `.page-eyebrow` + `.page-title` | Sur-titre = contexte (organisation, concours) ; un seul `h1`. |
| Fil d'Ariane | `components/breadcrumb.html` (`concours`, `poste`, `offres`, `spontanees`, `offre`, `current`, `current_poste`) | Toujours « Tableau de bord › … » ; page courante en `aria-current="page"`. |
| Navigation | `.nav-link` + `aria-current` | Élément actif : fond `brand-50`, texte `brand-700`, soulignement rouge 2 px. |
| Menu utilisateur | `<details class="user-menu">` | Sans JavaScript : avatar (initiale), nom, rôle, double authentification, déconnexion. |
| Tableau | `.table-wrap` + `.table-data` | En-têtes en petites capitales grises, lignes 48 px, survol `ink-50`, défilement horizontal sur mobile. |
| Formulaire | `.field-label`, `.field-input`, `.field-legend` | Libellé au-dessus, bordure `ink-400`, focus rouge + halo 20 %. |
| Filtre « pastille » | `.chip` (case ou bouton radio visible dans un `label`) | État coché : bordure et fond rouges légers (`:has(:checked)`). |
| Alerte | `components/alert.html` (`message`, `tone`, `role`), `.alert-*` | Icône + texte ; jamais rouge. |
| Messages Django | `components/messages.html` | `error` et `warning` → ton « attention ». |
| Sélection de lignes | `.lot-case` (cible 36 px) + `.lot-check` | Case par ligne et « tout sélectionner » (actions en lot, KAN-15) ; nom accessible « Sélectionner NOM Prénom ». |
| Panneau d'action en lot | `recruitment/_lot_panneau.html` | Carte à liseré anthracite ; avertissements en `fieldset` ambre avec choix radio sans présélection ; un seul `.btn-primary`. |
| Chronologie | `.timeline` | Formations, expériences, historique des décisions ; premier point en rouge (élément le plus récent / en vigueur). |
| Icônes | `components/icon.html` (`name`, `size`) | SVG en ligne, trait 1,75 px, `aria-hidden`. |
| Pages d'entrée | `allauth/layouts/entrance.html`, `.entrance-card`, `.auth-content` | Carte centrée, logo, accroche « Espace direction » ; le contenu allauth (sans classes) est stylé par descendance. |

## 7. Logo

- Fichier : `static/img/logo-mafoa.png` (945 × 231, PNG transparent) ; favicon
  `static/img/favicon.png`.
- **Jamais déformé ni recoloré** : hauteur fixée, largeur automatique (`h-9`/`h-10` dans l'en-tête,
  `h-14` sur la page de connexion).
- Zone de respiration : au minimum la hauteur du « M » autour du logo ; aucun élément collé.
- Toujours sur fond blanc (en-tête, carte de connexion). Pas de version sur fond rouge ou sombre
  tant qu'une déclinaison monochrome officielle n'est pas fournie.
- Le suffixe « AI » est séparé du logo par un filet vertical, en Poppins capitales anthracite.

## 8. RTL (arabe)

- `base.html` positionne `dir="rtl"` selon la langue active (`ar`).
- **Propriétés logiques uniquement** : `ms-`/`me-`, `ps-`/`pe-`, `start-`/`end-`,
  `text-start`/`text-end`, `border-s`, `rounded-s` ; jamais `ml-`/`mr-`/`pl-`/`pr-`,
  `left-`/`right-`, `text-left`/`text-right` (vérifié par les tests).
- Le chevron du fil d'Ariane « › » est mis en miroir automatiquement par l'algorithme bidi.
  Les flèches et icônes directionnelles (→, déconnexion) portent la classe `.flip-rtl`.
- Les tableaux et grilles suivent le sens de lecture sans adaptation.
- Contrôle visuel : capture de la fiche candidature en `dir="rtl"` à chaque évolution notable.

## 9. Mise en œuvre technique

- Tailwind CSS 3.4 compilé localement : `npm install` puis `npm run build:css`
  (`frontend/app.css` → `static/css/app.css`, minifié et **commité**). Docker et la CI n'ont pas
  besoin de Node.
- Tokens dans `tailwind.config.js` ; `content` couvre `templates/**/*.html`, `apps/**/*.py`
  et `static/js/**/*.js`.
- CSP : aucune origine externe (`script-src 'self'`, `font-src 'self'`). Aucun script ni
  gestionnaire d'événement en ligne ; pas d'attribut `style`.
