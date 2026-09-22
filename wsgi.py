"""Point d'entrée WSGI (Gunicorn dans Docker, ou mod_wsgi/Apache).

    gunicorn -w 3 -b 0.0.0.0:8000 wsgi:application
"""
import sys
from pathlib import Path

# Chemin relatif au fichier : plus de chemin Windows codé en dur
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import create_app  # noqa: E402

application = create_app()
app = application
