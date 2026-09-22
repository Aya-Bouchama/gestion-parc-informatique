"""API REST JSON — /api/v1

GET    /api/v1/health
GET    /api/v1/materiels?q=                    liste (+ recherche plein texte)
POST   /api/v1/materiels                       création
GET    /api/v1/materiels/<id>                  détail + historique
PUT    /api/v1/materiels/<id>                  modification
DELETE /api/v1/materiels/<id>                  suppression
GET    /api/v1/materiels/serie/<numero>        recherche par numéro de série
POST   /api/v1/materiels/<id>/distributions    nouvelle distribution (historique)
POST   /api/v1/materiels/<id>/pannes           déclaration de panne
GET    /api/v1/maintenance/predictions         risques de panne + obsolescence
GET    /api/v1/maintenance/modele              métriques du modèle
POST   /api/v1/maintenance/entrainer           (ré)entraînement du Random Forest
POST   /api/v1/ocr/numero-serie                extraction OCR (multipart, champ "image")
POST   /api/v1/import?simulation=1             import Excel/CSV (multipart, champ "fichier")
GET    /api/v1/import/modele                   modèle Excel vierge à remplir
GET    /api/v1/export                          export Excel de toute la base
"""
from datetime import date

from flask import Blueprint, current_app, jsonify, request, send_file

from ..extensions import db
from ..ml import maintenance as ml
from ..models import Materiel
from ..ocr import serial_ocr
from ..services import import_service
from ..services import materiel_service as svc

api = Blueprint("api", __name__, url_prefix="/api/v1")

EXTENSIONS_IMAGE = {"jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"}


# --------------------------------------------------------------------------- #
# Gestion d'erreurs homogène (JSON)
# --------------------------------------------------------------------------- #
@api.errorhandler(svc.ValidationError)
def _validation(err):
    return jsonify(error="validation", details=err.errors), 422


@api.errorhandler(svc.NotFound)
def _not_found(err):
    return jsonify(error="not_found", message=str(err)), 404


@api.errorhandler(413)
def _too_large(_):
    return jsonify(error="payload_too_large", message="Fichier trop volumineux (8 Mo max)"), 413


def _json() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    return data or {}


# --------------------------------------------------------------------------- #
# CRUD matériel
# --------------------------------------------------------------------------- #
@api.get("/health")
def health():
    db.session.execute(db.text("SELECT 1"))
    return jsonify(status="ok", ocr=serial_ocr.tesseract_disponible(),
                   modele_ml=ml.charger(current_app.config["ML_MODEL_PATH"]) is not None)


@api.get("/materiels")
def lister():
    items = svc.lister(request.args.get("q"))
    return jsonify(total=len(items), items=[m.to_dict() for m in items])


@api.post("/materiels")
def creer():
    m = svc.creer(_json())
    return jsonify(m.to_dict()), 201


@api.get("/materiels/<int:materiel_id>")
def detail(materiel_id):
    m = svc.obtenir(materiel_id)
    out = m.to_dict()
    out["historique_distributions"] = [
        {"id_gresa": d.id_gresa, "date_distribution": d.date_distribution.isoformat(),
         "nobre_materiel": d.nobre_materiel} for d in m.distributions]
    out["pannes"] = [p.to_dict() for p in m.pannes]
    return jsonify(out)


@api.put("/materiels/<int:materiel_id>")
@api.patch("/materiels/<int:materiel_id>")
def modifier(materiel_id):
    return jsonify(svc.modifier(materiel_id, _json()).to_dict())


@api.delete("/materiels/<int:materiel_id>")
def supprimer(materiel_id):
    svc.supprimer(materiel_id)
    return "", 204


@api.get("/materiels/serie/<path:numero_serie>")
def par_serie(numero_serie):
    m = svc.rechercher_par_serie(numero_serie)
    if not m:
        raise svc.NotFound("Aucun matériel trouvé avec ce numéro de série")
    return jsonify(m.to_dict())


