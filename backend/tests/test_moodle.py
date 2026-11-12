from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import lifeos.main
from lifeos.connectors.moodle.client import MoodleClient, MoodleError, _flatten, request_token
from lifeos.connectors.moodle.sync import MoodleAccess, MoodleSync, safe_name
from lifeos.core.auth import hash_password
from lifeos.core.config import Settings
from lifeos.core.extract import to_markdown
from lifeos.core.extract.text import front_matter
from lifeos.core.vault import Vault, VaultPathError
from lifeos.main import create_app

BASE = "https://moodle.example.invalid"
PASSWORD = "pw-for-tests"
NOW = 1_800_000_000
DUE = 1_791_064_740  # 2026-10-03 21:59 UTC = 23:59 in Vienna
KB = "ready/knowledge/Moodle"


def make_pdf(pages: list[str]) -> bytes:
    """A minimal PDF with one line of Helvetica text per page ("" = no text, like a scan)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode() if text else b""
        content_id = len(objects) + 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R"
            " /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
            " >> >> >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        kids.append(f"{len(objects) - 1} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n{body}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


def make_docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Assignment rules", level=1)
    d.add_paragraph("Hand in as PDF.")
    d.add_paragraph("First point", style="List Bullet")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Task", "Points"
    table.cell(1, 0).text, table.cell(1, 1).text = "One", "10"
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def make_pptx() -> bytes:
    from pptx import Presentation

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Cost accounting"
    slide.placeholders[1].text_frame.text = "Fixed and variable costs"
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def fileinfo(name: str, url: str, tm: int = 100, size: int = 1000, path: str = "/") -> dict:
    return {
        "type": "file",
        "filename": name,
        "filepath": path,
        "filesize": size,
        "fileurl": f"{BASE}/webservice/pluginfile.php/{url}",
        "timemodified": tm,
    }


class FakeMoodle:
    def __init__(self) -> None:
        self.files = {
            "script.pdf": make_pdf(["Introduction to costs", "Second page"]),
            "scan.pdf": make_pdf([""]),
            "slides.pptx": make_pptx(),
            "rules.docx": make_docx(),
            "index.html": b"<h2>Reading list</h2><p>Read <b>chapter 1</b>.</p>",
        }
        self.downloads: list[str] = []
        self.sections = [
            {
                "name": "",
                "summary": "<p>Welcome to the course.</p>",
                "modules": [
                    {
                        "id": 1,
                        "name": "News",
                        "modname": "forum",
                        "url": f"{BASE}/mod/forum/view.php?id=1",
                    },
                    {
                        "id": 2,
                        "name": "",
                        "modname": "label",
                        "description": "<p>Exam on <b>June 1</b></p>",
                    },
                    {
                        "id": 3,
                        "name": "Lecture script",
                        "modname": "resource",
                        "url": f"{BASE}/mod/resource/view.php?id=3",
                        "contents": [fileinfo("script.pdf", "script.pdf")],
                    },
                ],
            },
            {
                "name": "Week 1",
                "summary": "",
                "modules": [
                    {
                        "id": 4,
                        "name": "Material",
                        "modname": "folder",
                        "url": f"{BASE}/mod/folder/view.php?id=4",
                        "contents": [
                            fileinfo("slides.pptx", "slides.pptx"),
                            fileinfo("rules.docx", "rules.docx"),
                            fileinfo("scan.pdf", "scan.pdf"),
                            fileinfo("lecture.mp4", "lecture.mp4", size=900_000_000),
                        ],
                    },
                    {
                        "id": 5,
                        "name": "Reading list",
                        "modname": "page",
                        "url": f"{BASE}/mod/page/view.php?id=5",
                        "contents": [fileinfo("index.html", "index.html")],
                    },
                    {
                        "id": 6,
                        "name": "Homework 1",
                        "modname": "assign",
                        "url": f"{BASE}/mod/assign/view.php?id=6",
                        "dates": [
                            {
                                "label": "Opened:",
                                "timestamp": DUE - 7 * 86400,
                                "dataid": "allowsubmissionsfromdate",
                            },
                            {"label": "Due:", "timestamp": DUE, "dataid": "duedate"},
                        ],
                    },
                ],
            },
        ]

    def site_info(self) -> dict[str, Any]:
        return {"sitename": "Test Moodle", "fullname": "Test User", "userid": 7}

    def courses(self, userid: int) -> list[dict[str, Any]]:
        assert userid == 7
        return [
            {
                "id": 10,
                "fullname": "Cost Accounting: Basics",
                "shortname": "CA",
                "startdate": 0,
                "enddate": 0,
            },
            {
                "id": 11,
                "fullname": "Old Course",
                "shortname": "OLD",
                "startdate": 0,
                "enddate": 1_000_000_000,
            },
        ]

    def contents(self, courseid: int) -> list[dict[str, Any]]:
        assert courseid == 10
        return self.sections

    submitted: set[int] = set()

    def assignments(self, courseids: list[int]) -> list[dict[str, Any]]:
        assert courseids == [10]
        return [
            {
                "id": 60,
                "cmid": 6,
                "course": 10,
                "name": "Homework 1",
                "duedate": DUE,
                "cutoffdate": DUE + 86400,
                "allowsubmissionsfromdate": DUE - 7 * 86400,
                "intro": "<p>Write <b>two pages</b>.</p>",
            },
            {"id": 61, "cmid": 9, "course": 10, "name": "Optional essay", "duedate": 0},
        ]

    def quizzes(self, courseids: list[int]) -> list[dict[str, Any]]:
        return [
            {"id": 70, "coursemodule": 8, "course": 10, "name": "Quiz 1", "timeclose": DUE + 3600}
        ]

    def assignment_submitted(self, assignid: int) -> bool | None:
        return assignid in self.submitted

    def quiz_finished(self, quizid: int) -> bool | None:
        return quizid in self.submitted

    def download(self, fileurl: str) -> bytes:
        name = fileurl.rsplit("/", 1)[-1]
        self.downloads.append(name)
        return self.files[name]


@pytest.fixture
def moodle() -> FakeMoodle:
    return FakeMoodle()


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    (tmp_path / "vault" / "ready" / "knowledge").mkdir(parents=True)
    return Vault.open(tmp_path / "vault")


@pytest.fixture
def access(tmp_path: Path, moodle: FakeMoodle) -> MoodleAccess:
    a = MoodleAccess(BASE, tmp_path / "state", lambda url, token: moodle, lambda *a: "TOKEN")
    a.connect(token="TOKEN")
    return a


@pytest.fixture
def mirror(vault: Vault, access: MoodleAccess) -> MoodleSync:
    return MoodleSync(vault, access, "Moodle", clock=lambda: NOW, timezone="Europe/Vienna")


def read(vault: Vault, rel: str) -> str:
    return (vault.root / KB / rel).read_text(encoding="utf-8")


COURSE = "Cost Accounting- Basics"


# -- extraction ------------------------------------------------------------------


def test_extract_pdf_pages_and_scans() -> None:
    result = to_markdown("a.pdf", make_pdf(["Hello world", "Second"]))
    assert result.pages == 2 and not result.empty
    assert "## Page 1\n\nHello world" in result.body and "## Page 2" in result.body
    assert to_markdown("scan.pdf", make_pdf([""])).empty


def test_extract_docx_html_and_text() -> None:
    body = to_markdown("rules.docx", make_docx()).body
    assert "## Assignment rules" in body and "- First point" in body
    assert "| Task | Points |" in body
    html = to_markdown("p.html", b"<h2>Title</h2><p>Some <b>bold</b></p>").body
    assert "## Title" in html and "**bold**" in html
    assert to_markdown("n.txt", "Grüße".encode("cp1252")).body == "Grüße\n"


def test_extract_rejects_damaged_files() -> None:
    from lifeos.core.extract import ExtractError

    for name in ("x.pdf", "x.docx", "x.pptx"):
        with pytest.raises(ExtractError):
            to_markdown(name, b"garbage")
    with pytest.raises(ExtractError, match="unsupported"):
        to_markdown("x.zip", b"")


# -- sync ------------------------------------------------------------------------


def test_first_sync_mirrors_selected_courses(vault: Vault, moodle: FakeMoodle, mirror) -> None:
    report = mirror.sync()
    assert report.courses == 1  # the finished course is not selected by default
    assert sorted(report.created) == [
        f"Moodle/{COURSE}/00 Section 0/Lecture script.md",
        f"Moodle/{COURSE}/01 Week 1/Material/rules.md",
        f"Moodle/{COURSE}/01 Week 1/Material/scan.md",
        f"Moodle/{COURSE}/01 Week 1/Material/slides.md",
        f"Moodle/{COURSE}/01 Week 1/Reading list.md",
    ]
    assert report.skipped == ["Cost Accounting: Basics / lecture.mp4: .mp4 is not mirrored"]
    assert report.errors == []

    script = read(vault, f"{COURSE}/00 Section 0/Lecture script.md")
    meta = front_matter(script)
    assert meta["type"] == "moodle-file" and meta["moodle_id"] == "3:/script.pdf"
    assert meta["url"] == f"{BASE}/mod/resource/view.php?id=3" and meta["pages"] == 2
    assert "token" not in script.lower()
    assert "Introduction to costs" in script
    assert "No text found" in read(vault, f"{COURSE}/01 Week 1/Material/scan.md")
    assert "Cost accounting" in read(vault, f"{COURSE}/01 Week 1/Material/slides.md")
    assert "**chapter 1**" in read(vault, f"{COURSE}/01 Week 1/Reading list.md")

    overview = read(vault, f"{COURSE}/{COURSE}.md")
    assert front_matter(overview)["type"] == "moodle-course"
    assert "Welcome to the course." in overview and "- Exam on **June 1**" in overview
    assert f"[[Moodle/{COURSE}/00 Section 0/Lecture script|Lecture script]]" in overview
    assert f"[Homework 1]({BASE}/mod/assign/view.php?id=6) · assign" in overview
    assert "lecture.mp4 (.mp4 is not mirrored)" in overview


def test_second_sync_downloads_nothing(moodle: FakeMoodle, mirror) -> None:
    mirror.sync()
    moodle.downloads.clear()
    report = mirror.sync()
    assert moodle.downloads == []
    assert report.unchanged == 5 and not report.created and not report.skipped


def test_changed_file_is_fetched_again(vault: Vault, moodle: FakeMoodle, mirror) -> None:
    mirror.sync()
    moodle.files["script.pdf"] = make_pdf(["Revised costs"])
    moodle.sections[0]["modules"][2]["contents"][0]["timemodified"] = 200
    moodle.downloads.clear()
    report = mirror.sync()
    assert moodle.downloads == ["script.pdf"]
    assert report.updated == [f"Moodle/{COURSE}/00 Section 0/Lecture script.md"]
    assert "Revised costs" in read(vault, f"{COURSE}/00 Section 0/Lecture script.md")


def test_renamed_section_moves_notes(vault: Vault, moodle: FakeMoodle, mirror) -> None:
    mirror.sync()
    moodle.sections[1]["name"] = "Week one"
    report = mirror.sync()
    assert len(report.renamed) == 4
    assert (vault.root / KB / COURSE / "01 Week one" / "Reading list.md").is_file()
    assert not (vault.root / KB / COURSE / "01 Week 1" / "Reading list.md").exists()


def test_removed_file_keeps_its_note_flagged(vault: Vault, moodle: FakeMoodle, mirror) -> None:
    mirror.sync()
    moodle.sections[1]["modules"].pop(1)  # the page
    report = mirror.sync()
    assert report.deleted == [f"Moodle/{COURSE}/01 Week 1/Reading list.md"]
    assert front_matter(read(vault, f"{COURSE}/01 Week 1/Reading list.md"))["status"] == "deleted"
    assert mirror.sync().deleted == []


def test_course_selection(moodle: FakeMoodle, mirror) -> None:
    courses = mirror.courses()
    assert [(c["id"], c["in_progress"], c["selected"]) for c in courses] == [
        (10, True, True),
        (11, False, False),
    ]
    mirror.select([])
    assert mirror.sync().courses == 0
    assert not any(c["selected"] for c in mirror.courses())


def test_own_notes_are_never_overwritten(vault: Vault, mirror) -> None:
    mine = vault.root / KB / COURSE / "01 Week 1" / "Reading list.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("# My notes\n", encoding="utf-8")
    report = mirror.sync()
    assert any("different note already exists" in e for e in report.errors)
    assert mine.read_text(encoding="utf-8") == "# My notes\n"
    assert len(report.created) == 4


def test_broken_file_does_not_stop_the_sync(moodle: FakeMoodle, mirror) -> None:
    moodle.files["rules.docx"] = b"garbage"
    report = mirror.sync()
    assert len(report.created) == 4
    assert report.errors == [
        "Cost Accounting: Basics / rules.docx: "
        "not a readable .docx (password-protected or damaged?)"
    ]


def test_target_folder_must_stay_inside_the_knowledge_hub(vault: Vault) -> None:
    with pytest.raises(VaultPathError):
        MoodleSync(vault, None, "../notes")


def test_safe_names() -> None:
    assert safe_name("A: B / C? [1] #x") == "A- B - C- -1- -x"
    assert safe_name("...") == "untitled"


# -- access and client -------------------------------------------------------------


def test_access_keeps_only_the_token(tmp_path: Path, moodle: FakeMoodle) -> None:
    seen = []

    def token_factory(url: str, user: str, password: str) -> str:
        seen.append((url, user, password))
        return "TOKEN"

    access = MoodleAccess(BASE + "/", tmp_path / "state", lambda url, token: moodle, token_factory)
    assert not access.connected
    info = access.connect("me", "secret")
    assert info == {"url": BASE, "site": "Test Moodle", "user": "Test User", "userid": 7}
    stored = (tmp_path / "state" / "moodle-token.json").read_text()
    assert "TOKEN" in stored and "secret" not in stored
    assert seen == [(BASE, "me", "secret")]
    with pytest.raises(MoodleError, match="username and password"):
        access.connect("me", "")
    access.disconnect()
    assert not access.connected


def test_request_token_and_login_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        assert request.url.path == "/login/token.php" and form["service"] == "moodle_mobile_app"
        if form["password"] == "right":
            return httpx.Response(200, json={"token": "T1"})
        return httpx.Response(200, json={"error": "Invalid login"})

    transport = httpx.MockTransport(handler)
    assert request_token(BASE, "me", "right", transport) == "T1"
    with pytest.raises(MoodleError, match="Invalid login"):
        request_token(BASE, "me", "wrong", transport)
    with pytest.raises(MoodleError, match="https"):
        request_token("http://insecure.invalid", "me", "x", transport)


def test_client_calls_and_errors() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/webservice/pluginfile.php"):
            assert request.url.params["token"] == "T"
            return httpx.Response(200, content=b"PDFDATA")
        form = dict(httpx.QueryParams(request.content.decode()))
        calls.append(form)
        if form["wsfunction"] == "core_course_get_contents":
            return httpx.Response(200, json={"exception": "x", "errorcode": "invalidtoken"})
        return httpx.Response(200, json={"sitename": "S", "userid": 1})

    client = MoodleClient(BASE, "T", httpx.MockTransport(handler))
    assert client.site_info()["sitename"] == "S"
    assert calls[0]["wstoken"] == "T" and calls[0]["moodlewsrestformat"] == "json"
    with pytest.raises(MoodleError, match="connect again") as exc:
        client.contents(1)
    assert exc.value.status == 409
    assert client.download(f"{BASE}/webservice/pluginfile.php/1/x.pdf") == b"PDFDATA"
    with pytest.raises(MoodleError, match="another host"):
        client.download("https://elsewhere.invalid/webservice/pluginfile.php/1/x.pdf")


def test_flatten_params() -> None:
    assert _flatten({"courseids": [1, 2], "options": [{"name": "a", "value": True}]}) == {
        "courseids[0]": "1",
        "courseids[1]": "2",
        "options[0][name]": "a",
        "options[0][value]": "1",
    }


# -- API ---------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path, moodle: FakeMoodle, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "vault"
    (root / "ready" / "knowledge").mkdir(parents=True)
    state = tmp_path / "deploy-state"
    state.mkdir()
    monkeypatch.setattr(lifeos.main, "MoodleClient", lambda url, token: moodle)
    monkeypatch.setattr(
        "lifeos.connectors.moodle.sync.request_token", lambda url, user, password: "TOKEN"
    )
    settings = Settings(
        vault_root=root,
        session_secret="x" * 40,
        password_hash=hash_password(PASSWORD),
        moodle_url=BASE,
        state_dir=state,
    )
    c = TestClient(create_app(settings))
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return c


def test_api_connect_select_and_sync(api: TestClient, tmp_path: Path) -> None:
    status = api.get("/api/moodle/status").json()
    assert status["configured"] and not status["connected"]
    r = api.post("/api/moodle/connect", json={"username": "me", "password": "secret"})
    assert r.status_code == 200 and r.json()["user"] == "Test User"
    status = api.get("/api/moodle/status").json()
    assert status["connected"] and status["site"] == "Test Moodle" and "token" not in status
    courses = api.get("/api/moodle/courses").json()
    assert [c["id"] for c in courses if c["selected"]] == [10]
    assert api.put("/api/moodle/courses", json={"ids": [10]}).status_code == 200
    report = api.post("/api/moodle/sync").json()
    assert len(report["created"]) == 5
    assert api.get("/api/moodle/status").json()["files"] == 5
    # Neither the password nor the token end up in the vault.
    for path in (tmp_path / "vault").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "secret" not in text and "TOKEN" not in text


def test_api_without_configuration(client: TestClient) -> None:
    status = client.get("/api/moodle/status").json()
    assert not status["configured"] and "LIFEOS_MOODLE_URL" in status["missing"]
    r = client.post("/api/moodle/connect", json={"token": "x"})
    assert r.status_code == 400 and "LIFEOS_MOODLE_URL" in r.json()["detail"]


# -- dates and task facts -------------------------------------------------------------


def test_overview_shows_activity_dates_and_deadlines(
    vault: Vault, moodle: FakeMoodle, mirror
) -> None:
    report = mirror.sync()
    assert report.tasks == 3
    overview = read(vault, f"{COURSE}/{COURSE}.md")
    assert "· assign · **Due: 03.10.2026 23:59** (opens 26.09.2026 23:59)" in overview
    deadlines = overview.split("## Deadlines", 1)[1].split("\n## ", 1)[0]
    assert "- **03.10.2026 23:59** · [Homework 1]" in deadlines
    assert "- **04.10.2026 00:59** · [Quiz 1]" in deadlines
    assert "Optional essay" not in deadlines  # no due date


def test_task_facts_for_the_ai(vault: Vault, moodle: FakeMoodle, mirror) -> None:
    moodle.submitted = {70}
    mirror.sync()
    data = json.loads((vault.root / "inbox/moodle/tasks.json").read_text(encoding="utf-8"))
    assert data["timezone"] == "Europe/Vienna"
    by_key = {t["key"]: t for t in data["tasks"]}
    hw = by_key["moodle:assign:60"]
    assert hw["due"] == "2026-10-03T23:59+02:00" and hw["cutoff"] == "2026-10-04T23:59+02:00"
    assert hw["submitted"] is False and hw["url"] == f"{BASE}/mod/assign/view.php?id=6"
    assert hw["description"] == "Write **two pages**." and hw["shortname"] == "CA"
    assert by_key["moodle:quiz:70"]["submitted"] is True
    assert by_key["moodle:assign:61"]["due"] is None
    assert [t["key"] for t in data["tasks"]][-1] == "moodle:assign:61"  # undated last
    # Unchanged facts do not rewrite the file.
    before = (vault.root / "inbox/moodle/tasks.json").stat().st_mtime_ns
    mirror.sync()
    assert (vault.root / "inbox/moodle/tasks.json").stat().st_mtime_ns == before
