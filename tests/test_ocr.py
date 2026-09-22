import io

import cv2
import numpy as np
import pytest

from app.ocr import serial_ocr

pytestmark = pytest.mark.skipif(not serial_ocr.tesseract_disponible(), reason="Tesseract absent")


def _etiquette(texte_lignes, angle=0.0, bruit=True) -> bytes:
    img = np.full((260, 900, 3), 235, np.uint8)
    for i, t in enumerate(texte_lignes):
        cv2.putText(img, t, (30, 70 + i * 70), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (20, 20, 20), 3, cv2.LINE_AA)
    if bruit:
        rng = np.random.default_rng(0)
        img = np.clip(img + rng.normal(0, 12, img.shape), 0, 255).astype(np.uint8)
    if angle:
        M = cv2.getRotationMatrix2D((450, 130), angle, 1.0)
        img = cv2.warpAffine(img, M, (900, 260), borderValue=(235, 235, 235))
    return cv2.imencode(".png", img)[1].tobytes()


def test_extraction_regex():
    c = serial_ocr.extraire_candidats("MODEL: HP 400 G7  S/N: 5CD1234XYZ  MADE IN CHINA")
    assert c[0] == ("5CD1234XYZ", True)


def test_ocr_photo_etiquette():
    img = _etiquette(["MODEL: OPTIPLEX 3080", "S/N: CNF4K7X8123"])
    res = serial_ocr.lire_numero_serie(img)
    assert res["numero_serie"] == "CNF4K7X8123", res


def test_cle_ambigue():
    assert serial_ocr.cle_ambigue("CNOF4K7X8123") == serial_ocr.cle_ambigue("CN0F4K7X8123")


def test_ocr_etiquette_inclinee():
    img = _etiquette(["SERIAL NO: 5CG9281QWZ"], angle=4)
    res = serial_ocr.lire_numero_serie(img)
    assert res["numero_serie"] == "5CG9281QWZ", res


def test_endpoint_ocr(client):
    client.post("/api/v1/materiels", json={
        "type": "PC", "numero_serie": "CN0F4K7X8123", "modele": "OptiPlex", "emplacement": "CDI",
        "id_gresa": "01234A", "date_distribution": "2022-01-10", "nobre_materiel": 1})
    img = _etiquette(["MODEL: OPTIPLEX 3080", "S/N: CN0F4K7X8123"])
    r = client.post("/api/v1/ocr/numero-serie", data={"image": (io.BytesIO(img), "etiquette.png")},
                    content_type="multipart/form-data")
    body = r.get_json()
    # l'OCR peut lire "CNOF..." : la base corrige en "CN0F..."
    assert r.status_code == 200 and body["numero_serie"] == "CN0F4K7X8123", body
    assert body["materiel_existant"]["emplacement"] == "CDI"


def test_endpoint_ocr_fichier_invalide(client):
    r = client.post("/api/v1/ocr/numero-serie", data={"image": (io.BytesIO(b"pas une image"), "x.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 422
