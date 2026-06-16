import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

from scoring import compute_discoverability_score

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discoverability-check")
# httpx logs full request URLs at INFO level, which include apikey query params for every
# upstream call (Musixmatch/Songstats/JamBase) — suppress to WARNING so keys never hit logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_PATH = ""

app = FastAPI(title="Discoverability Check")
router = APIRouter()


@app.exception_handler(httpx.HTTPError)
async def _httpx_error_handler(request: Request, exc: httpx.HTTPError) -> JSONResponse:
    """Catch ALL upstream httpx failures so they never reach the default traceback logger.

    An httpx error's message embeds the request URL, which carries our apikey query params
    (Musixmatch/Songstats). Registering a handler for the specific httpx.HTTPError type routes
    it through Starlette's ExceptionMiddleware, which returns this response WITHOUT logging the
    traceback — so the only thing logged is the sanitized status/type from _safe_http_error.
    """
    logger.warning("Upstream request failed on %s: %s", request.url.path, _safe_http_error(exc))
    return JSONResponse(status_code=502, content={"detail": "Upstream data provider request failed"})


MUSIXMATCH_BASE = "https://api.musixmatch.com/ws/1.1"
JAMBASE_BASE = "https://www.jambase.com/jb-api/v1"
SONGSTATS_BASE = "https://api.songstats.com/enterprise/v1"
PAGES_TO_FETCH = 5   # 5 pages × 100 = up to 500 candidate tracks per artist_id
TOURING_WINDOW_DAYS = 365  # how far back JamBase events are counted for touring context
JAMBASE_PER_PAGE = 100     # JamBase events page size
PAGE_SIZE = 100
ARTIST_SEARCH_PAGE_SIZE = 30  # wide enough to surface fragmented duplicate artist records


class ArtistRequest(BaseModel):
    name: str


def mxm_body(resp_json: dict) -> dict:
    return resp_json.get("message", {}).get("body", {})


async def fetch_track_page(client: httpx.AsyncClient, artist_id: int, page: int, api_key: str) -> list:
    """Fetch one page of an artist's catalogue via track.search filtered by f_artist_id.

    f_artist_id is a precise filter (unlike the fuzzy q_artist text search), so no
    downstream exact-name filtering is needed.
    """
    resp = await client.get(
        f"{MUSIXMATCH_BASE}/track.search",
        params={
            "f_artist_id": artist_id,
            "s_track_rating": "desc",
            "page_size": PAGE_SIZE,
            "page": page,
            "apikey": api_key,
        },
    )
    resp.raise_for_status()
    return mxm_body(resp.json()).get("track_list", [])


async def _probe_artist_track_count(client: httpx.AsyncClient, artist_id: int, mxm_key: str) -> int:
    """Return Musixmatch's reported total track count for an f_artist_id filter (0 on error).

    Uses message.header.available (the total result count) via a single page_size=1 request,
    so we get the true catalogue size of each candidate without paginating. This lets the
    caller pick the highest-volume record among same-name duplicates rather than merely the
    first record that happens to have any track at all.
    """
    try:
        resp = await client.get(
            f"{MUSIXMATCH_BASE}/track.search",
            params={"f_artist_id": artist_id, "page_size": 1, "page": 1, "apikey": mxm_key},
        )
        resp.raise_for_status()
        j = resp.json()
        available = j.get("message", {}).get("header", {}).get("available")
        if isinstance(available, int):
            return available
        return len(mxm_body(j).get("track_list", []))
    except (httpx.HTTPError, ValueError):
        return 0


def _norm(name: str) -> str:
    return (name or "").strip().lower()


async def _candidate_ids_from_tracks(
    client: httpx.AsyncClient, query: str, mxm_key: str, pages: int = 2
) -> dict[int, str]:
    """Map artist_id -> artist_name for artists surfaced in track.search?q_artist results.

    artist.search frequently omits an artist's real high-volume catalogue record from its
    first page (e.g. Taylor Swift's 738-track record id=259675 is buried behind a dozen
    1-track duplicates). But that record's tracks dominate a rating-sorted track.search for
    the same name, so mining the top tracks reliably surfaces the canonical artist_id that
    artist.search misses. Returns {} on any error (caller still has artist.search results).
    """
    ids: dict[int, str] = {}
    try:
        resps = await asyncio.gather(
            *[
                client.get(
                    f"{MUSIXMATCH_BASE}/track.search",
                    params={"q_artist": query, "s_track_rating": "desc",
                            "page_size": PAGE_SIZE, "page": p, "apikey": mxm_key},
                )
                for p in range(1, pages + 1)
            ]
        )
    except httpx.HTTPError:
        return ids
    for resp in resps:
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            continue
        for t in mxm_body(resp.json()).get("track_list", []):
            tr = t.get("track", {})
            aid = tr.get("artist_id")
            if aid and aid not in ids:
                ids[aid] = tr.get("artist_name", "")
    return ids


