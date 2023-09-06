from fastapi.testclient import TestClient
from .main import app

client = TestClient(app)

uid = "046DD5BC"


def test_list_cco():
    response = client.get("/api/lighting/list_cco")
    assert response.status_code == 200
    assert response.json() == {"address": uid}


def test_set_control_mode():
    response = client.put(
        f"/api/lighting/{uid}/control_mode",
        json={
            "analog": False,
            "button": True,
            "modbus": False,
            "bacnet": False,
            "debug": True,
        },
    )
    assert response.status_code == 200
    response = client.get(f"/api/lighting/{uid}/control_mode")
    assert response.status_code == 200
    assert response.json() == {
        "analog": False,
        "button": True,
        "modbus": False,
        "bacnet": False,
        "debug": True,
    }


def test_reset_control_priority():
    response = client.post(f"/api/lighting/{uid}/reset_control_mode")
    assert response.status_code == 200


def test_dim_broadcast():
    response = client.put(
        f"/api/lighting/{uid}/dim_broadcast", json={"dimming_levels": [550, 550, 0]}
    )
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/dim_broadcast")
    assert response.status_code == 200
    assert response.json() == {"dimming_levels": [550, 550, 0]}


def test_get_all_sta_groups():
    response = client.get(f"/api/lighting/{uid}/groups")
    assert response.status_code == 200


def test_set_txpower():
    response = client.put(f"/api/lighting/{uid}/tx_power?txpower=15")
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/tx_power")
    assert response.status_code == 200
    assert response.json() == {"txpower": 15}


def test_set_access_time():
    response = client.put(f"/api/lighting/{uid}/access_time?access_time=10")
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/access_time")
    assert response.status_code == 200
    assert response.json() == {"access_time": 10}


def test_set_frequency_band():
    response = client.put(f"/api/lighting/{uid}/band?band=1")
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/band")
    assert response.status_code == 200
    assert response.json() == {"band": 1}


def test_set_dimming_channel():
    response = client.put(f"/api/lighting/{uid}/dim_channel?channel_count=1")
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/dim_channel")
    assert response.status_code == 200
    assert response.json() == {"channel_count": 1}


def test_set_startup_control():
    response = client.put(
        f"/api/lighting/{uid}/startup_control",
        json={"is_enabled": True, "default_dimming": 50},
    )
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/startup_control")
    assert response.status_code == 200
    assert response.json() == {
        "is_enabled": True,
        "default_dimming": 50,
    }


def test_set_modbus_node_addres():
    response = client.put(f"/api/lighting/{uid}/modbus_rtu_node_address?address=42")
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/modbus_rtu_node_address")
    assert response.status_code == 200
    assert response.json() == {"address": 42}


def test_set_ip_address():
    response = client.put(
        f"/api/lighting/{uid}/ip_address",
        json={
            "dynamic": False,
            "address": "192.0.0.128",
            "netmask": "255.255.255.128",
            "gateway": "192.168.0.1",
        },
    )
    assert response.status_code == 200

    response = client.get(f"/api/lighting/{uid}/ip_address")
    assert response.status_code == 200
    assert response.json() == {
        "dynamic": False,
        "address": "192.0.0.128",
        "netmask": "255.255.255.128",
        "gateway": "192.168.0.1",
    }


def test_rebuild_whitelist():
    with client.websocket_connect(f"/api/lighting/{uid}/network_discovery") as ws:
        for _ in range(10):
            data = ws.receive_text()
            print(data)
        ws.send_text("STOP")
        for _ in range(2):
            data = ws.receive_text()
            print(data)
