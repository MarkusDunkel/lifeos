"""Traffic lights, computed from the catalog and the recorded runs at request time.

Fixed rules, no AI: a process is red when its last run failed, a precondition is missing
or its last success is older than its deadline; yellow when it is getting late or was
skipped for an unusual reason. Targets inherit the worst light of the processes that
write them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.modules.control.catalog import HOST, MANUAL, PC, SERVER, Catalog, Process
from lifeos.modules.control.checks import Check

OFF, OK, WARN, FAIL = "off", "ok", "warn", "fail"
RANK = {OFF: 0, OK: 1, WARN: 2, FAIL: 3}
WORKER_STALE = 10 * 60  # no job ran for this long: the worker is down
FAILED = ("error", "conflict")


def worst(levels: list[str], default: str = OFF) -> str:
    return max(levels, key=RANK.__getitem__, default=default)


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} days"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class ProcessHealth:
    id: str
    label: str
    where: str
    trigger: str
    enabled: bool
    level: str
    reason: str
    description: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    status: str | None = None
    message: str = ""
    last_run: str | None = None
    last_success: str | None = None
    history: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    run_path: str | None = None
    can_request: bool = False  # a worker job: "run now" queues it for the worker
    hint: str = ""
    mirror: bool = False
    system: bool = False
    ai: list[dict] = field(default_factory=list)  # agents (model, billing, runs today)


@dataclass
class Sources:
    """Everything the evaluation reads; the router and the worker fill it."""

    jobs: dict[str, dict] = field(default_factory=dict)  # state/jobs.json
    pc: dict[str, dict] = field(default_factory=dict)  # state/pc-jobs.json
    manual: dict[str, str | None] = field(default_factory=dict)  # process → last run
    deployer: str | None = None  # last heartbeat of the host deployer
    checks: dict[str, Check] = field(default_factory=dict)
    system_checks: list[Check] = field(default_factory=list)
    ai: dict[str, list[dict]] = field(default_factory=dict)  # process → agents it calls


def evaluate(catalog: Catalog, src: Sources, now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    worker_last = max(
        (d for d in (_parse(e.get("lastRun")) for e in src.jobs.values()) if d), default=None
    )
    worker_age = (now - worker_last).total_seconds() if worker_last else None
    server_jobs = [p for p in catalog.processes if p.where == SERVER and p.enabled]
    worker = _worker_check(worker_age, bool(server_jobs))

    processes = [_one(p, src, now, tz, worker) for p in catalog.processes]

    nodes = []
    for node in catalog.nodes:
        writers = [p for p in processes if node.id in p.outputs and p.enabled]
        level = worst([p.level for p in writers]) if node.kind == "target" else OK
        reason = ""
        if node.kind == "target":
            bad = [p.label for p in writers if p.level in (WARN, FAIL)]
            reason = f"affected by: {', '.join(bad)}" if bad else ""
        nodes.append({**asdict(node), "level": level if writers or node.kind != "target" else OFF,
                      "reason": reason})  # fmt: skip

    system = [asdict(worker), *(asdict(c) for c in src.system_checks)]
    active = [p for p in processes if p.enabled]
    levels = [p.level for p in active] + [_check_level(c) for c in [worker, *src.system_checks]]
    counts = {lvl: sum(1 for p in active if p.level == lvl) for lvl in (OK, WARN, FAIL)}
    return {
        "level": worst(levels, OK),
        "counts": counts,
        "checkedAt": now.isoformat(timespec="seconds"),
        "processes": [_camel(asdict(p)) for p in processes],
        "nodes": nodes,
        "system": system,
    }


def _camel(d: dict) -> dict:
    def conv(key: str) -> str:
        head, *rest = key.split("_")
        return head + "".join(part.title() for part in rest)

    return {conv(k): v for k, v in d.items()}


def _check_level(check: Check) -> str:
    return OK if check.ok else (WARN if check.ok is None else FAIL)


def _worker_check(age: float | None, needed: bool) -> Check:
    if not needed:
        return Check("worker", "Worker", True, "no worker jobs set up")
    if age is None:
        return Check("worker", "Worker", False, "the worker has not run any job yet",
                     fix="COMPOSE_PROFILES=worker docker compose up -d")  # fmt: skip
    if age > WORKER_STALE:
        return Check("worker", "Worker", False, f"no job ran for {ago(age)}",
                     fix="docker compose logs worker; docker compose up -d worker")  # fmt: skip
    return Check("worker", "Worker", True, f"last job {ago(age)} ago")


def _one(p: Process, src: Sources, now: datetime, tz: ZoneInfo, worker: Check) -> ProcessHealth:
    entry: dict[str, Any] = {}
    if p.where == SERVER:
        entry = src.jobs.get(p.id, {})
    elif p.where == PC:
        entry = src.pc.get(p.id, {})
    elif p.where == MANUAL:
        last = src.manual.get(p.id)
        entry = {"lastRun": last, "lastSuccess": last, "status": "ok" if last else None}
    elif p.where == HOST:
        entry = {"lastRun": src.deployer, "lastSuccess": src.deployer,
                 "status": "ok" if src.deployer else None}  # fmt: skip

    checks = [src.checks[c] for c in p.checks if c in src.checks]
    health = ProcessHealth(
        p.id, p.label, p.where, p.trigger, p.enabled, OFF, "not set up",
        description=p.description, inputs=list(p.inputs), outputs=list(p.outputs),
        status=entry.get("status"), message=entry.get("message", ""),
        last_run=entry.get("lastRun"), last_success=entry.get("lastSuccess"),
        history=entry.get("history", []), checks=[asdict(c) for c in checks],
        run_path=p.run_path, can_request=p.where == SERVER and p.enabled, hint=p.hint,
        mirror=p.mirror, system=p.system, ai=src.ai.get(p.id, []),
    )  # fmt: skip
    if not p.enabled:
        return health
    health.level, health.reason = _light(p, entry, checks, now, tz, worker)
    return health


def _light(
    p: Process, entry: dict, checks: list[Check], now: datetime, tz: ZoneInfo, worker: Check
) -> tuple[str, str]:
    missing = [c for c in checks if c.ok is False]
    if missing:
        c = missing[0]
        return FAIL, f"{c.label}: {c.message}" + (f" – {c.fix}" if c.fix else "")
    if p.where == SERVER and worker.ok is False:
        return FAIL, f"worker not running ({worker.message})"

    status = entry.get("status")
    message = entry.get("message", "")
    if status in FAILED:
        return FAIL, f"last run failed: {message}" if message else "last run failed"

    success = _parse(entry.get("lastSuccess"))
    last_run = _parse(entry.get("lastRun"))

    if p.daily_at:
        hours, minutes = (int(x) for x in p.daily_at.split(":"))
        local = now.astimezone(tz)
        slot = datetime.combine(local.date(), time(hours, minutes), tzinfo=tz)
        if slot > local:
            slot -= timedelta(days=1)
        if success and success >= slot:
            return _skipped(p, status, message) or (
                OK,
                f"up to date (last success {_when(success, tz)})",
            )
        overdue = (now - slot).total_seconds()
        if p.fail_after and overdue > p.fail_after:
            return FAIL, f"no successful run since {_when(slot, tz)}"
        if p.warn_after and overdue > p.warn_after:
            return WARN, f"due since {_when(slot, tz)}, not done yet"
        return OK, f"due at {_when(slot, tz)}"

    if success is None:
        if last_run is None:
            if p.where == MANUAL:
                return WARN, "never run"
            return WARN, "has not run yet"
        if p.where == PC and status == "skipped":
            return WARN, f"skipped: {message}" if message else "skipped"
        return FAIL, "never succeeded"
    age = (now - success).total_seconds()
    if p.fail_after and age > p.fail_after:
        return FAIL, f"last success {ago(age)} ago (deadline {ago(p.fail_after)})"
    if p.warn_after and age > p.warn_after:
        return WARN, f"last success {ago(age)} ago"
    return _skipped(p, status, message) or (OK, f"last success {ago(age)} ago")


def _skipped(p: Process, status: str | None, message: str) -> tuple[str, str] | None:
    if status != "skipped" or p.where == PC:
        return None
    if any(message.startswith(q) for q in p.quiet_skips):
        return None
    return WARN, f"skipped: {message}" if message else "skipped"


def _when(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return local.strftime("%a %H:%M")