async def _resolve_artist_records(
    client: httpx.AsyncClient, query: str, mxm_key: str
) -> tuple[dict | None, list[int]]:
    """Resolve a query to a canonical artist record plus every artist_id to aggregate.

    Musixmatch splits a single real artist across many duplicate records that share the
    exact same name: homonyms (two "i cani", one empty) AND fragmentation (Katy Perry's
    catalogue is spread over 25+ records, one with 238 tracks, most with 0-1). Worse, the
    real high-volume record is often missing entirely from artist.search's first page, so
    relying on artist.search alone yields a tiny ghost catalogue. So we:
      1. Build a candidate pool from TWO sources: artist.search (fragmented same-name
         duplicates) AND the artist_ids mined from a rating-sorted track.search?q_artist
         (which surfaces the real record artist.search omits).
      2. Probe EVERY candidate's true track count via f_artist_id (header.available).
      3. Pick the canonical as the HIGHEST-track-count record *whose name exactly matches
         the query* — the exact-name filter is essential because fuzzy track.search also
         surfaces unrelated high-volume artists (e.g. "Katy Perry" pulls in Pat Boone /
         Bob Marley with far larger catalogues). Fall back to the global max, then to the
         first candidate, when nothing matches the query name.
      4. Aggregate over every record whose name *exactly* matches the canonical name AND
         has tracks, so fragmented same-name records are merged while "feat./variant" acts
         (e.g. "Taylor Swift feat. Bon Iver") stay excluded.

    Returns (canonical_artist, artist_ids_to_fetch). Returns (None, []) when nothing found.
    """
    artist_resp, track_ids = await asyncio.gather(
        client.get(
            f"{MUSIXMATCH_BASE}/artist.search",
            params={"q_artist": query, "page_size": ARTIST_SEARCH_PAGE_SIZE, "page": 1, "apikey": mxm_key},
        ),
        _candidate_ids_from_tracks(client, query, mxm_key),
    )
    artist_resp.raise_for_status()
    artist_list = mxm_body(artist_resp.json()).get("artist_list", [])
    candidates = [a["artist"] for a in artist_list if a.get("artist")]

    # Merge in track-search-surfaced ids not already present, as minimal {id, name} records.
    seen = {c["artist_id"] for c in candidates}
    for aid, name in track_ids.items():
        if aid not in seen:
            candidates.append({"artist_id": aid, "artist_name": name})
            seen.add(aid)

    if not candidates:
        return None, []

    counts = await asyncio.gather(
        *[_probe_artist_track_count(client, c["artist_id"], mxm_key) for c in candidates]
    )

    q = _norm(query)
    # Prefer the highest-volume record whose name exactly matches the query (this is the real
    # artist); the exact-name gate keeps fuzzy track.search homonyms from hijacking selection.
    name_matched = [i for i in range(len(candidates)) if _norm(candidates[i]["artist_name"]) == q]
    pool = name_matched if any(counts[i] > 0 for i in name_matched) else range(len(candidates))
    best_idx = max(pool, key=lambda i: counts[i])
    canonical = candidates[best_idx] if counts[best_idx] > 0 else candidates[0]
    canon_name = _norm(canonical["artist_name"])

    artist_ids = [
        c["artist_id"]
        for c, n in zip(candidates, counts)
        if n > 0 and _norm(c["artist_name"]) == canon_name
    ]
    if not artist_ids:
        artist_ids = [canonical["artist_id"]]
    return canonical, artist_ids


async def _resolve_tracks(client: httpx.AsyncClient, artist_ids: list[int], mxm_key: str) -> list[dict]:
    """Fetch and dedupe tracks across one or more artist_id records via f_artist_id.

    All (artist_id, page) fetches run concurrently; duplicate track_ids that appear under
    multiple fragmented records are collapsed.
    """
    tasks = [
        fetch_track_page(client, aid, p, mxm_key)
        for aid in artist_ids
        for p in range(1, PAGES_TO_FETCH + 1)
    ]
    pages = await asyncio.gather(*tasks, return_exceptions=True)
    seen_ids: set[int] = set()
    out: list[dict] = []
    for page_result in pages:
        if isinstance(page_result, Exception):
            continue
        for item in page_result:
            track = item.get("track", {})
            tid = track.get("track_id")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            out.append(track)
    return out


