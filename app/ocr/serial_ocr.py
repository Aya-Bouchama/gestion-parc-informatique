"""Extraction automatique du numéro de série depuis une photo d'étiquette.

Chaîne de traitement (OpenCV + Tesseract) :
  1. décodage de l'image, redimensionnement (Tesseract préfère ~30 px/caractère)
  2. niveaux de gris, débruitage (filtre bilatéral), correction d'inclinaison
  3. plusieurs binarisations (Otsu, adaptative, inversée) -> plusieurs essais OCR
  4. recherche par expressions régulières des motifs « S/N », « Serial », etc.
  5. classement des candidats (mot-clé détecté, confiance Tesseract, forme)
"""
from __future__ import annotations

import re
from collections import defaultdict

import cv2
import numpy as np
import pytesseract

# Mots-clés précédant un numéro de série sur les étiquettes constructeurs
_KEYWORDS = r"(?:S\s*/\s*N|SN|SER(?:IAL)?\s*(?:NO|NUMBER|N[O°])?|N[O°]\s*(?:DE\s*)?S[EÉ]RIE|NUM[EÉ]RO\s*DE\s*S[EÉ]RIE)"
_SERIAL = r"([A-Z0-9][A-Z0-9\-]{4,24}[A-Z0-9])"
RE_AVEC_MOT_CLE = re.compile(_KEYWORDS + r"\s*[:#.\-]?\s*" + _SERIAL, re.IGNORECASE)
RE_TOKEN = re.compile(r"\b[A-Z0-9][A-Z0-9\-]{5,24}[A-Z0-9]\b")
_MOTS_EXCLUS = {"SERIAL", "NUMBER", "MODEL", "MODELE", "PRODUCT", "MADEIN", "CHINA", "WARRANTY"}


class OCRIndisponible(RuntimeError):
    pass


def configurer(tesseract_cmd: str | None = None) -> None:
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


def tesseract_disponible() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Prétraitement OpenCV
# --------------------------------------------------------------------------- #
def decoder_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Fichier image illisible (formats acceptés : JPG, PNG, BMP, TIFF)")
    return img


def _redimensionner(gray: np.ndarray, largeur_cible: int = 1600) -> np.ndarray:
    h, w = gray.shape[:2]
    if w < largeur_cible:
        f = largeur_cible / w
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    elif w > 2 * largeur_cible:
        f = 2 * largeur_cible / w
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return gray


def _redresser(binaire: np.ndarray, gray: np.ndarray) -> np.ndarray:
    """Corrige une légère inclinaison (±15°) de l'étiquette."""
    coords = np.column_stack(np.where(binaire < 128))
    if len(coords) < 50:
        return gray
    angle = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    if abs(angle) < 0.5 or abs(angle) > 15:
        return gray
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def pretraiter(img: np.ndarray) -> list[np.ndarray]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = _redimensionner(gray)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    gray = _redresser(otsu, gray)

    variantes = []
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variantes.append(otsu)
    variantes.append(cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10))
    # Étiquettes à texte clair sur fond sombre
    if np.mean(otsu) < 110:
        variantes.append(cv2.bitwise_not(otsu))
    return variantes


# --------------------------------------------------------------------------- #
# OCR + extraction
# --------------------------------------------------------------------------- #
_AMBIGUS = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                          "S": "5", "B": "8", "Z": "2", "G": "6"})


def cle_ambigue(s: str) -> str:
    """Clé insensible aux confusions typiques de l'OCR (O/0, I/1, S/5, B/8...)."""
    return _normaliser_serie(s).replace("-", "").translate(_AMBIGUS)


def _normaliser_serie(s: str) -> str:
    return re.sub(r"[^A-Z0-9\-]", "", s.upper()).strip("-")


def _forme_plausible(s: str) -> bool:
    return (6 <= len(s) <= 26 and any(c.isdigit() for c in s)
            and any(c.isalpha() for c in s) and s not in _MOTS_EXCLUS)


def extraire_candidats(texte: str) -> list[tuple[str, bool]]:
    """Retourne [(numéro, trouvé_après_mot_clé), ...] depuis un texte OCR brut."""
    out = []
    for m in RE_AVEC_MOT_CLE.finditer(texte):
        s = _normaliser_serie(m.group(1))
        if len(s) >= 5:
            out.append((s, True))
    for tok in RE_TOKEN.findall(texte.upper()):
        s = _normaliser_serie(tok)
        if _forme_plausible(s):
            out.append((s, False))
    return out


def lire_numero_serie(data: bytes, lang: str = "eng") -> dict:
    if not tesseract_disponible():
        raise OCRIndisponible("Tesseract OCR n'est pas installé sur le serveur")

    img = decoder_image(data)
    scores: dict[str, float] = defaultdict(float)
    textes = []
    for variante in pretraiter(img):
        for psm in (6, 11):  # 6 = bloc de texte, 11 = texte épars (étiquettes)
            cfg = f"--oem 3 --psm {psm}"
            d = pytesseract.image_to_data(variante, lang=lang, config=cfg,
                                          output_type=pytesseract.Output.DICT)
            mots = [(w, float(c)) for w, c in zip(d["text"], d["conf"]) if w.strip()]
            texte = " ".join(w for w, _ in mots)
            textes.append(texte)
            conf_par_mot = {_normaliser_serie(w): c for w, c in mots}
            for serie, avec_cle in extraire_candidats(texte):
                conf = conf_par_mot.get(serie, 50.0)
                scores[serie] += (2.0 if avec_cle else 1.0) * max(conf, 1) / 100

    classement = sorted(scores.items(), key=lambda kv: -kv[1])
    return {
        "numero_serie": classement[0][0] if classement else None,
        "candidats": [{"valeur": s, "score": round(v, 2)} for s, v in classement[:5]],
        "texte_brut": max(textes, key=len) if textes else "",
    }
