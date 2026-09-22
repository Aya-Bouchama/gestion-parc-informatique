"""Logique métier : validation des entrées + opérations CRUD via l'ORM.

Toutes les requêtes passent par SQLAlchemy (requêtes paramétrées) :
plus aucune chaîne SQL construite à la main -> pas d'injection SQL possible.
"""
from datetime import date, datetime

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Etablissement, Materiel, Panne

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")


class ValidationError(Exception):
    def __init__(self, errors: dict):
        super().__init__("Données invalides")
        self.errors = errors


class NotFound(Exception):
    pass


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def parse_date(value) -> date:
    if isinstance(value, date):
        return value
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError("Format de date invalide (jj/mm/aaaa ou aaaa-mm-jj)")


def validate_payload(data: dict, partial: bool = False) -> dict:
    """Nettoie et valide un payload (formulaire ou JSON)."""
    fields = {
        "type": 100, "numero_serie": 100, "modele": 100,
        "emplacement": 150, "id_gresa": 50,
    }
    # compatibilité avec les noms de champs du prototype
    if "type_materiel" in data and "type" not in data:
        data = {**data, "type": data["type_materiel"]}

    clean, errors = {}, {}
    for name, max_len in fields.items():
        if name not in data or data[name] in (None, ""):
            if not partial:
                errors[name] = "Champ obligatoire"
            continue
        value = str(data[name]).strip()
        if len(value) > max_len:
            errors[name] = f"{max_len} caractères maximum"
        else:
            clean[name] = value

    if data.get("date_distribution") not in (None, ""):
        try:
            clean["date_distribution"] = parse_date(data["date_distribution"])
            if clean["date_distribution"] > date.today():
                errors["date_distribution"] = "La date ne peut pas être dans le futur"
        except ValueError as exc:
            errors["date_distribution"] = str(exc)
    elif not partial:
        errors["date_distribution"] = "Champ obligatoire"

    if data.get("nobre_materiel") not in (None, ""):
        try:
            n = int(data["nobre_materiel"])
            if n < 1:
                raise ValueError
            clean["nobre_materiel"] = n
        except (TypeError, ValueError):
            errors["nobre_materiel"] = "Entier positif attendu"
    elif not partial:
        errors["nobre_materiel"] = "Champ obligatoire"

    if errors:
        raise ValidationError(errors)
    return clean


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def lister(q: str | None = None) -> list[Materiel]:
    query = Materiel.query
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Etablissement).filter(or_(
            Materiel.numero_serie.ilike(like), Materiel.type.ilike(like),
            Materiel.modele.ilike(like), Materiel.emplacement.ilike(like),
            Etablissement.id_gresa.ilike(like),
        )).distinct()
    return query.order_by(Materiel.materiel_id.desc()).all()


def obtenir(materiel_id: int) -> Materiel:
    m = db.session.get(Materiel, materiel_id)
    if m is None:
        raise NotFound(f"Aucun matériel avec l'ID {materiel_id}")
    return m


def rechercher_par_serie(numero_serie: str) -> Materiel | None:
    return Materiel.query.filter_by(numero_serie=numero_serie.strip()).first()


def rapprocher_serie_ocr(numero_serie: str, cle_fn) -> Materiel | None:
    """Correspondance exacte, sinon tolérante aux confusions OCR (O/0, I/1...)."""
    exact = rechercher_par_serie(numero_serie)
    if exact:
        return exact
    cible = cle_fn(numero_serie)
    for (mid, serie) in db.session.query(Materiel.materiel_id, Materiel.numero_serie):
        if cle_fn(serie) == cible:
            return db.session.get(Materiel, mid)
    return None


def creer(data: dict) -> Materiel:
    clean = validate_payload(data)
    m = Materiel(type=clean["type"], numero_serie=clean["numero_serie"],
                 modele=clean["modele"], emplacement=clean["emplacement"])
    m.distributions.append(Etablissement(
        id_gresa=clean["id_gresa"], date_distribution=clean["date_distribution"],
        nobre_materiel=clean["nobre_materiel"]))
    db.session.add(m)
    _commit()
    return m


def modifier(materiel_id: int, data: dict) -> Materiel:
    m = obtenir(materiel_id)
    clean = validate_payload(data, partial=True)
    for attr in ("type", "numero_serie", "modele", "emplacement"):
        if attr in clean:
            setattr(m, attr, clean[attr])
    d = m.distribution_courante
    if d is None:
        d = Etablissement(id_gresa=clean.get("id_gresa", ""), nobre_materiel=1)
        m.distributions.append(d)
    for attr in ("id_gresa", "date_distribution", "nobre_materiel"):
        if attr in clean:
            setattr(d, attr, clean[attr])
    _commit()
    return m


def redistribuer(materiel_id: int, data: dict) -> Materiel:
    """Ajoute une nouvelle distribution (conserve l'historique)."""
    m = obtenir(materiel_id)
    errors = {}
    if not data.get("id_gresa"):
        errors["id_gresa"] = "Champ obligatoire"
    try:
        when = parse_date(data.get("date_distribution") or date.today())
    except ValueError as exc:
        errors["date_distribution"] = str(exc)
    if errors:
        raise ValidationError(errors)
    m.distributions.append(Etablissement(
        id_gresa=str(data["id_gresa"]).strip(), date_distribution=when,
        nobre_materiel=int(data.get("nobre_materiel") or 1)))
    if data.get("emplacement"):
        m.emplacement = str(data["emplacement"]).strip()
    _commit()
    return m


def supprimer(materiel_id: int) -> None:
    m = obtenir(materiel_id)
    db.session.delete(m)  # cascade ORM : distributions + pannes
    _commit()


def declarer_panne(materiel_id: int, data: dict) -> Panne:
    m = obtenir(materiel_id)
    try:
        when = parse_date(data.get("date_panne") or date.today())
    except ValueError as exc:
        raise ValidationError({"date_panne": str(exc)})
    p = Panne(date_panne=when, description=(data.get("description") or "")[:255])
    m.pannes.append(p)
    _commit()
    return p


def _commit():
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if "numero_serie" in str(exc.orig).lower() or "unique" in str(exc.orig).lower():
            raise ValidationError({"numero_serie": "Ce numéro de série existe déjà"})
        raise
    except Exception:
        db.session.rollback()
        raise
