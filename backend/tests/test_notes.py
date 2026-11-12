from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifeos.core.vault import Vault
from lifeos.modules.notes.schema import build_schema, infer, label_for
from lifeos.modules.notes.store import NotesStore

LONG = "First line\n\tindented detail with an umlaut: Grüße and a café"
RECORDS = [
    {
        "cells": {
            "body": LONG,
            "created": "2026-01-02T10:00:00.000Z",
            "due": "2026-02-01",
            "lastChanged": "2026-01-02T10:05:00.000Z",
            "project": "Alpha",
            "status": "open",
            "type": "Task",
        },
        "id": "11111111-aaaa-4aaa-8aaa-111111111111",
        "order": 3,
    },
    {
        "cells": {
            "body": "short",
            "created": "2026-01-03T09:00:00.000Z",
            "due": None,
            "lastChanged": "2026-01-03T09:00:00.000Z",
            "project": "Alpha",
            "type": "Info",
        },
        "id": "22222222-bbbb-4bbb-8bbb-222222222222",
        "order": 7,
    },
    {
        "cells": {
            "body": "third",
            "project": "Beta",
            "status": "open",
            "type": "Task",
        },
        "id": "33333333-cccc-4ccc-8ccc-333333333333",
        "order": 1,
    },
]
FOREIGN = "not json at all"


def line(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


@pytest.fixture
def notes_file(vault_root: Path) -> Path:
    path = vault_root / "ready/notes/notes-2026.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [line(RECORDS[0]), FOREIGN, line(RECORDS[1]), line(RECORDS[2])]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path


def test_list_and_schema(client: TestClient, notes_file: Path) -> None:
    data = client.get("/api/notes").json()
    assert [n["id"][:2] for n in data["notes"]] == ["11", "22", "33"]
    assert data["notes"][0]["cells"]["body"] == LONG
    assert data["notes"][0]["file"] == "notes-2026.jsonl"
    kinds = {f["key"]: f["kind"] for f in data["fields"]}
    assert kinds == {
        "body": "text",
        "project": "select",
        "status": "select",
        "type": "select",
        "due": "date",
        "created": "datetime",
        "lastChanged": "datetime",
    }
    fields = {f["key"]: f for f in data["fields"]}
    assert fields["type"]["options"] == ["Info", "Task"]
    assert fields["created"]["readonly"] is True
    assert fields["lastChanged"]["label"] == "Last changed"
    assert data["fields"][0]["key"] == "body"  # long text first


def test_update_rewrites_only_that_line(client: TestClient, notes_file: Path) -> None:
    before = notes_file.read_bytes().decode("utf-8").splitlines()
    note = client.get("/api/notes").json()["notes"][1]
    cells = {**note["cells"], "status": "open", "body": "edited", "created": "forged"}
    r = client.put(f"/api/notes/{note['id']}", json={"cells": cells, "revision": note["revision"]})
    assert r.status_code == 200
    updated = r.json()
    assert updated["cells"]["created"] == "2026-01-03T09:00:00.000Z"  # server-owned
    assert updated["cells"]["lastChanged"] != "2026-01-03T09:00:00.000Z"
    assert updated["revision"] != note["revision"]

    after = notes_file.read_bytes().decode("utf-8").splitlines()
    assert after[0] == before[0] and after[1] == FOREIGN and after[3] == before[3]
    stored = json.loads(after[2])
    assert stored["order"] == 7 and stored["cells"]["body"] == "edited"
    assert after[2] == line(stored)  # canonical: sorted keys, UTF-8 kept
    assert "Grüße" in after[0]


def test_stale_revision_conflicts(client: TestClient, notes_file: Path) -> None:
    note = client.get("/api/notes").json()["notes"][0]
    url = f"/api/notes/{note['id']}"
    first = client.put(url, json={"cells": note["cells"], "revision": note["revision"]})
    stale = client.put(url, json={"cells": note["cells"], "revision": note["revision"]})
    assert stale.status_code == 409
    assert stale.json()["currentRevision"] == first.json()["revision"]


def test_external_edit_of_other_note_does_not_conflict(
    client: TestClient, notes_file: Path
) -> None:
    notes = client.get("/api/notes").json()["notes"]
    lines = notes_file.read_text(encoding="utf-8").splitlines()
    lines[3] = line({**RECORDS[2], "cells": {**RECORDS[2]["cells"], "status": "done"}})
    notes_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = client.put(
        f"/api/notes/{notes[0]['id']}",
        json={"cells": notes[0]["cells"], "revision": notes[0]["revision"]},
    )
    assert r.status_code == 200


def test_create_and_delete(client: TestClient, notes_file: Path, vault_root: Path) -> None:
    r = client.post("/api/notes", json={"cells": {"type": "Task", "body": "new", "due": ""}})
    assert r.status_code == 200
    note = r.json()
    assert note["order"] == 8
    assert note["cells"]["due"] is None
    assert note["cells"]["created"] == note["cells"]["lastChanged"]
    year_file = vault_root / f"ready/notes/notes-{note['cells']['created'][:4]}.jsonl"
    assert note["id"] in year_file.read_text(encoding="utf-8")

    url = f"/api/notes/{note['id']}/delete"
    assert client.post(url, json={"revision": "stale"}).status_code == 409
    assert client.post(url, json={"revision": note["revision"]}).status_code == 200
    ids = [n["id"] for n in client.get("/api/notes").json()["notes"]]
    assert note["id"] not in ids and len(ids) == 3


def test_unknown_and_invalid_ids(client: TestClient, notes_file: Path) -> None:
    body = {"cells": {}, "revision": "x"}
    assert client.put("/api/notes/does-not-exist", json=body).status_code == 404
    assert client.put("/api/notes/..%2Fx", json=body).status_code in (404, 422)
    bad = client.post("/api/notes", json={"cells": {"type": {"nested": 1}}})
    assert bad.status_code == 400


def test_empty_vault_has_no_notes(client: TestClient) -> None:
    assert client.get("/api/notes").json() == {"fields": [], "notes": []}


def test_schema_overrides(client: TestClient, notes_file: Path) -> None:
    (notes_file.parent / "schema.json").write_text(
        json.dumps(
            {
                "fields": [
                    {"key": "status", "label": "State", "options": ["open", "done"]},
                    {"key": "project", "hidden": True},
                    {"key": "owner", "kind": "select", "options": ["me"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    fields = client.get("/api/notes").json()["fields"]
    assert [f["key"] for f in fields[:3]] == ["status", "project", "owner"]
    assert fields[0]["label"] == "State" and fields[0]["options"] == ["open", "done"]
    assert fields[1]["hidden"] is True
    assert fields[2]["kind"] == "select" and fields[2]["options"] == ["me"]


def test_invalid_schema_file_is_ignored(client: TestClient, notes_file: Path) -> None:
    (notes_file.parent / "schema.json").write_text("{broken", encoding="utf-8")
    assert client.get("/api/notes").status_code == 200


def test_inference_helpers(vault_root: Path) -> None:
    assert label_for("lastChanged") == "Last changed"
    assert label_for("due_date") == "Due date"
    store = NotesStore(Vault.open(vault_root))
    assert infer(store.list()) == [] and build_schema([], []) == []
