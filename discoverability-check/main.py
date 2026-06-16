import os
import asyncio
import logging
from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

from scoring import compute_discoverability_score

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discoverability-check")

BASE_PATH = ""

app = FastAPI(title="Discoverability Check")
router = APIRouter()

MUSIXMATCH_BASE = "https://api.musixmatch.com/ws/1.1"
SONGSTATS_BASE = "https://api.songstats.com/enterprise/v1"
PAGES_TO_FETCH = 5   # 5 pages × 100 = up to 500 candidate tracks
PAGE_SIZE = 100


class ArtistRequest(BaseModel):
    name: str


def mxm_body(resp_json: dict) -> dict:
    return resp_json.get("message", {}).get("body", {})


async def fetch_track_page(client: httpx.AsyncClient, q_artist: str, page: int, api_key: str) -> list:
    resp = await client.get(
        f"{MUSIXMATCH_BASE}/track.search",
        params={
            "q_artist": q_artist,
            "s_track_rating": "desc",
            "page_size": PAGE_SIZE,
            "page": page,
            "apikey": api_key,
        },
    )
    resp.raise_for_status()
    return mxm_body(resp.json()).get("track_list", [])


async def _resolve_tracks(client: httpx.AsyncClient, artist_name: str, exact_name: str, mxm_key: str) -> list[dict]:
    """Fetch all pages concurrently and return deduplicated track dicts for this artist."""
    pages = await asyncio.gather(
        *[fetch_track_page(client, artist_name, p, mxm_key) for p in range(1, PAGES_TO_FETCH + 1)],
        return_exceptions=True,
    )
    seen_ids: set[int] = set()
    matched: list[dict] = []
    for page_result in pages:
        if isinstance(page_result, Exception):
            continue
        for item in page_result:
            track = item.get("track", {})
            tid = track.get("track_id")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            if track.get("artist_name", "").lower() == exact_name:
                matched.append(track)
    return matched


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
        # Step 1: resolve artist → get exact name + artist_id
        search_resp = await client.get(
            f"{MUSIXMATCH_BASE}/artist.search",
            params={
                "q_artist": request.name,
                "page_size": 1,
                "page": 1,
                "apikey": api_key,
            },
        )
        search_resp.raise_for_status()
        artist_list = mxm_body(search_resp.json()).get("artist_list", [])
        if not artist_list:
            raise HTTPException(status_code=404, detail=f"Artist '{request.name}' not found on Musixmatch")

        artist = artist_list[0]["artist"]
        artist_id = artist["artist_id"]
        exact_name = artist["artist_name"].lower()

        # Step 2: fetch multiple pages of track.search concurrently
        # NOTE: artist.albums.get and album.tracks.get require a paid Musixmatch plan
        # and return empty on the free tier. track.search is the deepest available endpoint.
        pages = await asyncio.gather(
            *[fetch_track_page(client, artist["artist_name"], p, api_key) for p in range(1, PAGES_TO_FETCH + 1)],
            return_exceptions=True,
        )

    # Step 3: flatten, deduplicate, and filter to this artist only
    seen_ids: set[int] = set()
    matched_tracks = []
    for page_result in pages:
        if isinstance(page_result, Exception):
            continue
        for item in page_result:
            track = item.get("track", {})
            tid = track.get("track_id")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            if track.get("artist_name", "").lower() == exact_name:
                matched_tracks.append(track)

    return {
        "artist": artist,
        "artist_id": artist_id,
        "tracks": matched_tracks,
        "total_tracks_found": len(matched_tracks),
        "note": (
            "artist.albums.get and album.tracks.get require a paid Musixmatch plan. "
            f"Tracks sourced from {PAGES_TO_FETCH} pages of track.search, "
            "filtered to exact artist name match."
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

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        # Step 1: resolve artist via Musixmatch (must happen first — both branches need artist_name)
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
        exact_name = resolved_name.lower()

        # Step 2: track fetch + Songstats stats concurrently
        tracks, raw_stats = await asyncio.gather(
            _resolve_tracks(client, resolved_name, exact_name, mxm_key),
            _resolve_songstats(client, resolved_name, ss_key),
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
        "note": "stream_concentration uses num_favourite as proxy — not actual stream counts",
    }


app.include_router(router, prefix=BASE_PATH)
app.mount(BASE_PATH or "/", StaticFiles(directory="static", html=True), name="static")
