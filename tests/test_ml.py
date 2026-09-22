from datetime import date, timedelta

from app.ml import maintenance as ml


def test_entrainement_et_prediction(app, client):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-demo", "--n", "120"]).exit_code == 0

    assert client.get("/api/v1/maintenance/modele").get_json()["entraine"] is False
    r = client.post("/api/v1/maintenance/entrainer")
    assert r.status_code == 200, r.get_json()
    m = r.get_json()
    assert m["roc_auc_test"] > 0.6  # le modèle apprend mieux que le hasard

    preds = client.get("/api/v1/maintenance/predictions").get_json()["items"]
    assert len(preds) == 120
    assert all(0 <= p["proba_panne_180j"] <= 1 for p in preds)
    assert {p["risque"] for p in preds} <= {"faible", "moyen", "élevé"}


def test_donnees_insuffisantes(client):
    assert client.post("/api/v1/maintenance/entrainer").status_code == 409


def test_pas_de_fuite_temporelle():
    """Les variables à la date T n'utilisent aucune panne postérieure à T."""
    ref = date(2024, 1, 1)
    h = ml.HistoriqueMateriel(1, "PC", [(date(2020, 1, 1), 1)], [date(2023, 6, 1), date(2024, 3, 1)])
    f = ml.features_a_date(h, ref)
    assert f["nb_pannes_passees"] == 1
    assert f["jours_depuis_derniere_panne"] == (ref - date(2023, 6, 1)).days
    assert ml.features_a_date(h, date(2019, 1, 1)) is None


def test_obsolescence():
    assert ml.duree_vie_ans("Vidéoprojecteur") == 6
    assert ml.duree_vie_ans("Truc inconnu") == ml.DUREE_VIE_DEFAUT
    h = ml.HistoriqueMateriel(1, "Portable", [(date.today() - timedelta(days=5 * 365), 1)], [])
    p = ml.predire([h], "/nonexistent.joblib")[0]
    assert p["obsolete"] is True and p["risque"] == "modèle non entraîné"
