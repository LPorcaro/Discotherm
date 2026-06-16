---
name: Python app routing in this Node artifact monorepo
description: Why a standalone Python/FastAPI app can't use the preview pane/canvas, and how to reach it publicly.
---

This repo is a Node/pnpm "artifacts" monorepo. The platform reverse proxy (`REPLIT_ARTIFACT_ROUTER`, listens on the externalPort-80 path-based ingress) only routes to artifacts in its backend registry, which is populated by the native `createArtifact()` flow. `createArtifact` supports Node-based types only (react-vite, expo, data-visualization, slides, video-js, mockup-sandbox) — NOT Python.

**Consequence:** a hand-assembled Python/FastAPI app (e.g. `discoverability-check/` at repo root) is invisible to the router no matter what you do. Editing its `.replit-artifact/artifact.toml` via `verifyAndReplaceArtifactToml`, restarting its workflow, and calling `presentArtifact` all FAILED to add a route — router still returned 404 for the app's path on `localhost:80` and the bare dev domain. `listArtifacts()` showing it as registered is NOT enough; the router uses a separate registry.

**How to reach such an app publicly:** use the dev domain with the app's explicit external port, e.g. `https://${REPLIT_DEV_DOMAIN}:8000/`. The `8000-<id>...` subdomain prefix form returns a placeholder ("Run this app to see the results here"), and the bare dev domain goes through the router (404) — only the explicit `:PORT` suffix works. Such apps won't show in the preview pane/canvas and won't publish the normal way.

**Why:** confirmed by inspecting the router binary (uses NATS + `types.Artifact` registry) and that both working routes (`/api`, `/__mockup`) are `createArtifact`-made artifacts under `artifacts/`.

**How to apply:** if a user wants a Python (or other non-Node) app previewable/publishable here, the durable fix is to rebuild it natively (logic into the shared Express `api-server`, UI as a react-vite artifact). Otherwise hand them the explicit `:PORT` dev-domain link.
