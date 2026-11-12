from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifeos.core.auth import hash_password
from lifeos.core.config import Settings
from lifeos.main import create_app

PASSWORD = "correct horse battery staple"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    kb = root / "ready" / "knowledge"
    (kb / "Studium").mkdir(parents=True)
    (kb / "Studium" / "BWL.md").write_text(
        "# Betriebswirtschaftslehre\n\nGrundlagen der Kostenrechnung. Siehe [[Kosten]].\n",
        encoding="utf-8",
    )
    (kb / "Kosten.md").write_text("# Kosten\n\nFixkosten und variable Kosten.\n", encoding="utf-8")
    (kb / ".obsidian").mkdir()
    return root


@pytest.fixture
def settings(vault_root: Path) -> Settings:
    return Settings(
        vault_root=vault_root,
        session_secret="x" * 40,
        password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    c = TestClient(create_app(settings))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


@pytest.fixture
def anon(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
