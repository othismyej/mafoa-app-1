"""Accord en nombre des compteurs (KAN-15).

Sans catalogue ``fr`` compilé, ``ngettext`` retombe sur la règle anglaise (singulier
seulement pour 1) : « 0 décisions enregistrées ». En français, 0 s'accorde au singulier.
Usage : ``{% blocktranslate count c=n|accord with n=n %}…{% endblocktranslate %}`` : le
nombre affiché reste ``n`` ; seule la forme choisie change, et uniquement en français
(l'arabe garde sa forme propre au zéro).
"""

from django import template
from django.utils.translation import get_language

register = template.Library()


@register.filter
def accord(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return n
    if n == 0 and (get_language() or "fr").startswith("fr"):
        return 1
    return n
