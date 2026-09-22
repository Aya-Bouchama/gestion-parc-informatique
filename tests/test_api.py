from tests.conftest import PAYLOAD


def test_crud_complet(client):
    r = client.post("/api/v1/materiels", json=PAYLOAD)
    assert r.status_code == 201
    mid = r.get_json()["materiel_id"]
    assert r.get_json()["date_distribution"] == "2022-03-15"

    assert client.get("/api/v1/materiels").get_json()["total"] == 1
    assert client.get(f"/api/v1/materiels/serie/{PAYLOAD['numero_serie']}").status_code == 200

    r = client.put(f"/api/v1/materiels/{mid}", json={"emplacement": "CDI", "nobre_materiel": 5})
    assert r.status_code == 200
    assert r.get_json()["emplacement"] == "CDI" and r.get_json()["nobre_materiel"] == 5

    r = client.post(f"/api/v1/materiels/{mid}/distributions", json={"id_gresa": "09999B"})
    assert r.get_json()["nb_distributions"] == 2 and r.get_json()["id_gresa"] == "09999B"

    assert client.post(f"/api/v1/materiels/{mid}/pannes", json={"description": "écran"}).status_code == 201
    detail = client.get(f"/api/v1/materiels/{mid}").get_json()
    assert len(detail["historique_distributions"]) == 2 and len(detail["pannes"]) == 1

    assert client.get(f"/imprimer/{mid}").status_code == 200
    assert client.delete(f"/api/v1/materiels/{mid}").status_code == 204
    assert client.get(f"/api/v1/materiels/{mid}").status_code == 404


def test_validation(client):
    r = client.post("/api/v1/materiels", json={**PAYLOAD, "date_distribution": "32/13/2022", "nobre_materiel": -1})
    assert r.status_code == 422
    assert {"date_distribution", "nobre_materiel"} <= set(r.get_json()["details"])
    r = client.post("/api/v1/materiels", json={"type": "PC"})
    assert r.status_code == 422 and "numero_serie" in r.get_json()["details"]


def test_doublon_numero_serie(client):
    client.post("/api/v1/materiels", json=PAYLOAD)
    r = client.post("/api/v1/materiels", json=PAYLOAD)
    assert r.status_code == 422 and "numero_serie" in r.get_json()["details"]


def test_injection_sql_neutralisee(client):
    """Une charge d'injection est stockée comme simple texte (requêtes paramétrées ORM)."""
    evil = "X'; DROP TABLE Materiel; --"
    client.post("/api/v1/materiels", json={**PAYLOAD, "emplacement": evil})
    r = client.get("/api/v1/materiels?q=" + "' OR '1'='1")
    assert r.status_code == 200 and r.get_json()["total"] == 0
    items = client.get("/api/v1/materiels").get_json()["items"]
    assert items[0]["emplacement"] == evil


def test_compat_formulaire_prototype(client):
    """Les anciens noms de champs (type_materiel) et l'envoi en form-data restent acceptés."""
    data = {**PAYLOAD, "type_materiel": "Imprimante"}
    del data["type"]
    r = client.post("/api/v1/materiels", data=data)
    assert r.status_code == 201 and r.get_json()["type"] == "Imprimante"


def test_pages(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/health").get_json()["status"] == "ok"
    assert client.get("/api/v1/inexistant").get_json()["error"] == "not_found"
