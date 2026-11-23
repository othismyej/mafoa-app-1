// Configuration Tailwind de Mafoa AI : tokens de la charte (docs/design/charte.md).
// Le CSS compilé (static/css/app.css) est commité : « npm run build:css » après toute
// modification d'un gabarit qui introduit de nouvelles classes.
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.html", "./apps/**/*.py", "./static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        // Marque (site mafoamanagement.com) : accent, jamais couleur de statut.
        brand: {
          50: "#fdf3f4",
          100: "#fae1e3",
          200: "#f3b9be",
          600: "#d01c2b", // primary Bootstrap du site
          700: "#b11825", // survol
          800: "#a61622", // actif / pressé
          logo: "#bc3735",
        },
        // Neutres anthracite (gris purs du site, sans teinte bleue).
        ink: {
          50: "#f8f9fa", // fond de page (light du site)
          100: "#f1f3f5",
          200: "#e4e6e9",
          300: "#cfd2d6",
          400: "#8e9095", // bordure de champ (≥ 3:1)
          500: "#7a7b7e", // gris du logo : décor, icônes, jamais texte courant
          600: "#5c5d61", // texte secondaire
          700: "#474747", // anthracite du logo
          800: "#424242", // texte courant (secondary du site)
          900: "#262626", // titres
        },
      },
      fontFamily: {
        sans: ["Roboto", "Noto Sans Arabic", "Segoe UI", "Tahoma", "system-ui", "sans-serif"],
        display: ["Poppins", "Roboto", "Noto Sans Arabic", "Segoe UI", "Tahoma", "sans-serif"],
      },
      borderRadius: {
        card: "0.75rem", // 12 px
        control: "0.5rem", // 8 px
      },
      boxShadow: {
        card: "0 1px 2px rgb(38 38 38 / 0.04), 0 1px 3px rgb(38 38 38 / 0.06)",
        raised: "0 4px 12px rgb(38 38 38 / 0.08), 0 2px 4px rgb(38 38 38 / 0.04)",
      },
      maxWidth: {
        page: "80rem", // 1280 px
      },
    },
  },
  plugins: [],
};
