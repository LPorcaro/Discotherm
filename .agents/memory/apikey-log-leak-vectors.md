---
name: API key log-leak vectors (discoverability-check)
description: How the FastAPI app's three upstream API keys can leak into logs, and the two mechanisms that must both stay in place to prevent it.
---

In `discoverability-check/`, all three upstream keys (Musixmatch, Songstats, JamBase)
are sent as an `apikey` **query param**, so ANY log line that emits the request URL
leaks a secret. Closing this required TWO independent mechanisms — both must stay:

1. `logging.getLogger("httpx").setLevel(logging.WARNING)` — httpx logs every request
   URL (with the apikey) at INFO. Without this, normal successful calls leak keys.
2. A centralized `@app.exception_handler(httpx.HTTPError)` that returns a sanitized 502
   and logs only `_safe_http_error(exc)` (status code or exception class) + `request.url.path`.

**Why the exception handler (not just try/except everywhere):** several upstream calls
use unguarded `raise_for_status()`. An unhandled `httpx.HTTPStatusError` carries the full
URL in its message; if it reaches Uvicorn it is logged as a traceback → key leak.

**Why register for the specific `httpx.HTTPError` type, not the catch-all `Exception`:**
Starlette's `ServerErrorMiddleware` (the 500/Exception handler) ALWAYS re-raises after
calling the handler, so Uvicorn still logs the traceback. A handler registered for a
**specific** exception type is routed through `ExceptionMiddleware` instead, which returns
the response WITHOUT logging a traceback. So a catch-all handler would NOT stop the leak;
the type-specific one does.

**Logging convention (keep as the only allowed pattern):** never log a raw httpx exception
or the JamBase `errors` array (its message echoes the key). Log status/type via
`_safe_http_error`, and for API-level failures log only the error `code`s.

**How to apply:** any new upstream call here inherits the protection automatically as long
as keys stay in query params and these two mechanisms remain. If you add a key via a header
instead, the URL no longer carries it — but keep both mechanisms anyway for the others.
Verified empirically by forcing an `HTTPStatusError` with a fake `apikey=...` URL → 502,
secret absent from all logs.
