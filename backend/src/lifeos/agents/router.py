"""API for the agents page: list the AI teammates and switch how they are paid."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lifeos.agents.admin import AgentAdmin, AgentAdminError


class BillingChange(BaseModel):
    billing: str
    revision: str | None = None  # revision of the agent file the change is based on


def build_router(admin: AgentAdmin) -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    @router.get("")
    def overview() -> dict:
        return admin.overview()

    @router.put("/{name}/billing")
    def set_billing(name: str, change: BillingChange) -> dict:
        try:
            return admin.set_billing(name, change.billing, change.revision)
        except AgentAdminError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    return router
