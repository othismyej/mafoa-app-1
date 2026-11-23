// Visionneuse des pièces de la fiche candidature (KAN-12). Aucun gestionnaire en ligne (CSP).
// « Fermer » vide la zone et rend le focus au bouton « Consulter » de la pièce.
document.addEventListener("click", function (event) {
  var bouton = event.target.closest && event.target.closest("[data-visionneuse-fermer]");
  if (!bouton) return;
  var zone = document.getElementById("visionneuse");
  if (zone) zone.replaceChildren();
  var id = bouton.getAttribute("data-visionneuse-fermer");
  var retour = id && document.querySelector('[data-consulter="' + id + '"]');
  if (retour) retour.focus();
});
// Après chargement d'une pièce : focus sur le titre du fragment (annoncé par aria-live).
document.addEventListener("htmx:afterSwap", function (evt) {
  var cible = evt.detail.target;
  if (!cible || cible.id !== "visionneuse") return;
  var titre = cible.querySelector("[data-visionneuse-titre]");
  if (titre) titre.focus();
});
// Pièce supprimée entre l'affichage de la fiche et le clic : le message 404 est affiché.
document.addEventListener("htmx:beforeSwap", function (evt) {
  var cible = evt.detail.target;
  if (evt.detail.xhr.status === 404 && cible && cible.id === "visionneuse") {
    evt.detail.shouldSwap = true;
    evt.detail.isError = false;
  }
});
