import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db


@pytest.fixture()
def app(tmp_path):
    class Cfg(TestConfig):
        ML_MODEL_PATH = str(tmp_path / "model.joblib")
    app = create_app(Cfg)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


PAYLOAD = {
    "type": "PC", "numero_serie": "SN-ABC12345", "modele": "Dell OptiPlex",
    "emplacement": "Salle 1", "id_gresa": "01234A",
    "date_distribution": "15/03/2022", "nobre_materiel": 3,
}
