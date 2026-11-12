# LifeOS app — working rules

- This repository is **public**. Never add personal data, real names, local paths,
  tokens or vault content — including in tests, fixtures, docs and commit messages.
  Anything specific comes from `deploy/.env` (`LIFEOS_*` variables).
- Code, comments, docs and UI text are in English.
- Backend: `cd backend && uv run pytest && uv run ruff check src tests && uv run ruff format src tests`.
- Frontend: `cd frontend && npm run build && npm test`. On Windows run vitest from a path
  with an upper-case drive letter (`C:\...`), otherwise it fails with "reading 'config'".
- Every request path goes through `lifeos.core.vault.Vault.resolve(..., zone=...)`;
  writes use `write_checked` (revision check) or `write_atomic`.
- Vault layout and workspace rules: `docs/vault.md`. Architecture: `docs/architecture.md`.
