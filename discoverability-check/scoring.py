"""
Pure-logic discoverability scoring. No API calls.

Usage:
    from scoring import compute_discoverability_score
    result = compute_discoverability_score(artist_data, tracks, stats)
"""

from __future__ import annotations


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _status(score: float) -> str:
    if score >= 60:
        return "good"
    if score >= 30:
        return "warning"
    return "critical"


# ---------------------------------------------------------------------------
# Diagnostic 1 — CATALOGUE_DEPTH
# Measures metadata completeness: what fraction of tracks have both
# has_lyrics=1 and has_richsync=1 (synced lyrics).  A catalogue that is
# invisible to lyric-based search / karaoke / sync placements scores low.
# ---------------------------------------------------------------------------
def _catalogue_depth(tracks: list[dict]) -> dict:
    total = len(tracks)
    if total == 0:
        return {
            "name": "CATALOGUE_DEPTH",
            "score": 0,
            "status": "critical",
            "recommendation": (
                "No tracks found for this artist. Ensure the artist name is correct "
                "and that the catalogue has been delivered to streaming DSPs."
            ),
        }

    with_both = sum(
        1
        for t in tracks
        if t.get("has_lyrics") == 1 and t.get("has_richsync") == 1
    )
    ratio = with_both / total  # 0–1
    score = round(_clamp(ratio * 100))

    if score >= 60:
        rec = (
            f"{with_both}/{total} tracks have full metadata (lyrics + synced lyrics). "
            "Maintain this by submitting richsync data for every new release."
        )
    elif score >= 30:
        rec = (
            f"Only {with_both}/{total} tracks carry both lyrics and synced lyrics. "
            "Submit missing richsync files via your distributor or directly through "
            "the Musixmatch for Artists portal to improve lyric-search visibility."
        )
    else:
        rec = (
            f"Only {with_both}/{total} tracks have full lyric metadata — the catalogue "
            "is largely invisible to lyric-driven discovery surfaces. Prioritise "
            "uploading lyrics and richsync for the top 20 tracks first, then backfill "
            "the rest via your distributor's metadata delivery pipeline."
        )

    return {
        "name": "CATALOGUE_DEPTH",
        "score": score,
        "status": _status(score),
        "detail": {"tracks_total": total, "tracks_with_full_metadata": with_both},
        "recommendation": rec,
    }


# ---------------------------------------------------------------------------
# Diagnostic 2 — STREAM_CONCENTRATION
# Proxy for catalogue-tail visibility: what share of all track favourites
# (a rough proxy for streams / saves) is held by the top 3 tracks.
# High concentration means the long tail is invisible — listeners never
# discover the deeper catalogue.
# Score is INVERTED: lower concentration = higher score.
# ---------------------------------------------------------------------------
def _stream_concentration(tracks: list[dict]) -> dict:
    if not tracks:
        return {
            "name": "STREAM_CONCENTRATION",
            "score": 0,
            "status": "critical",
            "recommendation": "No track data available to evaluate stream concentration.",
        }

    favs = [t.get("num_favourite", 0) or 0 for t in tracks]
    total_favs = sum(favs)

    if total_favs == 0:
        # No favourite data — score neutral rather than punishing
        return {
            "name": "STREAM_CONCENTRATION",
            "score": 50,
            "status": "warning",
            "detail": {"total_favourites": 0, "top3_favourites": 0, "concentration_pct": None},
            "recommendation": (
                "Favourite counts are unavailable for this catalogue. "
                "Pitch tracks to playlist curators across the full catalogue, "
                "not just the top releases, to build tail visibility."
            ),
        }

    sorted_favs = sorted(favs, reverse=True)
    top3_favs = sum(sorted_favs[:3])
    concentration_pct = (top3_favs / total_favs) * 100
    # Invert: 0 % concentration → 100 score; 100 % → 0 score
    score = round(_clamp(100 - concentration_pct))

    if score >= 60:
        rec = (
            f"The top 3 tracks account for {concentration_pct:.1f}% of all saves — "
            "listeners are discovering the wider catalogue. Keep releasing varied content "
            "and pitching deep cuts to editorial playlists."
        )
    elif score >= 30:
        rec = (
            f"The top 3 tracks account for {concentration_pct:.1f}% of all saves. "
            "The catalogue tail has some traction but is under-exposed. "
            "Consider targeting catalogue tracks in playlist pitches, YouTube premieres, "
            "or TikTok campaigns to surface lesser-known material."
        )
    else:
        rec = (
            f"The top 3 tracks account for {concentration_pct:.1f}% of all saves — "
            "the catalogue tail is virtually invisible. Run targeted campaigns on "
            "non-single tracks: pitch them to editorial, include them in social content, "
            "and use pre-save pages for back-catalogue releases to build momentum outside "
            "the flagship hits."
        )

    return {
        "name": "STREAM_CONCENTRATION",
        "score": score,
        "status": _status(score),
        "detail": {
            "tracks_total": len(tracks),
            "total_favourites": total_favs,
            "top3_favourites": top3_favs,
            "concentration_pct": round(concentration_pct, 2),
        },
        "recommendation": rec,
    }


