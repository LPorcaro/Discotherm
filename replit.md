# Discotherm

Discotherm is a music discoverability diagnostic: enter an artist and it scores how discoverable they are across streaming/search surfaces and generates a report (with PDF export).

## Run & Operate

- The app is a Python FastAPI service in `discoverability-check/` (uvicorn).
- Dev: it runs on port 8000 via the `discoverability-check` workflow, and is ALSO served at `/` through the deployable `api-server` artifact on port 8080 (so the preview/proxy and production both work).
- `pnpm --filter @workspace/api-server run typecheck` — typecheck the (now unused) Node api-server package
- Required Python deps live in `discoverability-check/requirements.txt` and root `pyproject.toml`/`uv.lock` (keep them in sync).
- API keys (secrets): `JAMBASE_API_KEY`, `MUSIXMATCH_API_KEY`, `SONGSTATS_API_KEY` power the external lookups; `SESSION_SECRET` for sessions.

## Stack

- App: Python 3.11, FastAPI + uvicorn, httpx, numpy (scoring), reportlab (PDF export).
- Monorepo shell: pnpm workspaces, Node.js 24 (the Node `api-server`/`mockup-sandbox` artifacts are scaffold tooling; only `api-server` is repurposed to host the Python app in production).

## Where things live

- `discoverability-check/main.py` — FastAPI app (`app = FastAPI(title="Discotherm")`), routes via `APIRouter`, static mount. `BASE_PATH=""` so everything is served at root.
- `discoverability-check/scoring.py` — discoverability scoring (uses numpy).
- `discoverability-check/pdf_export.py` — PDF report generation (reportlab).
- `discoverability-check/static/` — frontend HTML/CSS/JS (served by StaticFiles).
- `artifacts/api-server/.replit-artifact/artifact.toml` — the deployable artifact; its production/dev run commands launch the Python uvicorn process (see Architecture decisions).

## Architecture decisions

- **The Python app is deployed by repurposing the Node `api-server` artifact.** This monorepo's deployment router only runs Node-type artifacts created via `createArtifact` (no Python type). So `api-server`'s `artifact.toml` is pointed at the Python app: build = `pip install -r discoverability-check/requirements.txt`, run = `uvicorn main:app` on port 8080, paths = `["/"]`. The platform just runs those args against the artifact's port — it doesn't care that the process is Python.
- Dev vs prod CWD differs: dev runs from the artifact dir (`artifacts/api-server`, so `cd ../../discoverability-check`); prod runs from the repo root (so `cd discoverability-check`).
- The standalone Python app cannot be its own deployable artifact, and `.replit` deployment config cannot be edited directly.

## Product

Enter an artist → Discotherm pulls data from JamBase, Musixmatch, and Songstats, computes a discoverability score, and renders a report viewable in-browser and exportable to PDF. Recent lookups are kept in a search history.

## User preferences

_Populate as you build._

## Gotchas

- Keep Python deps in sync across `discoverability-check/requirements.txt` AND root `pyproject.toml`/`uv.lock`, or production crashes (this is how numpy was originally missing in prod).
- After editing `discoverability-check/*.py`, restart the workflow(s) — uvicorn runs without `--reload`. Static HTML/CSS/JS is served fresh without a restart.
- The Python app at `/` and the `api-server` artifact share port 8080 in dev via the repurposed artifact; the `discoverability-check` workflow additionally runs a copy on 8000 for the canvas iframe.

## Pointers

- See `.agents/memory/python-app-routing.md` for the full deployment recipe and rationale.
- See the `pnpm-workspace` skill for workspace structure.