def _safe_http_error(exc: Exception) -> str:
    """Describe an httpx/JSON error WITHOUT exposing the request URL (it carries the apikey)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _event_dates(events: list[dict]) -> list[str]:
    """Extract YYYY-MM-DD strings from a JamBase events list, skipping malformed entries."""
    return [
        ev["startDate"][:10]
        for ev in events
        if isinstance(ev.get("startDate"), str) and len(ev["startDate"]) >= 10
    ]


async def _resolve_touring_context(
    client: httpx.AsyncClient, artist_name: str, jb_key: str | None
) -> dict | None:
    """Annotate touring activity from JamBase events (NOT a scored diagnostic).

    Counts the artist's events over the trailing TOURING_WINDOW_DAYS window and finds the
    most recent show date. Returns None on ANY failure (missing key, HTTP error, bad JSON,
    artist not on JamBase) so the report degrades gracefully and the badge is simply omitted.
    """
    if not jb_key:
        return None

    today = datetime.now(timezone.utc).date()
    date_from = today - timedelta(days=TOURING_WINDOW_DAYS)
    base_params = {
        "artistName": artist_name,
        "eventDateFrom": date_from.isoformat(),
        "eventDateTo": today.isoformat(),
        "perPage": JAMBASE_PER_PAGE,
        "apikey": jb_key,
    }
    try:
        resp = await client.get(f"{JAMBASE_BASE}/events", params={**base_params, "page": 1})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("JamBase touring lookup failed for %r: %s", artist_name, _safe_http_error(exc))
        return None

    if not data.get("success"):
        codes = [e.get("code") for e in (data.get("errors") or []) if isinstance(e, dict)]
        logger.warning("JamBase touring lookup unsuccessful for %r: %s", artist_name, codes)
        return None

    events = data.get("events") or []
    pagination = data.get("pagination") or {}
    show_count = pagination.get("totalItems")
    if not isinstance(show_count, int):
        show_count = len(events)

    dates = _event_dates(events)

    # JamBase paginates events in date order, so the most recent show in a past window can
    # sit on the LAST page. When the window spans multiple pages, fetch the last page too
    # (bounded: one extra request) and fold its dates in so most_recent stays accurate
    # regardless of ascending/descending ordering.
    total_pages = pagination.get("totalPages")
    if not isinstance(total_pages, int):
        total_pages = (show_count + JAMBASE_PER_PAGE - 1) // JAMBASE_PER_PAGE if show_count else 1
    if total_pages > 1:
        try:
            last = await client.get(f"{JAMBASE_BASE}/events", params={**base_params, "page": total_pages})
            last.raise_for_status()
            dates += _event_dates(last.json().get("events") or [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("JamBase last-page fetch failed for %r: %s", artist_name, _safe_http_error(exc))

    most_recent = max(dates) if dates else None

    return {
        "show_count": show_count,
        "most_recent_show_date": most_recent,
        "is_active_touring_artist": show_count > 0,
    }


async def _fetch_artist_image(client: httpx.AsyncClient, tracks: list[dict]) -> str | None:
    """Use the highest-rated track's Spotify id to fetch album art via Spotify oEmbed (no auth)."""
    candidates = sorted(
        (t for t in tracks if t.get("track_spotify_id")),
        key=lambda t: t.get("track_rating", 0) or 0,
        reverse=True,
    )
    if not candidates:
        return None
    spotify_id = candidates[0]["track_spotify_id"]
    try:
        resp = await client.get(
            "https://open.spotify.com/oembed",
            params={"url": f"https://open.spotify.com/track/{spotify_id}"},
        )
        resp.raise_for_status()
        return resp.json().get("thumbnail_url") or None
    except (httpx.HTTPError, ValueError):
        return None