# ---------------------------------------------------------------------------
# Diagnostic 3 — PLAYLIST_REACH
# Measures algorithmic vs organic reach: ratio of editorial (human-curated,
# algorithmically boosted) to total Spotify playlist placements.
# A healthy ratio signals that the artist has broken into Spotify editorial,
# which multiplies organic discovery. Scale: 10 % editorial ratio → score 100.
# ---------------------------------------------------------------------------
def _playlist_reach(stats: dict) -> dict:
    spotify = stats.get("spotify", {})
    editorial = spotify.get("playlists_editorial_current") or 0
    total = spotify.get("playlists_current") or 0

    if total == 0:
        return {
            "name": "PLAYLIST_REACH",
            "score": 0,
            "status": "critical",
            "detail": {"playlists_total": 0, "playlists_editorial": 0, "editorial_pct": None},
            "recommendation": (
                "No Spotify playlist data found. Ensure the artist profile is verified "
                "on Spotify for Artists and begin pitching new releases to Spotify editorial "
                "at least 7 days before release via the Spotify for Artists submission tool."
            ),
        }

    editorial_pct = (editorial / total) * 100
    # 10 % editorial ratio → score 100 (generous scale reflecting how rare high ratios are)
    score = round(_clamp(editorial_pct * 10))

    if score >= 60:
        rec = (
            f"{editorial} of {total} current playlists are editorial ({editorial_pct:.2f}%). "
            "Strong editorial presence — sustain it by submitting every new release to "
            "Spotify editorial at least 7 days before release and maintaining release "
            "cadence to stay in the algorithm."
        )
    elif score >= 30:
        rec = (
            f"{editorial} of {total} current playlists are editorial ({editorial_pct:.2f}%). "
            "There is some editorial coverage, but most placements are organic. "
            "Increase pitching frequency: submit every track (not just singles) to "
            "Spotify for Artists editorial, and work with your label or distributor "
            "on personalised playlist outreach."
        )
    else:
        rec = (
            f"Only {editorial} of {total} current playlists are editorial ({editorial_pct:.2f}%). "
            "The artist is almost entirely reliant on organic playlist adds. "
            "Prioritise Spotify for Artists editorial pitches for every upcoming release, "
            "engage a playlist plugger, and investigate whether metadata issues "
            "(genre tags, mood tags, release date accuracy) are reducing editorial "
            "algorithmic eligibility."
        )

    return {
        "name": "PLAYLIST_REACH",
        "score": score,
        "status": _status(score),
        "detail": {
            "playlists_total": total,
            "playlists_editorial": editorial,
            "editorial_pct": round(editorial_pct, 4),
        },
        "recommendation": rec,
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------
def compute_discoverability_score(
    artist_data: dict,
    tracks: list[dict],
    stats: dict,
) -> dict:
    """
    Parameters
    ----------
    artist_data : dict
        Musixmatch artist object (from artist.search → artist_list[0]["artist"]).
    tracks : list[dict]
        List of Musixmatch track objects (the raw dicts, not wrapped in {"track": ...}).
    stats : dict
        Songstats stats dict keyed by source, e.g. {"spotify": {...}, "apple_music": {...}}.
        This matches the ``raw_stats`` field returned by POST /artist/stats.

    Returns
    -------
    dict with keys:
        overall_score  — int 0–100
        diagnostics    — list of diagnostic dicts
    """
    d_catalogue = _catalogue_depth(tracks)
    d_concentration = _stream_concentration(tracks)
    d_reach = _playlist_reach(stats)

    diagnostics = [d_catalogue, d_concentration, d_reach]
    overall = round(sum(d["score"] for d in diagnostics) / len(diagnostics))

    return {
        "artist_name": artist_data.get("artist_name", "Unknown"),
        "overall_score": overall,
        "overall_status": _status(overall),
        "diagnostics": diagnostics,
    }
