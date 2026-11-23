// Compteur de caractères des champs portant data-compteur-cible="<id du compteur>".
// Délégation d'événement : fonctionne aussi pour les formulaires insérés par HTMX.
document.addEventListener("input", function (event) {
  var champ = event.target;
  var cible = champ && champ.getAttribute && champ.getAttribute("data-compteur-cible");
  if (!cible) return;
  var compteur = document.getElementById(cible);
  if (compteur) compteur.textContent = String(champ.value.length);
});
