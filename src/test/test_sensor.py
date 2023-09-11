from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)


def test_get_sensor_names():
    response = client.get("/api/sensor/names")
    assert response.status_code == 200
    print(response.json())


def test_get_sensor_name():
    response = client.get("/api/sensor/name?id=0")
    assert response.status_code == 200
    print(response.json())


def test_restart():
    response = client.post("/api/sensor/restart?id=0")
    assert response.status_code == 200


def test_get_single_measurement():
    response = client.get("/api/sensor/single_measurement?id=0")
    assert response.status_code == 200
    print(response.json())


def test_get_multiple_measurement():
    with client.stream(
        method="get", url="/api/sensor/multiple_measurement?id=0&interval=3&count=3"
    ) as r:
        for line in r.iter_lines():
            print(line)
        assert r.status_code == 200
