import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Discoverability Check")

MUSIXMATCH_BASE = "https://api.musixmatch.com/ws/1.1"
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


@app.post("/artist")
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
            *[fetch_track_page(client, request.name, p, api_key) for p in range(1, PAGES_TO_FETCH + 1)],
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


app.mount("/", StaticFiles(directory="static", html=True), name="static")
