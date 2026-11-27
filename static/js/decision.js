// Formulaires de décision (KAN-13) et d'anomalie (KAN-42) : les refus 400 (règle non respectée)
// et 409 (conflit de concurrence), ainsi que l'échec 503 d'une relance de détection, renvoient
// le bloc avec son message. Limité à ces formulaires : les autres requêtes htmx gardent le
// comportement par défaut (aucun remplacement sur erreur).
document.addEventListener("htmx:beforeSwap", function (evt) {
  var status = evt.detail.xhr.status;
  var elt = evt.detail.elt;
  var cible = elt.closest("[data-decision-form], [data-anomalie-form], [data-relancer-detection]");
  if ((status === 400 || status === 409 || status === 503) && cible) {
    evt.detail.shouldSwap = true;
    evt.detail.isError = false;
  }
});
