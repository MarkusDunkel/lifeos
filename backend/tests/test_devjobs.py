from __future__ import annotations

import json

import httpx
import pytest

from lifeos.connectors.devjobs.client import DevjobsClient, DevjobsError
from lifeos.connectors.devjobs.parse import ParseError, parse_posting, sitemap_ids

ID1 = "a" * 32
ID2 = "b" * 32
ID3 = "c" * 32

ROBOTS = """User-agent: *
Allow: /
Disallow: /jobs/search?
Disallow: /api/
"""

SITEMAP = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://devjobs.at/job/{ID1}</loc></url>
  <url><loc>https://devjobs.at/job/{ID2}</loc></url>
  <url><loc>https://devjobs.at/job/{ID1}</loc></url>
  <url><loc>https://devjobs.at/team/example</loc></url>
</urlset>"""


def page(posting_id: str, **overrides) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Software Developer",
        "datePosted": "2026-11-01",
        "validThrough": "2027-01-01",
        "hiringOrganization": {"@type": "Organization", "name": "Example GmbH"},
        "jobLocation": [
            {
                "@type": "Place",
                "address": {
                    "addressLocality": "Graz",
                    "addressRegion": "Steiermark",
                    "addressCountry": "AT",
                    "latitude": 47.07,
                    "longitude": 15.44,
                },
            }
        ],
        "employmentType": "FULL_TIME",
        "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": "EUR",
            "value": {"@type": "QuantitativeValue", "unitText": "MONTH", "value": 3500},
        },
        "skills": "Python, SQL, Docker",
        "jobBenefits": "Home Office, Jobrad",
        "industry": "IT",
        "description": (
            "&lt;h2&gt;Deine Rolle&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;Build things&lt;/li&gt;&lt;/ul&gt;"
        ),
    } | overrides
    blob = json.dumps(data)
    return f'<html><head><script type="application/ld+json">{blob}</script></head></html>'


def test_sitemap_ids_are_unique_job_ids() -> None:
    assert sitemap_ids(SITEMAP) == [ID1, ID2]


def test_parse_posting_reads_job_posting_json_ld() -> None:
    p = parse_posting(ID1, page(ID1))
    assert (p.source, p.id, p.url) == ("devjobs", ID1, f"https://devjobs.at/job/{ID1}")
    assert p.title == "Software Developer" and p.company == "Example GmbH"
    assert p.locations[0].city == "Graz" and p.locations[0].lat == pytest.approx(47.07)
    assert p.salary is not None and p.salary.unit == "MONTH" and p.salary.yearly() == 49000
    assert p.skills == ["Python", "SQL", "Docker"]
    assert p.benefits == ["Home Office", "Jobrad"]
    assert p.employment_type == ["FULL_TIME"]
    assert "## Deine Rolle" in p.description and "Build things" in p.description


def test_parse_posting_without_job_posting_fails() -> None:
    with pytest.raises(ParseError):
        parse_posting(ID1, "<html>nothing here</html>")


def test_parse_posting_tolerates_missing_fields() -> None:
    p = parse_posting(ID1, page(ID1, baseSalary=None, jobLocation=None, skills=None))
    assert p.salary is None and p.locations == [] and p.skills == []


class Site:
    def __init__(self, robots: str = ROBOTS) -> None:
        self.robots = robots
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        assert "LifeOS" in request.headers["user-agent"]
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if path == "/sitemap-job.xml":
            return httpx.Response(200, text=SITEMAP)
        if path == f"/job/{ID3}":
            return httpx.Response(404)
        if path.startswith("/job/"):
            return httpx.Response(200, text=page(path.rsplit("/", 1)[1]))
        return httpx.Response(500)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(site: Site, t: FakeTime | None = None) -> DevjobsClient:
    t = t or FakeTime()
    return DevjobsClient(httpx.MockTransport(site), sleep=t.sleep, clock=t.clock)


def test_client_fetches_sitemap_and_postings_politely() -> None:
    site, t = Site(), FakeTime()
    c = client(site, t)
    c.check_robots()
    assert c.sitemap_ids() == [ID1, ID2]
    assert c.posting(ID2).id == ID2
    assert site.calls == ["/robots.txt", "/sitemap-job.xml", f"/job/{ID2}"]
    assert t.slept and all(s >= 1.0 for s in t.slept)  # at least one second between requests


def test_client_refuses_when_robots_disallows_postings() -> None:
    c = client(Site(robots="User-agent: *\nDisallow: /job/\n"))
    with pytest.raises(DevjobsError) as err:
        c.check_robots()
    assert err.value.status == 409


def test_client_never_requests_search_pages() -> None:
    site = Site()
    c = client(site)
    c.check_robots()
    c.sitemap_ids()
    assert not any(call.startswith("/jobs/search") for call in site.calls)


def test_client_reports_missing_posting_and_blocks() -> None:
    c = client(Site())
    with pytest.raises(DevjobsError) as err:
        c.posting(ID3)
    assert err.value.status == 404

    blocked = DevjobsClient(
        httpx.MockTransport(lambda r: httpx.Response(403)), sleep=lambda s: None
    )
    with pytest.raises(DevjobsError) as err:
        blocked.sitemap_ids()
    assert err.value.status == 403
