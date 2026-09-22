"""Modèles ORM SQLAlchemy.

Les noms de tables et de colonnes du prototype (Materiel, Etablissement,
ID_Gresa, nobre_materiel...) sont conservés. La table Panne est nouvelle :
elle fournit l'historique d'incidents nécessaire au modèle de maintenance
prédictive. Les tables sont créées automatiquement au démarrage (SQLite).
"""
from datetime import date

from .extensions import db


class Materiel(db.Model):
    __tablename__ = "Materiel"

    materiel_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type = db.Column(db.String(100), nullable=False)
    numero_serie = db.Column(db.String(100), nullable=False, unique=True, index=True)
    modele = db.Column(db.String(100), nullable=False)
    emplacement = db.Column(db.String(150), nullable=False)

    distributions = db.relationship(
        "Etablissement", back_populates="materiel",
        cascade="all, delete-orphan", order_by="Etablissement.date_distribution",
    )
    pannes = db.relationship(
        "Panne", back_populates="materiel",
        cascade="all, delete-orphan", order_by="Panne.date_panne",
    )

    @property
    def distribution_courante(self):
        return self.distributions[-1] if self.distributions else None

    def to_dict(self) -> dict:
        d = self.distribution_courante
        return {
            "materiel_id": self.materiel_id,
            "type": self.type,
            "numero_serie": self.numero_serie,
            "modele": self.modele,
            "emplacement": self.emplacement,
            "id_gresa": d.id_gresa if d else None,
            "date_distribution": d.date_distribution.isoformat() if d and d.date_distribution else None,
            "nobre_materiel": d.nobre_materiel if d else None,
            "nb_distributions": len(self.distributions),
            "nb_pannes": len(self.pannes),
        }


class Etablissement(db.Model):
    """Historique de distribution d'un matériel vers un établissement (code GRESA)."""
    __tablename__ = "Etablissement"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_gresa = db.Column("ID_Gresa", db.String(50), nullable=False, index=True)
    materiel_id = db.Column(db.Integer, db.ForeignKey("Materiel.materiel_id", ondelete="CASCADE"), nullable=False)
    date_distribution = db.Column(db.Date, nullable=False, default=date.today)
    nobre_materiel = db.Column(db.Integer, nullable=False, default=1)

    materiel = db.relationship("Materiel", back_populates="distributions")


class Panne(db.Model):
    """Incident / panne déclaré sur un matériel (labels pour le modèle ML)."""
    __tablename__ = "Panne"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    materiel_id = db.Column(db.Integer, db.ForeignKey("Materiel.materiel_id", ondelete="CASCADE"), nullable=False)
    date_panne = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(255))

    materiel = db.relationship("Materiel", back_populates="pannes")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "materiel_id": self.materiel_id,
            "date_panne": self.date_panne.isoformat(),
            "description": self.description,
        }
