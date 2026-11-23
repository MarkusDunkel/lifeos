"""Career API: the job profiles, their weights and the target curriculum."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter

from lifeos.agents.comments import Texts
from lifeos.core.vault import Vault, write_atomic
from lifeos.modules.career import curriculum
from lifeos.modules.career.profiles import OVERVIEW_FILE, PROFILES_DIR, CareerFiles


class CareerService:
    def __init__(self, vault: Vault, committer: Callable[[list[str], str], None] | None = None):
        self.vault = vault
        self.files = CareerFiles(vault)
        self.committer = committer

    def curriculum(self) -> curriculum.Curriculum:
        return curriculum.compute(self.files.profiles(), self.files.overview())

    def overview(self) -> dict:
        result = self.curriculum()
        return {
            "profilesDir": PROFILES_DIR,
            "overviewFile": OVERVIEW_FILE,
            "overviewExists": bool(self.files.overview_text()),
            "profiles": [asdict(p) for p in result.profiles],
            "items": [asdict(i) for i in result.items],
            "warnings": result.warnings,
        }

    def refresh(self) -> dict:
        """Rewrite the generated curriculum block in ``Overview.md``."""
        text = self.files.overview_text()
        if not text:
            return {"written": False}
        texts = Texts.load(self.vault)
        block = curriculum.block(self.curriculum(), texts, date.today().isoformat())
        new = curriculum.with_block(text, block)
        if new != text:
            write_atomic(self.vault.resolve(OVERVIEW_FILE), new)
            if self.committer:
                self.committer([OVERVIEW_FILE], "career: curriculum recalculated")
        return {"written": new != text}


def build_router(service: CareerService) -> APIRouter:
    router = APIRouter(prefix="/api/career", tags=["career"])

    @router.get("/overview")
    def overview() -> dict:
        return service.overview()

    @router.post("/curriculum/refresh")
    def refresh() -> dict:
        return service.refresh()

    return router
