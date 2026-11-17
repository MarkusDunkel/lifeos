"""Parse devjobs.at pages: the job sitemap and the JSON-LD ``JobPosting`` of a posting page."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from lifeos.core.extract import html_to_markdown
from lifeos.modules.jobsearch.model import Location, Posting, Salary

SOURCE = "devjobs"
BASE_URL = "https://devjobs.at"
JOB_ID = re.compile(r"/job/([0-9a-f]{32})(?:[/?#]|$)")
_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


class ParseError(ValueError):
    pass


def sitemap_ids(xml: str) -> list[str]:
    """Posting ids in ``sitemap-job.xml``, in sitemap order, without duplicates."""
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)
    ids = (m.group(1) for loc in locs if (m := JOB_ID.search(html.unescape(loc))))
    return list(dict.fromkeys(ids))


def posting_url(posting_id: str) -> str:
    return f"{BASE_URL}/job/{posting_id}"


def parse_posting(posting_id: str, page: str) -> Posting:
    data = _job_posting(page)
    if data is None:
        raise ParseError(f"no JobPosting data on page {posting_id}")
    org = data.get("hiringOrganization") or {}
    return Posting(
        source=SOURCE,
        id=posting_id,
        url=posting_url(posting_id),
        title=_text(data.get("title")),
        company=_text(org.get("name") if isinstance(org, dict) else org),
        locations=_locations(data.get("jobLocation")),
        salary=_salary(data.get("baseSalary")),
        employment_type=_as_list(data.get("employmentType")),
        date_posted=_text(data.get("datePosted")) or None,
        valid_through=_text(data.get("validThrough")) or None,
        skills=_split(data.get("skills")),
        benefits=_split(data.get("jobBenefits")),
        industry=_text(data.get("industry")),
        description=html_to_markdown(html.unescape(_text(data.get("description")))),
    )


def _job_posting(page: str) -> dict[str, Any] | None:
    for blob in _LD_JSON.findall(page):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [_text(v) for v in values if _text(v)]


def _split(value: Any) -> list[str]:
    """``"SAP, SAP S/4Hana"`` or a list → clean list."""
    parts = [_text(v) for v in value] if isinstance(value, list) else _text(value).split(",")
    return [p.strip() for p in parts if p.strip()]


def _locations(value: Any) -> list[Location]:
    places = value if isinstance(value, list) else [value] if value else []
    out: list[Location] = []
    for place in places:
        if not isinstance(place, dict):
            continue
        addr = place.get("address") or {}
        geo = place.get("geo") or {}
        out.append(
            Location(
                city=_text(addr.get("addressLocality")),
                region=_text(addr.get("addressRegion")),
                country=_text(addr.get("addressCountry")),
                lat=_float(addr.get("latitude", geo.get("latitude"))),
                lon=_float(addr.get("longitude", geo.get("longitude"))),
            )
        )
    return out


def _salary(value: Any) -> Salary | None:
    if not isinstance(value, dict):
        return None
    inner = value.get("value")
    if isinstance(inner, dict):
        amount = _float(inner.get("value", inner.get("minValue")))
        unit = _text(inner.get("unitText")) or "YEAR"
    else:
        amount, unit = _float(inner), "YEAR"
    if amount is None:
        return None
    return Salary(amount=amount, unit=unit.upper(), currency=_text(value.get("currency")) or "EUR")


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
