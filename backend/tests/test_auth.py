from fastapi.testclient import TestClient


def test_health_is_public(anon: TestClient) -> None:
    assert anon.get("/api/health").json()["status"] == "ok"


def test_api_requires_login(anon: TestClient) -> None:
    assert anon.get("/api/knowledge/tree").status_code == 401
    assert anon.get("/api/auth/session").json()["authenticated"] is False


def test_wrong_password_rejected(anon: TestClient) -> None:
    assert anon.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_login_logout_roundtrip(client: TestClient) -> None:
    assert client.get("/api/auth/session").json()["authenticated"] is True
    assert client.get("/api/knowledge/tree").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/knowledge/tree").status_code == 401


def test_tampered_cookie_rejected(anon: TestClient) -> None:
    anon.cookies.set("lifeos_session", "owner.forged.signature")
    assert anon.get("/api/knowledge/tree").status_code == 401
