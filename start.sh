#!/bin/sh
# Démarrage en production (Render, Docker…).
# Hébergement gratuit = disque effacé à chaque redémarrage :
# si la base n'existe pas encore, on charge les données de démo et on entraîne le modèle.
set -e
DB="${DATA_DIR:-/app/data}/gestion.sqlite3"

if [ ! -f "$DB" ]; then
  echo ">> Première initialisation : import des données de démonstration"
  flask --app wsgi import-donnees donnees_fictives_dpen_fes.xlsx
  echo ">> Entraînement du modèle de maintenance prédictive"
  flask --app wsgi ml-train || echo "!! Entraînement ignoré (données insuffisantes)"
fi

# Render fournit le port dans $PORT ; 8000 par défaut en local.
exec gunicorn -w "${WEB_CONCURRENCY:-1}" -b "0.0.0.0:${PORT:-8000}" --timeout 120 wsgi:application