@api.post("/materiels/<int:materiel_id>/distributions")
def redistribuer(materiel_id):
    return jsonify(svc.redistribuer(materiel_id, _json()).to_dict()), 201


@api.post("/materiels/<int:materiel_id>/pannes")
def declarer_panne(materiel_id):
    return jsonify(svc.declarer_panne(materiel_id, _json()).to_dict()), 201


# --------------------------------------------------------------------------- #
# IA : maintenance prédictive
# --------------------------------------------------------------------------- #
@api.get("/maintenance/predictions")
def predictions():
    materiels = Materiel.query.all()
    hist = ml.historique_depuis_orm(materiels)
    preds = ml.predire(hist, current_app.config["ML_MODEL_PATH"])
    infos = {m.materiel_id: m for m in materiels}
    for p in preds:
        m = infos[p["materiel_id"]]
        p.update(type=m.type, modele=m.modele, numero_serie=m.numero_serie, emplacement=m.emplacement)
    return jsonify(items=preds)


@api.get("/maintenance/modele")
def infos_modele():
    bundle = ml.charger(current_app.config["ML_MODEL_PATH"])
    if not bundle:
        return jsonify(entraine=False), 200
    return jsonify(entraine=True, **bundle["metriques"])


@api.post("/maintenance/entrainer")
def entrainer():
    hist = ml.historique_depuis_orm(Materiel.query.all())
    try:
        metriques = ml.entrainer(hist, current_app.config["ML_MODEL_PATH"])
    except ml.DonneesInsuffisantes as exc:
        return jsonify(error="donnees_insuffisantes", message=str(exc)), 409
    return jsonify(entraine=True, **metriques)


# --------------------------------------------------------------------------- #
# IA : vision (OCR)
# --------------------------------------------------------------------------- #
@api.post("/ocr/numero-serie")
def ocr_numero_serie():
    f = request.files.get("image")
    if f is None or not f.filename:
        return jsonify(error="validation", details={"image": "Fichier image requis"}), 422
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in EXTENSIONS_IMAGE:
        return jsonify(error="validation", details={"image": "Format non supporté"}), 422
    try:
        res = serial_ocr.lire_numero_serie(f.read(), lang=current_app.config["OCR_LANG"])
    except serial_ocr.OCRIndisponible as exc:
        return jsonify(error="ocr_indisponible", message=str(exc)), 503
    except ValueError as exc:
        return jsonify(error="validation", details={"image": str(exc)}), 422

    # Le matériel est-il déjà connu ? (tolérant aux confusions O/0, I/1, S/5...)
    existant = None
    for c in res["candidats"]:
        existant = svc.rapprocher_serie_ocr(c["valeur"], serial_ocr.cle_ambigue)
        if existant:
            res["numero_serie"] = existant.numero_serie  # correction par la base
            break
    res["materiel_existant"] = existant.to_dict() if existant else None
    return jsonify(res)


# --------------------------------------------------------------------------- #
# Import / export de fichiers
# --------------------------------------------------------------------------- #
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@api.post("/import")
def importer_fichier():
    f = request.files.get("fichier")
    if f is None or not f.filename:
        raise svc.ValidationError({"fichier": "Fichier requis"})
    simulation = request.args.get("simulation", "0").lower() in ("1", "true", "oui")
    return jsonify(import_service.importer(f.read(), f.filename, simulation=simulation))


@api.get("/import/modele")
def modele_import():
    import io
    return send_file(io.BytesIO(import_service.generer_modele()), mimetype=XLSX,
                     as_attachment=True, download_name="modele_import_materiel.xlsx")


@api.get("/export")
def exporter():
    import io
    return send_file(io.BytesIO(import_service.exporter()), mimetype=XLSX, as_attachment=True,
                     download_name=f"inventaire_materiel_{date.today():%Y-%m-%d}.xlsx")
