// Actions en lot sur la liste des candidatures (KAN-15). Amélioration progressive : sans
// JavaScript, les cases et les deux boutons d'envoi fonctionnent (pages complètes).
// Ici : « tout sélectionner sur la page », compteur annoncé, focus sur le titre du panneau
// inséré, affichage des refus 400 / 409 dans le panneau. Aucune sélection n'est mémorisée.
(function () {
  function cases() {
    return document.querySelectorAll('input[data-lot-case]');
  }

  function majCompteur() {
    var liste = cases();
    var n = 0;
    for (var i = 0; i < liste.length; i++) if (liste[i].checked) n++;
    var compteur = document.querySelector("[data-lot-compteur]");
    if (compteur) {
      var modele = compteur.getAttribute(n > 1 ? "data-pluriel" : "data-singulier") || "%n";
      compteur.textContent = modele.replace("%n", String(n));
    }
    var tout = document.querySelector("input[data-lot-tout]");
    if (tout) {
      tout.checked = liste.length > 0 && n === liste.length;
      tout.indeterminate = n > 0 && n < liste.length;
    }
  }

  function init() {
    var label = document.querySelector("[data-lot-tout-label]");
    if (label) label.hidden = false;
    majCompteur();
  }

  document.addEventListener("change", function (event) {
    var cible = event.target;
    if (!cible || !cible.matches) return;
    if (cible.matches("input[data-lot-tout]")) {
      var liste = cases();
      for (var i = 0; i < liste.length; i++) liste[i].checked = cible.checked;
      majCompteur();
    } else if (cible.matches("input[data-lot-case]")) {
      majCompteur();
    }
  });

  document.addEventListener("DOMContentLoaded", init);
  document.addEventListener("htmx:afterSettle", function (evt) {
    init();
    var cible = evt.detail && evt.detail.target;
    if (cible && cible.id === "lot-panneau") {
      var titre = cible.querySelector("[data-lot-titre]");
      if (titre) titre.focus();
    }
  });

  // Les refus (400 : saisie incomplète, sélection vide ; 409 : lot déjà exécuté) renvoient le
  // panneau avec son message : on l'affiche, limité au panneau des lots.
  document.addEventListener("htmx:beforeSwap", function (evt) {
    var status = evt.detail.xhr.status;
    var cible = evt.detail.target;
    if ((status === 400 || status === 409) && cible && cible.id === "lot-panneau") {
      evt.detail.shouldSwap = true;
      evt.detail.isError = false;
    }
  });
})();
