from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.support import esp_image


def test_password_gate_and_firmware_upload(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, admin_password="secret", scheduler_enabled=False))
    with TestClient(app) as client:
        assert client.get("/health").json()["ok"] is True
        assert client.get("/api/devices").status_code == 401
        assert client.post("/api/login", json={"password": "nope"}).status_code == 401
        assert client.post("/api/login", json={"password": "secret"}).status_code == 200
        assert client.get("/api/devices").json() == []
        rejected = client.put("/api/settings", json={"scan_subnets": "10.0.0.0/8"})
        assert rejected.status_code == 400
        saved = client.put("/api/settings", json={"scan_subnets": "192.168.4.0/24", "backup_retention": 4})
        assert saved.status_code == 200
        assert saved.json()["scan_subnets"] == "192.168.4.0/24"
        assert saved.json()["backup_retention"] == 4
        uploaded = client.post(
            "/api/firmware/bins",
            files={"file": ("WLED_0.15.0_ESP32.bin", esp_image(0), "application/octet-stream")},
        )
        assert uploaded.status_code == 200
        body = uploaded.json()
        assert body["chip_family"] == "esp32"
        assert body["family_source"] == "header"
        profiles = client.post("/api/firmware/profiles/defaults")
        assert profiles.status_code == 200
        names = {item["name"] for item in profiles.json()}
        assert "Dig-Uno ESP32" in names
        assert "Dig-Uno ESP8266" in names
