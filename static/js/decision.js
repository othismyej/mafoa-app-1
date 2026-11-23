// Formulaire de décision (KAN-13) : les refus 400 (règle non respectée) et 409 (conflit de
// concurrence) renvoient le formulaire avec son message. Limité à ce formulaire : les autres
// requêtes htmx gardent le comportement par défaut (aucun remplacement sur erreur).
document.addEventListener("htmx:beforeSwap", function (evt) {
  var status = evt.detail.xhr.status;
  if ((status === 400 || status === 409) && evt.detail.elt.closest("[data-decision-form]")) {
    evt.detail.shouldSwap = true;
    evt.detail.isError = false;
  }
});
