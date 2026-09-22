"""Pages HTML : l'interface (Vue.js + Bootstrap) consomme l'API REST."""
from datetime import datetime

from flask import Blueprint, abort, render_template

from ..extensions import db
from ..models import Materiel

web = Blueprint("web", __name__)


@web.get("/")
def index():
    return render_template("index.html")


@web.get("/imprimer/<int:materiel_id>")
def imprimer(materiel_id):
    m = db.session.get(Materiel, materiel_id) or abort(404)
    return render_template("impression.html", m=m, d=m.distribution_courante, now=datetime.now())