async def _fetch_track_mood(client: httpx.AsyncClient, track_id: int, mxm_key: str) -> list[str] | None:
    """Return the ordered ``main_moods`` list for a track via Musixmatch lyric analysis, or None.

    The list is ordered by prominence (most prominent first). None means the analysis
    failed or no moods were available (restricted track), so the caller should treat
    the track as skipped.
    """
    try:
        resp = await client.get(
            f"{MUSIXMATCH_BASE}/track.lyrics.analysis.get",
            params={"track_id": track_id, "apikey": mxm_key},
        )
        resp.raise_for_status()
        analysis = (mxm_body(resp.json()).get("analysis") or {})
        main_moods = ((analysis.get("moods") or {}).get("main_moods")) or []
        return main_moods if main_moods else None
    except (httpx.HTTPError, ValueError):
        return None


async def _resolve_mood_data(client: httpx.AsyncClient, tracks: list[dict], mxm_key: str) -> dict:
    """Analyse the top 15 tracks by rating concurrently and summarise mood coherence inputs."""
    top = sorted(tracks, key=lambda t: t.get("track_rating", 0) or 0, reverse=True)[:15]
    top = [t for t in top if t.get("track_id")]
    if not top:
        return {"track_moods": [], "skipped": 0}

    results = await asyncio.gather(
        *[_fetch_track_mood(client, t["track_id"], mxm_key) for t in top]
    )
    track_moods = [m for m in results if m]
    skipped = len(results) - len(track_moods)
    return {"track_moods": track_moods, "skipped": skipped}


async def _resolve_songstats(client: httpx.AsyncClient, resolved_name: str, ss_key: str) -> dict:
    """Search Songstats for the artist and return raw_stats keyed by source."""
    headers = {"apikey": ss_key, "Accept": "application/json"}
    ss_search = await client.get(
        f"{SONGSTATS_BASE}/artists/search",
        params={"q": resolved_name},
        headers=headers,
    )
    ss_search.raise_for_status()
    results = ss_search.json().get("results", [])
    if not results:
        return {}
    songstats_artist_id = results[0]["songstats_artist_id"]
    ss_stats = await client.get(
        f"{SONGSTATS_BASE}/artists/stats",
        params={"songstats_artist_id": songstats_artist_id},
        headers=headers,
    )
    ss_stats.raise_for_status()
    raw: dict = {}
    for entry in ss_stats.json().get("stats", []):
        raw[entry["source"]] = entry["data"]
    return raw


