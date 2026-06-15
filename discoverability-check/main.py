import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Discoverability Check")

MUSIXMATCH_BASE = "https://api.musixmatch.com/ws/1.1"


class ArtistRequest(BaseModel):
    name: str


@app.post("/artist")
async def get_artist(request: ArtistRequest):
    api_key = os.getenv("MUSIXMATCH_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="MUSIXMATCH_API_KEY not configured")

    async with httpx.AsyncClient(timeout=10.0) as client:
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
        search_data = search_resp.json()

        artist_list = (
            search_data.get("message", {})
            .get("body", {})
            .get("artist_list", [])
        )
        if not artist_list:
            raise HTTPException(status_code=404, detail=f"Artist '{request.name}' not found on Musixmatch")

        artist = artist_list[0]["artist"]
        artist_id = artist["artist_id"]

        tracks_resp = await client.get(
            f"{MUSIXMATCH_BASE}/track.search",
            params={
                "q_artist": request.name,
                "s_track_rating": "desc",
                "page_size": 10,
                "page": 1,
                "apikey": api_key,
            },
        )
        tracks_resp.raise_for_status()
        tracks_data = tracks_resp.json()

    return {
        "artist": artist,
        "top_tracks": tracks_data.get("message", {}).get("body", {}).get("track_list", []),
    }


app.mount("/", StaticFiles(directory="static", html=True), name="static")
