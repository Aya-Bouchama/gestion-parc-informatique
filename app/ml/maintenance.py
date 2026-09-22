"""Maintenance prédictive : Random Forest (scikit-learn).

Principe
--------
Pour chaque matériel on construit, à une date de référence T, des variables
issues de son historique de distribution et d'incidents :

    type, age_jours, nb_distributions, jours_depuis_derniere_distribution,
    nobre_materiel, nb_pannes_passees, jours_depuis_derniere_panne

La cible vaut 1 si le matériel tombe en panne dans les HORIZON_JOURS suivant T.
L'entraînement utilise plusieurs dates de référence glissantes dans le passé
(« snapshots ») afin de ne jamais utiliser d'information future (pas de fuite
de données), puis le modèle prédit le risque à la date du jour.

Un indicateur d'obsolescence (âge vs durée de vie de référence par type)
complète la probabilité de panne.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

HORIZON_JOURS = 180
SNAPSHOT_PAS_JOURS = 90
NB_SNAPSHOTS = 12
MIN_ECHANTILLONS = 30

NUM_FEATURES = [
    "age_jours", "nb_distributions", "jours_depuis_derniere_distribution",
    "nobre_materiel", "nb_pannes_passees", "jours_depuis_derniere_panne",
]
CAT_FEATURES = ["type"]

# Durée de vie de référence (années) utilisée pour l'indicateur d'obsolescence
DUREE_VIE_ANS = {
    "pc": 5, "ordinateur": 5, "portable": 4, "laptop": 4, "unite centrale": 5,
    "ecran": 7, "moniteur": 7, "imprimante": 6, "scanner": 6,
    "videoprojecteur": 6, "projecteur": 6, "tablette": 4, "serveur": 6,
    "switch": 8, "routeur": 8, "onduleur": 5,
}
DUREE_VIE_DEFAUT = 5


def _normaliser(txt: str) -> str:
    import unicodedata
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    return txt.lower().strip()


def duree_vie_ans(type_materiel: str) -> int:
    t = _normaliser(type_materiel)
    for cle, ans in DUREE_VIE_ANS.items():
        if cle in t:
            return ans
    return DUREE_VIE_DEFAUT


# --------------------------------------------------------------------------- #
# Construction des variables
# --------------------------------------------------------------------------- #
@dataclass
class HistoriqueMateriel:
    materiel_id: int
    type: str
    distributions: list[tuple[date, int]]  # (date, nobre_materiel)
    pannes: list[date]


def historique_depuis_orm(materiels) -> list[HistoriqueMateriel]:
    return [
        HistoriqueMateriel(
            materiel_id=m.materiel_id,
            type=m.type,
            distributions=[(d.date_distribution, d.nobre_materiel) for d in m.distributions],
            pannes=[p.date_panne for p in m.pannes],
        )
        for m in materiels
    ]


def features_a_date(h: HistoriqueMateriel, ref: date) -> dict | None:
    dist = sorted(d for d in h.distributions if d[0] <= ref)
    if not dist:
        return None  # matériel pas encore mis en service à cette date
    pannes = sorted(p for p in h.pannes if p <= ref)
    premiere, derniere = dist[0], dist[-1]
    return {
        "materiel_id": h.materiel_id,
        "type": _normaliser(h.type) or "inconnu",
        "age_jours": (ref - premiere[0]).days,
        "nb_distributions": len(dist),
        "jours_depuis_derniere_distribution": (ref - derniere[0]).days,
        "nobre_materiel": derniere[1] or 1,
        "nb_pannes_passees": len(pannes),
        # -1 = jamais tombé en panne
        "jours_depuis_derniere_panne": (ref - pannes[-1]).days if pannes else -1,
    }


def construire_dataset(historiques: list[HistoriqueMateriel], aujourd_hui: date | None = None) -> pd.DataFrame:
    aujourd_hui = aujourd_hui or date.today()
    lignes = []
    for k in range(NB_SNAPSHOTS):
        ref = aujourd_hui - timedelta(days=HORIZON_JOURS + k * SNAPSHOT_PAS_JOURS)
        fin = ref + timedelta(days=HORIZON_JOURS)
        for h in historiques:
            f = features_a_date(h, ref)
            if f is None:
                continue
            f["cible"] = int(any(ref < p <= fin for p in h.pannes))
            f["date_ref"] = ref
            lignes.append(f)
    return pd.DataFrame(lignes)


# --------------------------------------------------------------------------- #
# Modèle
# --------------------------------------------------------------------------- #
def creer_pipeline() -> Pipeline:
    preprocess = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES),
        ("num", "passthrough", NUM_FEATURES),
    ])
    rf = RandomForestClassifier(
        n_estimators=300, max_depth=8, min_samples_leaf=3,
        class_weight="balanced", random_state=42, n_jobs=-1,
    )
    return Pipeline([("preprocess", preprocess), ("rf", rf)])


class DonneesInsuffisantes(Exception):
    pass


def entrainer(historiques: list[HistoriqueMateriel], chemin_modele: str,
              aujourd_hui: date | None = None) -> dict:
    df = construire_dataset(historiques, aujourd_hui)
    if len(df) < MIN_ECHANTILLONS or df["cible"].nunique() < 2:
        raise DonneesInsuffisantes(
            f"{len(df)} échantillons, {int(df['cible'].sum()) if len(df) else 0} pannes : "
            "historique insuffisant. Déclarez des pannes ou lancez `flask seed-demo`."
        )

    X, y = df[CAT_FEATURES + NUM_FEATURES], df["cible"]
    # Split par matériel (et non par ligne) pour éviter qu'un même matériel
    # se retrouve à la fois dans le train et le test.
    ids = df["materiel_id"].unique()
    ids_train, ids_test = train_test_split(ids, test_size=0.25, random_state=42)
    tr, te = df["materiel_id"].isin(ids_train), df["materiel_id"].isin(ids_test)

    pipe = creer_pipeline()
    pipe.fit(X[tr], y[tr])

    metriques = {"n_echantillons": int(len(df)), "taux_panne": round(float(y.mean()), 3)}
    if y[te].nunique() == 2:
        proba = pipe.predict_proba(X[te])[:, 1]
        metriques["roc_auc_test"] = round(float(roc_auc_score(y[te], proba)), 3)
        rapport = classification_report(y[te], pipe.predict(X[te]), output_dict=True, zero_division=0)
        metriques["precision_panne"] = round(rapport["1"]["precision"], 3)
        metriques["rappel_panne"] = round(rapport["1"]["recall"], 3)

    # Modèle final ré-entraîné sur toutes les données
    pipe.fit(X, y)
    noms = pipe.named_steps["preprocess"].get_feature_names_out()
    importances = sorted(
        zip(noms, pipe.named_steps["rf"].feature_importances_), key=lambda t: -t[1])
    metriques["importances"] = {n.split("__")[-1]: round(float(v), 3) for n, v in importances[:8]}
    metriques["entraine_le"] = (aujourd_hui or date.today()).isoformat()

    Path(chemin_modele).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipe, "metriques": metriques}, chemin_modele)
    return metriques


def charger(chemin_modele: str) -> dict | None:
    p = Path(chemin_modele)
    return joblib.load(p) if p.exists() else None


def niveau_risque(proba: float) -> str:
    if proba >= 0.6:
        return "élevé"
    if proba >= 0.3:
        return "moyen"
    return "faible"


def predire(historiques: list[HistoriqueMateriel], chemin_modele: str,
            aujourd_hui: date | None = None) -> list[dict]:
    aujourd_hui = aujourd_hui or date.today()
    bundle = charger(chemin_modele)
    lignes = [f for h in historiques if (f := features_a_date(h, aujourd_hui))]
    if not lignes:
        return []
    df = pd.DataFrame(lignes)
    probas = (bundle["pipeline"].predict_proba(df[CAT_FEATURES + NUM_FEATURES])[:, 1]
              if bundle else np.full(len(df), np.nan))

    types = {h.materiel_id: h.type for h in historiques}
    resultats = []
    for f, p in zip(lignes, probas):
        vie = duree_vie_ans(types[f["materiel_id"]])
        ratio = f["age_jours"] / (vie * 365)
        res = {
            "materiel_id": int(f["materiel_id"]),
            "age_ans": round(f["age_jours"] / 365, 1),
            "duree_vie_ans": vie,
            "obsolescence_pct": round(min(ratio, 9.99) * 100),
            "obsolete": ratio >= 1,
            "nb_pannes": int(f["nb_pannes_passees"]),
        }
        if not np.isnan(p):
            res["proba_panne_180j"] = round(float(p), 3)
            res["risque"] = niveau_risque(float(p))
        else:
            res["proba_panne_180j"] = None
            res["risque"] = "modèle non entraîné"
        resultats.append(res)
    return sorted(resultats, key=lambda r: -(r["proba_panne_180j"] or 0))
