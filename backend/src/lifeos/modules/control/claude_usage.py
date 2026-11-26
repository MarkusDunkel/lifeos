"""PC side: report the tokens of interactive Claude Code sessions to the control room.

Claude Code keeps a transcript per session (and per subagent) as JSONL under
``~/.claude/projects``. Every assistant message carries its ``usage``; a message streamed in
several entries repeats the same id, so each id counts once. The report sends hourly totals
per transcript file; the server replaces a bucket it already has, so re-sending is safe.

Only files that changed since the last run are read again (``claude-usage.json`` next to
the env file keeps their size and mtime). Nothing but numbers, the model and the folder
name of the project leaves the PC.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

from lifeos.modules.control.reporter import ReporterSettings, load_settings, reported
from lifeos.modules.jobsearch.autostart import (
    AutostartError,
    _schtasks,
    current_user,
    windowless_python,
)

log = logging.getLogger("lifeos.control.claude_usage")

PROCESS = "claude-usage"
TASK_NAME = r"LifeOS\Claude Code usage"
CURSOR_FILE = "claude-usage.json"
CHUNK = 1000
USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
# working directories of LifeOS's own headless calls: billed to the server's ledger
SKIP_CWD_PARTS = ("lifeos-claude-",)


def projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def parse(path: Path) -> list[dict]:
    """Hourly buckets of one transcript: ``{source, hour, tokens, model, project}``."""
    messages: dict[str, tuple[datetime, int, str]] = {}
    project = ""
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            cwd = str(entry.get("cwd") or "")
            if any(part in cwd for part in SKIP_CWD_PARTS):
                return []
            project = project or Path(cwd.replace("\\", "/")).name
            message = entry.get("message") or {}
            usage = message.get("usage") or {}
            model = str(message.get("model") or "")
            if model.startswith("<") or not entry.get("timestamp"):
                continue  # synthetic messages carry no real usage
            tokens = sum(int(usage.get(k) or 0) for k in USAGE_KEYS)
            at = datetime.fromisoformat(str(entry["timestamp"]).replace("Z", "+00:00"))
            key = str(message.get("id") or entry.get("uuid") or len(messages))
            messages[key] = (at, tokens, model)  # the last entry of a message wins
    hours: dict[str, list] = defaultdict(lambda: [0, ""])
    for at, tokens, model in messages.values():
        hour = at.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()
        hours[hour][0] += tokens
        hours[hour][1] = model or hours[hour][1]
    return [
        {"source": path.stem, "hour": hour, "tokens": t, "model": m, "project": project}
        for hour, (t, m) in sorted(hours.items())
        if t > 0
    ]


def changed_files(root: Path, cursor: dict[str, list]) -> list[tuple[Path, list]]:
    out = []
    for path in sorted(root.rglob("*.jsonl")):
        try:
            stat = path.stat()
        except OSError:
            continue
        mark = [stat.st_size, int(stat.st_mtime)]
        if cursor.get(str(path)) != mark:
            out.append((path, mark))
    return out


def upload(buckets: list[dict], settings: ReporterSettings, transport=None) -> None:
    if not settings.url or not settings.control_report_token:
        raise RuntimeError("LIFEOS_CONTROL_SERVER_URL / LIFEOS_CONTROL_REPORT_TOKEN not set")
    url = settings.url.rstrip("/") + f"/api/control/report/{PROCESS}/tokens"
    headers = {"Authorization": f"Bearer {settings.control_report_token}"}
    with httpx.Client(timeout=30, transport=transport) as http:
        for i in range(0, len(buckets), CHUNK):
            r = http.post(url, json={"buckets": buckets[i : i + CHUNK]}, headers=headers)
            r.raise_for_status()


def report_usage(
    settings: ReporterSettings,
    cursor_path: Path,
    root: Path | None = None,
    transport=None,
) -> str:
    """Send what changed since the last run; returns a one-line summary."""
    root = root or projects_dir()
    if not root.is_dir():
        return f"no Claude Code transcripts at {root.name}"
    try:
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cursor = {}
    files = changed_files(root, cursor)
    buckets = [b for path, _ in files for b in parse(path)]
    if buckets:
        upload(buckets, settings, transport)
    for path, mark in files:  # only after the server took them
        cursor[str(path)] = mark
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps(cursor), encoding="utf-8")
    total = sum(b["tokens"] for b in buckets)
    return f"{len(files)} transcript(s) read, {len(buckets)} hour(s), {total:,} tokens sent"


def run(env_file: Path | None) -> int:
    """What the scheduled task runs: report the usage and the run itself."""
    settings = load_settings(env_file)
    cursor = (env_file.parent if env_file else Path.home() / ".lifeos") / CURSOR_FILE
    with reported(PROCESS, env_file) as result:
        result.message = report_usage(settings, cursor)
        result.status = "ok"
    log.info("claude usage: %s", result.message)
    return 0


# -- the scheduled task --------------------------------------------------------------------


def task_xml(python: Path, env_file: Path, workdir: Path, user: str, start: date) -> str:
    args = f'-m lifeos.cli claude-usage report --env-file "{env_file}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>LifeOS: report the tokens of Claude Code sessions every 15 minutes.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT2M</Delay>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>{start.isoformat()}T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT15M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(python))}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(workdir))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(env_file: Path) -> str:
    if os.name != "nt":
        raise AutostartError("the autostart is only available on Windows")
    env_file = env_file.resolve()
    if not env_file.is_file():
        raise AutostartError(f"env file not found: {env_file}")
    workdir = Path(__file__).resolve().parents[4]  # backend/ (src/lifeos/modules/control)
    xml = task_xml(windowless_python(), env_file, workdir, current_user(), date.today())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "task.xml"
        path.write_text(xml, encoding="utf-16")
        _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(path), "/F")
    return TASK_NAME


def uninstall() -> str:
    if os.name != "nt":
        raise AutostartError("the autostart is only available on Windows")
    _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    return TASK_NAME
