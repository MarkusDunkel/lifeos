"""Register ``lifeos teams-files sync --commit`` as a Windows scheduled task.

Triggers: logon (after 10 minutes, so the OneDrive client has caught up) and daily at
07:00; a missed daily run starts as soon as the PC is on. The task runs as the current
user, without admin rights and without a console window.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from lifeos.modules.jobsearch.autostart import (
    AutostartError,
    _schtasks,
    current_user,
    windowless_python,
)

TASK_NAME = r"LifeOS\Teams files sync"

__all__ = ["TASK_NAME", "AutostartError", "install", "task_xml", "uninstall"]


def task_xml(python: Path, env_file: Path, workdir: Path, user: str, start: date) -> str:
    args = f'-m lifeos.cli teams-files sync --commit --env-file "{env_file}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>LifeOS: mirror the synced Teams files into the vault once a day.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT10M</Delay>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>{start.isoformat()}T07:00:00</StartBoundary>
      <Enabled>true</Enabled>
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
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
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
    workdir = Path(__file__).resolve().parents[4]  # backend/ (src/lifeos/connectors/folder)
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
