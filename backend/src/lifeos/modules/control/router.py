from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lifeos.modules.control.catalog import PC, SERVER
from lifeos.modules.control.service import Control

REPORT_PREFIX = "/api/control/report/"


class PcReport(BaseModel):
    status: Literal["ok", "skipped", "error"]
    message: str = Field(default="", max_length=2000)
    duration: float | None = Field(default=None, ge=0)


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
        return {"process": process_id, "requestedAt": control.requests.request(job)}

    @router.post("/report/{process_id}")
    def report(process_id: str, body: PcReport) -> dict:
        """Result of a PC task (bearer LIFEOS_CONTROL_REPORT_TOKEN)."""
        process = control.catalog.process(process_id)
        if process is None or process.where != PC:
            raise HTTPException(404, "no such PC process")
        return control.pc.record(process_id, body.status, body.message, body.duration)

    return router
