"""Webhooks du site (cible, à implémenter).

Prévu : POST signé HMAC-SHA256 sur ``/integrations/webhooks/site/`` à chaque nouvelle
candidature ou pièce. Vérification de la signature et de l'horodatage (anti-rejeu),
puis déclenchement d'une tâche Celery. Une synchronisation de rattrapage toutes les
15 minutes couvre les webhooks perdus.
"""
