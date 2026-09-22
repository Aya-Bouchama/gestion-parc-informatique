"""Configuration centralisée, lue depuis les variables d'environnement (.env).

Plus aucun secret n'est écrit en dur dans le code source.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))


def _database_uri() -> str:
    """Base SQLite (fichier data/gestion.sqlite3), créée automatiquement.

    DATABASE_URL permet de pointer vers un autre fichier si besoin.
    """
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'gestion.sqlite3'}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # timeout : attend (au lieu d'échouer) si un autre processus écrit en même temps
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 Mo max pour les photos OCR

    ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", str(BASE_DIR / "models_ml" / "maintenance_rf.joblib"))
    TESSERACT_CMD = os.getenv("TESSERACT_CMD")  # ex. C:\Program Files\Tesseract-OCR\tesseract.exe
    OCR_LANG = os.getenv("OCR_LANG", "eng")
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SECRET_KEY = "test"
