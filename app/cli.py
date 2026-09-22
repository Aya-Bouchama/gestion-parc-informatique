"""Commandes : flask init-db | flask seed-demo | flask ml-train"""
import random
from datetime import date, timedelta

import click
from flask import current_app

from .extensions import db
from .ml import maintenance as ml
from .services import import_service
from .services.materiel_service import ValidationError
from .models import Etablissement, Materiel, Panne

TYPES = {
    # type : (modèles, risque de base annuel)
    "PC": (["Dell OptiPlex 3080", "HP ProDesk 400", "Lenovo ThinkCentre M70"], 0.10),
    "Portable": (["Lenovo ThinkPad E14", "HP ProBook 450", "Dell Latitude 3520"], 0.16),
    "Imprimante": (["HP LaserJet M404", "Canon i-SENSYS", "Epson EcoTank L3250"], 0.20),
    "Vidéoprojecteur": (["Epson EB-W06", "BenQ MW560", "Optoma W400"], 0.14),
    "Écran": (["Dell P2419H", "Samsung S24", "HP 24mh"], 0.04),
    "Tablette": (["Samsung Galaxy Tab A8", "iPad 9"], 0.12),
}
EMPLACEMENTS = ["Salle informatique 1", "Salle informatique 2", "Direction", "CDI",
                "Secrétariat", "Salle des professeurs", "Laboratoire SVT", "Magasin"]


def register(app):
    @app.cli.command("init-db")
    def init_db():
        """Crée les tables (idempotent)."""
        db.create_all()
        click.echo("Tables créées.")

    @app.cli.command("seed-demo")
    @click.option("--n", default=150, help="Nombre de matériels à générer")
    @click.option("--reset", is_flag=True, help="Vide les tables avant génération")
    def seed_demo(n, reset):
        """Génère un jeu de données de DÉMONSTRATION (historique simulé)."""
        db.create_all()
        if reset:
            Panne.query.delete(); Etablissement.query.delete(); Materiel.query.delete()
            db.session.commit()
        rng = random.Random(2022)
        today = date.today()
        for i in range(n):
            type_, (modeles, base) = rng.choice(list(TYPES.items()))
            debut = today - timedelta(days=rng.randint(60, 8 * 365))
            m = Materiel(type=type_, modele=rng.choice(modeles),
                         numero_serie=f"{ml._normaliser(type_)[:2].upper()}{rng.randint(10**7, 10**8 - 1)}X{i:03d}",
                         emplacement=rng.choice(EMPLACEMENTS))
            # Historique de distribution
            d, dates_distrib = debut, []
            while d <= today:
                m.distributions.append(Etablissement(
                    id_gresa=f"0{rng.randint(1000, 9999)}{rng.choice('ABCDEFGH')}",
                    date_distribution=d, nobre_materiel=rng.randint(1, 30)))
                dates_distrib.append(d)
                d += timedelta(days=rng.randint(400, 1500))
            # Pannes simulées : le risque croît avec l'âge, les pannes passées
            # et le nombre de déplacements (processus de hasard mensuel)
            jour, n_pannes = debut + timedelta(days=30), 0
            while jour <= today:
                age_ans = (jour - debut).days / 365
                nb_redistrib = sum(x <= jour for x in dates_distrib)
                h = base / 12 * (1 + 0.3 * age_ans ** 1.2) * (1 + 0.25 * min(n_pannes, 4)) * (1 + 0.1 * nb_redistrib)
                if rng.random() < min(h, 0.9):
                    m.pannes.append(Panne(date_panne=jour, description="Panne simulée (démo)"))
                    n_pannes += 1
                jour += timedelta(days=30)
            db.session.add(m)
        db.session.commit()
        click.echo(f"{n} matériels de démonstration créés.")

    @app.cli.command("ml-train")
    def ml_train():
        """Entraîne le Random Forest de maintenance prédictive."""
        hist = ml.historique_depuis_orm(Materiel.query.all())
        try:
            metriques = ml.entrainer(hist, current_app.config["ML_MODEL_PATH"])
        except ml.DonneesInsuffisantes as exc:
            raise click.ClickException(str(exc))
        for k, v in metriques.items():
            click.echo(f"{k}: {v}")

    @app.cli.command("import-donnees")
    @click.argument("fichier", type=click.Path(exists=True, dir_okay=False))
    @click.option("--simulation", is_flag=True, help="Vérifie le fichier sans rien enregistrer")
    def import_donnees(fichier, simulation):
        """Importe vos données depuis un fichier Excel (.xlsx) ou CSV."""
        with open(fichier, "rb") as fh:
            try:
                r = import_service.importer(fh.read(), fichier, simulation=simulation)
            except ValidationError as exc:
                raise click.ClickException(str(exc.errors))
        click.echo(("[SIMULATION] " if simulation else "") +
                   f"{r['lignes']} lignes lues : {r['crees']} créés, {r['mis_a_jour']} mis à jour, "
                   f"{r['distributions_ajoutees']} distributions ajoutées, {r['pannes_ajoutees']} pannes ajoutées.")
        for e in r["erreurs"]:
            click.echo(f"  ⚠ {e['feuille']} ligne {e['ligne']} : " +
                       "; ".join(f"{k} — {v}" for k, v in e["erreurs"].items()))

    @app.cli.command("modele-import")
    @click.argument("fichier", default="modele_import_materiel.xlsx")
    def modele_import(fichier):
        """Crée le modèle Excel vierge à remplir."""
        with open(fichier, "wb") as fh:
            fh.write(import_service.generer_modele())
        click.echo(f"Modèle créé : {fichier}")
