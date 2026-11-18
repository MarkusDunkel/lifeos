"""Runtime configuration, read from environment variables prefixed with ``LIFEOS_``.

Nothing personal or machine-specific lives in the code: paths, secrets and the
password hash all come from the deployment's ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIFEOS_", extra="ignore")

    vault_root: Path = Field(description="Absolute path to the vault directory.")
    session_secret: str = Field(min_length=32, description="Secret used to sign session cookies.")
    password_hash: str | None = Field(
        default=None, description="Argon2 hash of the login password (see `lifeos hash-password`)."
    )
    auth_disabled: bool = Field(
        default=False, description="Disable login entirely. Only for local development."
    )
    session_max_age_days: int = 30
    cookie_secure: bool = Field(
        default=False, description="Set the Secure flag on cookies (enable behind HTTPS)."
    )
    static_dir: Path | None = Field(
        default=None, description="Directory with the built frontend; served as SPA if set."
    )
    github_token: str | None = Field(
        default=None, description="GitHub token (scopes: repo, project) for the tickets sync."
    )
    github_repos: str = Field(
        default="", description="Comma-separated repositories to mirror, e.g. `me/board,me/app`."
    )
    github_projects: str = Field(
        default="",
        description=(
            "Comma-separated Projects v2 boards as `owner/number` whose fields the ticket "
            "mirror shows, e.g. `me/3,me/4`. The first is the primary project (what a flat "
            "`project:` in a ticket file refers to). The applications board is always added."
        ),
    )
    github_project: str | None = Field(
        default=None, description="Deprecated single board; used when github_projects is empty."
    )
    state_dir: Path | None = Field(
        default=None,
        description="Directory outside the vault for connector tokens (e.g. `deploy/state`).",
    )
    ms_client_id: str | None = Field(
        default=None, description="Microsoft Entra app (personal accounts) for OneDrive."
    )
    onedrive_folder: str | None = Field(
        default=None, description="OneDrive folder whose presentations are mirrored."
    )
    presentations_dir: str = Field(
        default="Presentations",
        description="Folder in the knowledge hub that receives the presentation mirrors.",
    )

    moodle_url: str | None = Field(
        default=None, description="Moodle site, e.g. `https://moodle.example.edu`."
    )
    moodle_dir: str = Field(
        default="Moodle",
        description="Folder in the knowledge hub that receives the course mirrors.",
    )

    timezone: str = Field(
        default="UTC", description="Time zone for due dates, e.g. `Europe/Vienna`."
    )

    moodle_sync_at: str | None = Field(
        default=None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description=(
            "Daily time (HH:MM, in LIFEOS_TIMEZONE) at which `lifeos worker` syncs Moodle and "
            "commits the result as `moodle: …`. Unset disables the daily job."
        ),
    )

    mcp_path_token: str | None = Field(
        default=None,
        min_length=32,
        description=(
            "Enables the MCP server at /mcp/<token>/ (study tools for ChatGPT and other "
            "assistants). Random, at least 32 characters (`lifeos secret`). Unset = MCP off."
        ),
    )

    git_sync_interval: int = Field(
        default=0,
        ge=0,
        description=(
            "Seconds between automatic vault syncs with its git remote (commit, pull --rebase, "
            "push) run by `lifeos worker`. 0 disables the sync, e.g. on a laptop clone."
        ),
    )

    jobsearch_import_token: str | None = Field(
        default=None,
        min_length=32,
        description=(
            "Bearer token for /api/jobsearch/import/* (the job importer on the PC). "
            "Random, at least 32 characters (`lifeos secret`). Unset = import disabled."
        ),
    )
    jobsearch_evaluate_interval: int = Field(
        default=0,
        ge=0,
        description=(
            "Seconds between job evaluation runs of `lifeos worker` (pre-filter and rating of "
            "newly imported postings). 0 disables the job."
        ),
    )

    applications_project: str | None = Field(
        default=None,
        description=(
            "Projects v2 board for job applications as `owner/number` "
            "(`lifeos applications setup-board` creates it)."
        ),
    )
    applications_repo: str | None = Field(
        default=None,
        description="Private repository for application issues; default: first of github_repos.",
    )
    applications_interval: int = Field(
        default=0,
        ge=0,
        description="Seconds between board checks of `lifeos worker` for applications; 0 = off.",
    )

    agents_interval: int = Field(
        default=0,
        ge=0,
        description=(
            "Seconds between rounds of the AI teammates in `lifeos worker`: mirror the board "
            "contracts (`boards/` in the vault) to GitHub, then let agents (`agents/`) take "
            "tickets in their columns. 0 = off. Needs LIFEOS_GITHUB_TOKEN."
        ),
    )

    agents_eur_per_usd: float = Field(
        default=0.9,
        gt=0,
        description="Exchange rate for the cost shown in agent reports (API prices are in USD).",
    )

    claude_command: str = Field(
        default="claude",
        description=(
            "Command that starts Claude Code, for agents with `billing: subscription` (they "
            "run `claude -p` with CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`)."
        ),
    )

    app_repo: str = Field(
        default="",
        description=(
            "owner/name of this app's GitHub repository. With it, pull requests the assistant "
            "opens for a ticket are tested, merged into LIFEOS_DEPLOY_BRANCH and deployed by "
            "the host deployer (ops/deployer.sh) when the ticket goes to review."
        ),
    )

    deploy_branch: str = Field(
        default="main",
        description="The branch the server runs; the assistant's pull requests are merged into it.",
    )

    claude_timeout: int = Field(
        default=900,
        gt=0,
        description="Seconds one Claude Code call of an agent may take before it is stopped.",
    )

    @property
    def github_project_list(self) -> list[str]:
        refs = [r.strip() for r in self.github_projects.split(",") if r.strip()]
        if not refs and self.github_project:
            refs = [self.github_project]
        if self.applications_project:
            refs.append(self.applications_project)
        return list(dict.fromkeys(refs))

    @property
    def github_repo_list(self) -> list[str]:
        return [r.strip() for r in self.github_repos.split(",") if r.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
