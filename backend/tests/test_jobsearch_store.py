from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from lifeos.connectors.devjobs import curl as curl_module
from lifeos.connectors.devjobs.curl import CurlTransport
from lifeos.core.vault import Vault
from lifeos.modules.jobsearch.dedup import match_key, normalize_company, normalize_title
from lifeos.modules.jobsearch.model import Location, Posting
from lifeos.modules.jobsearch.store import ROOT, JobStore


def make(pid: str, title: str, company: str = "Example GmbH", *cities: str) -> Posting:
    return Posting(
        source="devjobs",
        id=pid,
        url=f"https://devjobs.at/job/{pid}",
        title=title,
        company=company,
        locations=[Location(city=c) for c in (cities or ("Graz",))],
    )


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    (tmp_path / "vault").mkdir()
    return JobStore(Vault.open(tmp_path / "vault"))


# -- normalisation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Software Developer (m/w/d)", "Software Developer"),
        ("Software Developer (w/m/x)", "software developer"),
        ("Software Developer (all genders)", "Software-Developer"),
        ("Softwareentwickler:in (m/w/d)", "Softwareentwickler"),
        ("Softwareentwickler*in", "Softwareentwickler/-in"),
        ("Data Engineer (*all genders)", "Data Engineer m/f/d"),
    ],
)
def test_title_variants_normalise_alike(a: str, b: str) -> None:
    assert normalize_title(a) == normalize_title(b)


def test_different_titles_stay_different() -> None:
    assert normalize_title("Senior Developer") != normalize_title("Junior Developer")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Example GmbH", "EXAMPLE Ges.m.b.H."),
        ("Example GmbH & Co KG", "Example"),
        ("Example AG", "example"),
    ],
)
def test_company_legal_forms_are_ignored(a: str, b: str) -> None:
    assert normalize_company(a) == normalize_company(b)


def test_match_key_combines_company_and_title() -> None:
    assert match_key(make("a1", "Developer (m/w/d)", "Example GmbH")) == match_key(
        make("a2", "Developer", "Example")
    )


# -- duplicates -----------------------------------------------------------------------


def test_same_job_twice_is_a_duplicate_of_the_first(store: JobStore) -> None:
    result = store.add([make("a1", "Developer (m/w/d)"), make("a2", "Developer")])
    assert result == {"stored": 2, "added": 2, "duplicates": 1, "reposts": 0}
    index = store.index()
    assert "duplicateOf" not in index["devjobs:a1"]
    assert index["devjobs:a2"]["duplicateOf"] == "devjobs:a1"
    assert store.counts() == {"active": 1, "duplicates": 1, "reposts": 0, "gone": 0}


def test_same_title_in_another_city_is_a_different_job(store: JobStore) -> None:
    store.add([make("a1", "Developer", "Example", "Graz"), make("a2", "Developer", "X", "Wien")])
    store.add([make("a3", "Developer", "X", "Graz")])
    assert all("duplicateOf" not in e for e in store.index().values())


def test_shared_city_counts_as_same_place(store: JobStore) -> None:
    first = make("a1", "Developer", "Example", "Graz", "Wien")
    store.add([first, make("a2", "Developer", "Example", "Wien")])
    assert store.index()["devjobs:a2"]["duplicateOf"] == "devjobs:a1"


def test_duplicate_takes_over_when_the_first_goes_offline(store: JobStore) -> None:
    store.add([make("a1", "Developer"), make("a2", "Developer"), make("a3", "Developer")])
    store.diff("devjobs", ["a2", "a3"])  # a1 left the board
    index = store.index()
    assert index["devjobs:a1"]["goneAt"]
    assert "duplicateOf" not in index["devjobs:a2"]
    assert index["devjobs:a3"]["duplicateOf"] == "devjobs:a2"


def test_job_published_again_is_a_repost(store: JobStore) -> None:
    store.add([make("a1", "Developer")])
    store.diff("devjobs", [])  # offline
    result = store.add([make("a9", "Developer")])
    assert result["reposts"] == 1 and result["duplicates"] == 0
    entry = store.index()["devjobs:a9"]
    assert entry["repostOf"] == "devjobs:a1" and "duplicateOf" not in entry


def test_entries_without_match_key_are_backfilled(store: JobStore) -> None:
    """Postings imported before duplicate detection get linked on the next import."""
    store.add([make("a1", "Developer"), make("a2", "Developer")])
    index_file = store.vault.resolve(f"{ROOT}/index.json")
    old = json.loads(index_file.read_text(encoding="utf-8"))
    for entry in old.values():
        for field in ("match", "cities", "title", "company", "skills", "duplicateOf"):
            entry.pop(field, None)
    index_file.write_text(json.dumps(old), encoding="utf-8")

    store.add([make("a3", "Other job")])
    index = store.index()
    assert index["devjobs:a2"]["duplicateOf"] == "devjobs:a1"
    assert index["devjobs:a1"]["title"] == "Developer"


# -- retention ------------------------------------------------------------------------


def test_prune_deletes_postings_offline_for_long(store: JobStore) -> None:
    store.add([make("a1", "Old job"), make("a2", "Current job")])
    store.diff("devjobs", ["a2"])
    index_file = store.vault.resolve(f"{ROOT}/index.json")
    index = json.loads(index_file.read_text(encoding="utf-8"))
    index["devjobs:a1"]["goneAt"] = (datetime.now(UTC) - timedelta(days=366)).isoformat()
    index_file.write_text(json.dumps(index), encoding="utf-8")

    record = store.finish({"source": "devjobs"})
    assert record["pruned"] == 1
    assert "devjobs:a1" not in store.index() and store.posting("devjobs:a1") is None
    assert store.posting("devjobs:a2") is not None


def test_recently_gone_postings_are_kept(store: JobStore) -> None:
    store.add([make("a1", "Job")])
    store.diff("devjobs", [])
    assert store.prune() == 0 and "devjobs:a1" in store.index()


# -- curl transport -------------------------------------------------------------------


def test_curl_transport_passes_only_the_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd, capture_output, timeout):
        seen.append(cmd)
        out = b"<html>ok</html>" + curl_module._STATUS_MARK + b"200"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=b"")

    monkeypatch.setattr(curl_module.subprocess, "run", fake_run)
    client = httpx.Client(transport=CurlTransport(curl="curl"), headers={"User-Agent": "Test/1"})
    response = client.get("https://example.org/page")
    assert response.status_code == 200 and response.text == "<html>ok</html>"
    cmd = seen[0]
    assert cmd[cmd.index("--user-agent") + 1] == "Test/1"
    assert "--header" not in cmd and "-H" not in cmd  # no other headers, no disguise


def test_curl_transport_reports_status_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=b"no" + curl_module._STATUS_MARK + b"403"),
            subprocess.CompletedProcess([], 6, stdout=b"", stderr=b"Could not resolve host"),
        ]
    )
    monkeypatch.setattr(curl_module.subprocess, "run", lambda *a, **k: next(answers))
    client = httpx.Client(transport=CurlTransport(curl="curl"))
    assert client.get("https://example.org/").status_code == 403
    with pytest.raises(httpx.ConnectError):
        client.get("https://example.org/")
