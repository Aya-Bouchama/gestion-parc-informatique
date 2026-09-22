import io
from datetime import date

import pandas as pd

from app.services import import_service


def _post(client, contenu, nom, simulation=False):
    return client.post(f"/api/v1/import?simulation={int(simulation)}",
                       data={"fichier": (io.BytesIO(contenu), nom)}, content_type="multipart/form-data")


def _xlsx(materiels, pannes=None):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(materiels).to_excel(w, sheet_name="Materiels", index=False)
        if pannes is not None:
            pd.DataFrame(pannes).to_excel(w, sheet_name="Pannes", index=False)
    return buf.getvalue()


def test_modele_telechargeable_et_importable(client):
    r = client.get("/api/v1/import/modele")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    rep = _post(client, r.data, "modele.xlsx").get_json()
    assert rep["crees"] == 2 and rep["pannes_ajoutees"] == 1 and rep["erreurs"] == []
    assert client.get("/api/v1/materiels").get_json()["total"] == 2


def test_simulation_n_ecrit_rien(client):
    contenu = import_service.generer_modele()
    rep = _post(client, contenu, "m.xlsx", simulation=True).get_json()
    assert rep["simulation"] and rep["crees"] == 2
    assert client.get("/api/v1/materiels").get_json()["total"] == 0


def test_csv_francais_colonnes_libres(client):
    csv = ("N° de série;Type matériel;Marque;Salle;Code GRESA;Date;Quantité\n"
           "ABC001;PC;Dell;CDI;01234A;15/03/2022;4\n"
           "ABC002;Écran;Samsung;CDI;01234A;2022-03-15;\n").encode("utf-8")
    rep = _post(client, csv, "donnees.csv").get_json()
    assert rep["crees"] == 2 and rep["erreurs"] == [], rep
    items = {m["numero_serie"]: m for m in client.get("/api/v1/materiels").get_json()["items"]}
    assert items["ABC001"]["nobre_materiel"] == 4 and items["ABC002"]["nobre_materiel"] == 1
    assert items["ABC001"]["modele"] == "Dell" and items["ABC001"]["emplacement"] == "CDI"


def test_lignes_invalides_signalees_sans_bloquer(client):
    contenu = _xlsx([
        {"Type": "PC", "Numéro de série": "OK1", "Modèle": "x", "Emplacement": "y",
         "ID GRESA": "01A", "Date de distribution": date(2022, 1, 1), "Nombre de matériel": 1},
        {"Type": "PC", "Numéro de série": "KO1", "Modèle": "x", "Emplacement": "y",
         "ID GRESA": "01A", "Date de distribution": "31/02/2022", "Nombre de matériel": 1},
        {"Type": "PC", "Numéro de série": "OK1", "Modèle": "x", "Emplacement": "y",
         "ID GRESA": "01A", "Date de distribution": date(2022, 1, 1), "Nombre de matériel": 1},
        {"Type": None, "Numéro de série": None, "Modèle": None, "Emplacement": None,
         "ID GRESA": None, "Date de distribution": None, "Nombre de matériel": None},
    ])
    rep = _post(client, contenu, "f.xlsx").get_json()
    assert rep["crees"] == 1 and rep["lignes"] == 3
    assert [(e["ligne"], list(e["erreurs"])) for e in rep["erreurs"]] == [
        (3, ["date_distribution"]), (4, ["numero_serie"])]


def test_mise_a_jour_ajoute_historique(client):
    base = {"Type": "PC", "Numéro de série": "SN1", "Modèle": "x", "Emplacement": "Salle 1",
            "ID GRESA": "01A", "Date de distribution": date(2021, 1, 1), "Nombre de matériel": 1}
    _post(client, _xlsx([base]), "a.xlsx")
    rep = _post(client, _xlsx([{**base, "ID GRESA": "02B", "Date de distribution": date(2023, 5, 1),
                                "Emplacement": "Salle 2"}],
                              pannes=[{"Numéro de série": "SN1", "Date de la panne": date(2024, 2, 1),
                                       "Description": "écran"},
                                      {"Numéro de série": "INCONNU", "Date de la panne": date(2024, 2, 1),
                                       "Description": ""}]), "b.xlsx").get_json()
    assert rep["mis_a_jour"] == 1 and rep["distributions_ajoutees"] == 1 and rep["pannes_ajoutees"] == 1
    assert rep["erreurs"][0]["feuille"] == "Pannes"
    m = client.get("/api/v1/materiels").get_json()["items"][0]
    assert m["id_gresa"] == "02B" and m["emplacement"] == "Salle 2" and m["nb_distributions"] == 2


def test_export_puis_reimport(client):
    _post(client, import_service.generer_modele(), "m.xlsx")
    r = client.get("/api/v1/export")
    assert r.status_code == 200
    feuilles = pd.read_excel(io.BytesIO(r.data), sheet_name=None)
    assert set(feuilles) == {"Materiels", "Pannes", "Historique distributions"}
    rep = _post(client, r.data, "export.xlsx").get_json()   # ré-import idempotent
    assert rep["crees"] == 0 and rep["distributions_ajoutees"] == 0 and rep["pannes_ajoutees"] == 0


def test_fichier_invalide(client):
    assert _post(client, b"xx", "f.pdf").status_code == 422
    assert _post(client, b"a;b\n1;2\n", "f.csv").status_code == 422
