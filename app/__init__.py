"""Application factory — Gestion du matériel informatique."""
from flask import Flask, jsonify, request
from flask_cors import CORS

from .config import Config
from .extensions import db


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})

    from .ocr import serial_ocr
    serial_ocr.configurer(app.config.get("TESSERACT_CMD"))

    from .api.routes import api
    from .web.routes import web
    app.register_blueprint(api)
    app.register_blueprint(web)

    from . import cli
    cli.register(app)

    @app.errorhandler(404)
    def not_found(err):
        if request.path.startswith("/api/"):
            return jsonify(error="not_found", message="Ressource introuvable"), 404
        return err

    @app.errorhandler(500)
    def server_error(err):
        db.session.rollback()
        if request.path.startswith("/api/"):
            return jsonify(error="server_error", message="Erreur interne"), 500
        return err

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    with app.app_context():
        db.create_all()

    return app