@router.post("/artist")
async def get_artist(request: ArtistRequest):
    api_key = os.getenv("MUSIXMATCH_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="MUSIXMATCH_API_KEY not configured")

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: resolve the query to a canonical artist whose f_artist_id has tracks.
        artist, artist_ids = await _resolve_artist_records(client, request.name, api_key)
        if artist is None:
            raise HTTPException(status_code=404, detail=f"Artist '{request.name}' not found on Musixmatch")
        artist_id = artist["artist_id"]

        # Step 2: aggregate the catalogue precisely via track.search?f_artist_id across every
        # exact-name record (no fuzzy name filter needed).
        # NOTE: artist.albums.get and album.tracks.get require a paid Musixmatch plan
        # and return empty on the free tier. track.search is the deepest available endpoint.
        matched_tracks = await _resolve_tracks(client, artist_ids, api_key)

    return {
        "artist": artist,
        "artist_id": artist_id,
        "tracks": matched_tracks,
        "total_tracks_found": len(matched_tracks),
        "note": (
            "artist.albums.get and album.tracks.get require a paid Musixmatch plan. "
            f"Tracks aggregated from {PAGES_TO_FETCH} pages of track.search per exact-name "
            "artist record (f_artist_id), deduplicated."
        ),
    }


SONGSTATS_BASE = "https://api.songstats.com/enterprise/v1"


@router.post("/artist/stats")
async def get_artist_stats(request: ArtistRequest):
    mxm_key = os.getenv("MUSIXMATCH_API_KEY")
    ss_key = os.getenv("SONGSTATS_API_KEY")
    if not mxm_key:
        raise HTTPException(status_code=500, detail="MUSIXMATCH_API_KEY not configured")
    if not ss_key:
        raise HTTPException(status_code=500, detail="SONGSTATS_API_KEY not configured")

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        # Step 1: resolve artist via Musixmatch
        mxm_resp = await client.get(
            f"{MUSIXMATCH_BASE}/artist.search",
            params={"q_artist": request.name, "page_size": 1, "page": 1, "apikey": mxm_key},
        )
        mxm_resp.raise_for_status()
        artist_list = mxm_body(mxm_resp.json()).get("artist_list", [])
        if not artist_list:
            raise HTTPException(status_code=404, detail=f"Artist '{request.name}' not found on Musixmatch")

        artist = artist_list[0]["artist"]
        artist_id = artist["artist_id"]
        resolved_name = artist["artist_name"]

        # Step 2: search Songstats for the resolved artist name
        ss_headers = {"apikey": ss_key, "Accept": "application/json"}
        ss_search = await client.get(
            f"{SONGSTATS_BASE}/artists/search",
            params={"q": resolved_name},
            headers=ss_headers,
        )
        ss_search.raise_for_status()
        ss_results = ss_search.json().get("results", [])
        if not ss_results:
            raise HTTPException(status_code=404, detail=f"Artist '{resolved_name}' not found on Songstats")

        songstats_artist = ss_results[0]
        songstats_artist_id = songstats_artist["songstats_artist_id"]

        # Step 3: fetch streaming stats
        ss_stats = await client.get(
            f"{SONGSTATS_BASE}/artists/stats",
            params={"songstats_artist_id": songstats_artist_id},
            headers=ss_headers,
        )
        ss_stats.raise_for_status()

    stats_by_source: dict = {}
    for entry in ss_stats.json().get("stats", []):
        stats_by_source[entry["source"]] = entry["data"]

    spotify = stats_by_source.get("spotify", {})

    return {
        "musixmatch_artist_id": artist_id,
        "songstats_artist_id": songstats_artist_id,
        "artist_name": resolved_name,
        "songstats_profile_url": songstats_artist.get("site_url"),
        "spotify": {
            "streams_total": spotify.get("streams_total"),
            "monthly_listeners_current": spotify.get("monthly_listeners_current"),
            "followers_total": spotify.get("followers_total"),
            "popularity_current": spotify.get("popularity_current"),
            "playlists_current": spotify.get("playlists_current"),
            "playlists_total": spotify.get("playlists_total"),
            "playlists_editorial_current": spotify.get("playlists_editorial_current"),
            "playlist_reach_current": spotify.get("playlist_reach_current"),
        },
        "raw_stats": stats_by_source,
    }


@router.post("/artist/report")
async def get_artist_report(request: ArtistRequest):
    mxm_key = os.getenv("MUSIXMATCH_API_KEY")
    ss_key = os.getenv("SONGSTATS_API_KEY")
    if not mxm_key:
        raise HTTPException(status_code=500, detail="MUSIXMATCH_API_KEY not configured")
    if not ss_key:
        raise HTTPException(status_code=500, detail="SONGSTATS_API_KEY not configured")
    jb_key = os.getenv("JAMBASE_API_KEY")  # optional — touring badge degrades gracefully without it

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        # Step 1: resolve to a canonical artist whose f_artist_id has tracks (must happen
        # first — both the track fetch and the Songstats lookup need it).
        artist, artist_ids = await _resolve_artist_records(client, request.name, mxm_key)
        if artist is None:
            raise HTTPException(status_code=404, detail=f"Artist '{request.name}' not found on Musixmatch")
        artist_id = artist["artist_id"]
        resolved_name = artist["artist_name"]

        # Step 2: track fetch (aggregated by f_artist_id) + Songstats stats + JamBase touring
        # context concurrently (touring context is a non-scored annotation, key optional).
        tracks, raw_stats, touring_context = await asyncio.gather(
            _resolve_tracks(client, artist_ids, mxm_key),
            _resolve_songstats(client, resolved_name, ss_key),
            _resolve_touring_context(client, resolved_name, jb_key),
        )

        # Step 3: artist thumbnail + mood analysis (both depend on resolved tracks)
        artist_image_url, mood_data = await asyncio.gather(
            _fetch_artist_image(client, tracks),
            _resolve_mood_data(client, tracks, mxm_key),
        )

    # Step 4: score
    scored = compute_discoverability_score(artist, tracks, raw_stats, mood_data, logger=logger)

    return {
        "artist_name": resolved_name,
        "artist_id": artist_id,
        "artist_image_url": artist_image_url,
        "total_tracks": len(tracks),
        "overall_score": scored["overall_score"],
        "overall_status": scored["overall_status"],
        "diagnostics": scored["diagnostics"],
        "touring_context": touring_context,
        "note": "stream_concentration uses num_favourite as proxy — not actual stream counts",
    }


app.include_router(router, prefix=BASE_PATH)
app.mount(BASE_PATH or "/", StaticFiles(directory="static", html=True), name="static")
