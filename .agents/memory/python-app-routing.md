---
name: Python app routing & deployment in this Node artifact monorepo
description: How to serve/deploy a standalone Python (FastAPI/uvicorn) app in this Node "artifacts" monorepo, where only Node artifacts are natively deployable.
---

This repo is a Node/pnpm "artifacts" monorepo. Deployment uses `.replit` `[deployment] router = "application"`, which only runs/serves artifacts the platform recognizes as **runnable artifacts**. `createArtifact` (the only way to register one) supports Node types only (react-vite, expo, data-visualization, slides, video-js, mockup-sandbox) — NOT Python.

**Consequences (confirmed):**
- A hand-assembled Python app at repo root (e.g. `discoverability-check/`) is NOT a runnable artifact. Adding `[services.production]` to its `.replit-artifact/artifact.toml` does nothing — a publish still reports `artifact mode enabled runnable=1` (only the real artifacts run) and the live URL 404s at `/`.
- `.replit` cannot be edited directly (blocked), and in artifact mode its `deployment.run` is ignored anyway. There is no callback to set a non-artifact run command. So you cannot deploy the Python app as its own deployment.
- The in-editor preview proxy (`localhost:80`) also won't route to the root Python app for the same reason.

**Working solution — repurpose an existing deployable Node artifact to run the Python process.** The scaffold ships `artifacts/api-server` (a real, enumerated, deployable artifact). Point ITS `artifact.toml` services at the Python app instead of Node:
- `[[services]] paths = ["/"]`, keep its `localPort` (8080) and `id`. `kind` CANNOT be changed via `verifyAndReplaceArtifactToml` — leave it (`api`).
- `[services.production].build.args = ["sh","-c","pip install -r discoverability-check/requirements.txt"]`
- `[services.production].run.args = ["sh","-c","cd discoverability-check && exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8080"]`
- `[services.production.health.startup].path = "/"`
- Dev `[services.development].run`: same uvicorn, but dev CWD is the **artifact dir** (`artifacts/api-server`), so use `cd ../../discoverability-check`. Production CWD is the **repo root**, so use `cd discoverability-check`. (CWD differs between dev and prod — this bit me.)
- Free the root path on the Python app's own `artifact.toml` first (e.g. `paths=["/discoverability-check"]`) or `verifyAndReplaceArtifactToml` rejects the duplicate `/`.

**Why this works:** the deploy/preview just executes the artifact's `run` args against its `localPort` and routes by `paths`; it doesn't care whether the process is Node or Python. Verify in dev with `curl localhost:80/` (should be 200) BEFORE asking the user to republish — this is the only pre-publish check available.

**Gotcha:** numpy (and all Python deps) must be in BOTH `discoverability-check/requirements.txt` (used by the build) and root `pyproject.toml`/`uv.lock` (used by dev tooling), or the app crashes at runtime.

**Cleaner long-term alternative (not done here):** rebuild natively — logic into the shared Express `api-server`, UI as a react-vite artifact.
