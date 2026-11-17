"""Register ``lifeos jobsearch autorun`` as a Windows scheduled task.

Triggers: logon (after 2 minutes), wake from sleep (after 1 minute) and every hour.
The autorun itself decides whether to ask, so frequent triggers cost nothing. The task
runs as the current user, without admin rights and without a console window.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = r"LifeOS\Job search import"

# Event "the system has resumed from sleep".
_WAKE_QUERY = (
    '<QueryList><Query Id="0" Path="System"><Select Path="System">'
    "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]"
    "</Select></Query></QueryList>"
)


class AutostartError(RuntimeError):
    pass


def current_user() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    if not user:
        raise AutostartError("cannot determine the Windows user (USERNAME is not set)")
    return f"{domain}\\{user}" if domain else user


def windowless_python() -> Path:
    """``pythonw.exe`` of the running environment, so no console window pops up."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else exe


def task_xml(python: Path, env_file: Path, workdir: Path, user: str, start: date) -> str:
    args = f'-m lifeos.cli jobsearch autorun --env-file "{env_file}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>LifeOS: import new job postings once a day (asks first).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT2M</Delay>
    </LogonTrigger>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription>{escape(_WAKE_QUERY)}</Subscription>
      <Delay>PT1M</Delay>
    </EventTrigger>
    <CalendarTrigger>
      <StartBoundary>{start.isoformat()}T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1H</Interval>
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
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
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


def _schtasks(*args: str) -> str:
    proc = subprocess.run(["schtasks", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AutostartError((proc.stderr or proc.stdout).strip() or f"schtasks {args[0]} failed")
    return proc.stdout.strip()


def install(env_file: Path) -> str:
    if os.name != "nt":
        raise AutostartError("the autostart is only available on Windows")
    env_file = env_file.resolve()
    if not env_file.is_file():
        raise AutostartError(f"env file not found: {env_file}")
    workdir = Path(__file__).resolve().parents[4]  # backend/ (src/lifeos/modules/jobsearch)
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
