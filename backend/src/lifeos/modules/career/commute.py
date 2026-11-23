"""Commute times from the homes to the postings, estimated with OpenRouteService.

Car (``driving-car``) and bike (``cycling-regular``) durations are fetched with the matrix
API – one home as source, many posting locations as destinations – for every location that
lies within a home's radius (from the prefilter). Many postings share a location (a city
centre), so the cache is per home and rounded coordinate, not per posting:
``.lifeos/jobsearch/commute.json`` = ``{home label: {"lat,lon": {"car": min, "bike": min}}}``.

The worker job ``commute`` fills what is missing (``LIFEOS_ORS_API_KEY``; free plan: 500
matrix requests a day, 40 a minute). The job list shows the times beside the home labels and
the sorting by fit or recommendation weighs them softly (:func:`factor`): no penalty up to
the home's ``comfort_minutes``, linearly down to half the score at ``max_minutes``, half
beyond (flagged). Unknown times cost nothing.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from lifeos.core.vault import STATE, Vault, write_atomic
from lifeos.modules.career.prefilter import Fit, FitIndex, Home, load_homes

log = logging.getLogger("lifeos.career.commute")

CACHE_FILE = f"{STATE}/jobsearch/commute.json"
ORS_URL = "https://api.openrouteservice.org/v2/matrix/{profile}"
PROFILES = {"car": "driving-car", "bike": "cycling-regular"}
BATCH = 100  # destinations per request (the free plan allows 3500 routes per request)
PAUSE = 1.6  # seconds between requests (40 per minute)
MAX_REQUESTS = 60  # per round, well below the daily quota


def coord(lat: float, lon: float) -> str:
    return f"{lat:.3f},{lon:.3f}"


def factor(minutes: float | None, home: Home) -> float:
    """Soft weight of a commute: 1 up to comfort, 0.5 at the maximum and beyond."""
    if minutes is None or minutes <= home.comfort_minutes:
        return 1.0
    if minutes >= home.max_minutes:
        return 0.5
    span = max(home.max_minutes - home.comfort_minutes, 1.0)
    return 1.0 - 0.5 * (minutes - home.comfort_minutes) / span


class OrsClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=60)
        self.sleep = sleep
        self.requests = 0

    def durations(
        self, mode: str, home: Home, targets: list[tuple[float, float]]
    ) -> list[float | None]:
        """Minutes from ``home`` to each ``(lat, lon)`` target (None: no route)."""
        if self.requests:
            self.sleep(PAUSE)
        self.requests += 1
        body = {
            "locations": [[home.lon, home.lat]] + [[lon, lat] for lat, lon in targets],
            "sources": [0],
            "destinations": list(range(1, len(targets) + 1)),
            "metrics": ["duration"],
        }
        r = self.http.post(ORS_URL.format(profile=PROFILES[mode]), json=body,
                           headers={"Authorization": self.api_key})  # fmt: skip
        r.raise_for_status()
        row = (r.json().get("durations") or [[]])[0]
        return [round(d / 60, 1) if d is not None else None for d in row]


class Commutes:
    """The cache of commute times, read by the web and filled by the worker."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def read(self) -> dict[str, dict[str, dict[str, float | None]]]:
        try:
            return json.loads(self.vault.resolve(CACHE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def homes(self) -> dict[str, Home]:
        return {h.label: h for h in load_homes(self.vault)}

    def annotate(self, fit: Fit, cache: dict[str, Any] | None = None,
                 homes: dict[str, Home] | None = None) -> dict[str, Any]:  # fmt: skip
        """Commute per near home and the soft weight of the best one."""
        cache = self.read() if cache is None else cache
        homes = self.homes() if homes is None else homes
        per_home: dict[str, dict[str, Any]] = {}
        best: float | None = None
        weight = 1.0
        too_far = False
        for near in fit.homes:
            if near.lat is None or near.lon is None:
                continue
            times = (cache.get(near.label) or {}).get(coord(near.lat, near.lon))
            if not times:
                continue
            values = [t for t in (times.get("car"), times.get("bike")) if t is not None]
            quickest = min(values) if values else None
            home = homes.get(near.label)
            per_home[near.label] = {"car": times.get("car"), "bike": times.get("bike"),
                                    "tooFar": bool(home and quickest is not None
                                                   and quickest > home.max_minutes)}  # fmt: skip
            if quickest is not None and home and (best is None or quickest < best):
                best, weight = quickest, factor(quickest, home)
                too_far = quickest > home.max_minutes
        if fit.remote:  # a remote posting is not slowed down by the office location
            weight, too_far = 1.0, False
        return {"homes": per_home, "minutes": best, "factor": round(weight, 3), "tooFar": too_far}

    def fill(self, client: OrsClient, fit: FitIndex, keys: list[str],
             max_requests: int = MAX_REQUESTS) -> dict[str, int]:  # fmt: skip
        """Fetch the missing times for the near locations of ``keys``."""
        cache = self.read()
        homes = self.homes()
        wanted: dict[str, dict[str, tuple[float, float]]] = {}
        for f in fit.all(keys).values():
            for near in f.homes:
                if near.lat is None or near.lon is None or near.label not in homes:
                    continue
                c = coord(near.lat, near.lon)
                if c not in (cache.get(near.label) or {}):
                    wanted.setdefault(near.label, {})[c] = (round(near.lat, 3), round(near.lon, 3))
        fetched = failed = 0
        for label, targets in wanted.items():
            items = list(targets.items())
            for i in range(0, len(items), BATCH):
                if client.requests + len(PROFILES) > max_requests:
                    break
                chunk = items[i : i + BATCH]
                results: dict[str, dict[str, float | None]] = {c: {} for c, _ in chunk}
                try:
                    for mode in PROFILES:
                        minutes = client.durations(mode, homes[label], [t for _, t in chunk])
                        for (c, _), m in zip(chunk, minutes, strict=False):
                            results[c][mode] = m
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("commute for %s failed: %s", label, exc)
                    failed += len(chunk)
                    continue
                cache.setdefault(label, {}).update(results)
                fetched += len(chunk)
                write_atomic(self.vault.resolve(CACHE_FILE), json.dumps(cache))
        missing = sum(len(t) for t in wanted.values()) - fetched - failed
        return {"fetched": fetched, "failed": failed, "left": max(missing, 0),
                "requests": client.requests}  # fmt: skip
