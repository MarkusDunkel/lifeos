from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lifeos.modules.control.catalog import PC, SERVER
from lifeos.modules.control.service import Control
from lifeos.modules.control.tokens import series

REPORT_PREFIX = "/api/control/report/"


class UsageBucket(BaseModel):
    source: str = Field(min_length=1, max_length=200)  # transcript file, no path
    hour: str = Field(min_length=10, max_length=40)  # ISO time in that hour
    tokens: int = Field(ge=0)
    model: str = Field(default="", max_length=100)
    project: str = Field(default="", max_length=200)  # folder name, no path


class UsageReport(BaseModel):
    buckets: list[UsageBucket] = Field(max_length=5000)


class PcReport(BaseModel):
    status: Literal["ok", "skipped", "error"]
    message: str = Field(default="", max_length=2000)
    duration: float | None = Field(default=None, ge=0)
    # the app commit the task ran on and how its self-update went (lifeos.core.self_update)
    version: str | None = Field(default=None, pattern=r"^[0-9a-f]{4,40}$")
    update: str = Field(default="", max_length=300)
    update_ok: bool | None = Field(default=None, alias="updateOk")


def build_router(control: Control) -> APIRouter:
    router = APIRouter(prefix="/api/control", tags=["control"])

    @router.get("/status")
    def status() -> dict:
        """Traffic lights of every process, the flow diagram and the AI report."""
        return control.status()

    @router.post("/run/{process_id}")
    def run(process_id: str) -> dict:
        """Ask the worker to run one of its jobs at its next tick (within 30 s)."""
        process = control.catalog.process(process_id)
        if process is None or process.where != SERVER:
            raise HTTPException(404, "no such worker job")
        if not process.enabled:
            raise HTTPException(400, f"{process.label} is not set up")
        job = process.job or process_id
        asked = control.requests.request(job)
        control.invalidate()
        return {"process": process_id, "requestedAt": asked}

    @router.get("/tokens")
    def burned_tokens(range: Literal["24h", "7d", "30d"] = "24h") -> dict:  # noqa: A002
        """Tokens per bucket and mode (auto, semi, interactive) for the stacked chart."""
        return series(control.catalog, control.state_dir, control.tz, range)

    @router.post("/report/claude-usage/tokens")
    def claude_usage(body: UsageReport) -> dict:
        """Hourly Claude Code tokens from the PC (bearer LIFEOS_CONTROL_REPORT_TOKEN)."""
        return control.interactive.upsert([b.model_dump() for b in body.buckets])

    @router.post("/report/{process_id}")
    def report(process_id: str, body: PcReport) -> dict:
        """Result of a PC task (bearer LIFEOS_CONTROL_REPORT_TOKEN)."""
        process = control.catalog.process(process_id)
        if process is None or process.where != PC:
            raise HTTPException(404, "no such PC process")
        app = None
        if body.version or body.update or body.update_ok is not None:
            app = {"version": body.version, "update": body.update, "ok": body.update_ok}
        entry = control.pc.record(process_id, body.status, body.message, body.duration, app)
        control.invalidate()
        return entry

    return router
