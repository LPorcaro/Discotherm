"""
Pure-logic discoverability scoring. No API calls.

Usage:
    from scoring import compute_discoverability_score
    result = compute_discoverability_score(artist_data, tracks, stats)
"""

from __future__ import annotations

import math


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

    if total_favs < 20:
        # Sample too small to compute a meaningful concentration ratio.
        sorted_favs = sorted(favs, reverse=True)
        top3_favs = sum(sorted_favs[:3])
        return {
            "name": "STREAM_CONCENTRATION",
            "score": 50,
            "status": "insufficient_data",
            "detail": {
                "tracks_total": len(tracks),
                "total_favourites": total_favs,
                "top3_favourites": top3_favs,
                "concentration_pct": None,
            },
            "recommendation": (
                f"Only {total_favs} total favourites across the catalogue — the sample size "
                "is too small to reliably assess stream concentration. This metric becomes "
                "meaningful once the catalogue has accumulated more listener saves; revisit "
                "it then. In the meantime, focus on driving saves through playlist pitching "
                "and social campaigns across the full catalogue."
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
    # Blend two signals so a massive catalogue with large absolute editorial reach
    # is not penalised the same as a tiny one just because the *ratio* is low:
    #   - relative component: 10 % editorial ratio → 100 (rare, so generous scale)
    #   - absolute component: log-scaled count, 1000 editorial playlists → 100
    pct_component = min(editorial_pct * 10, 100)
    abs_component = min(math.log10(editorial + 1) / math.log10(1000) * 100, 100)
    score = round(_clamp(0.5 * pct_component + 0.5 * abs_component))

    if score >= 60:
        rec = (
            f"{editorial} of {total} current playlists are editorial ({editorial_pct:.2f}%), "
            f"a strong editorial footprint in both absolute ({editorial} placements) and "
            "relative terms. Sustain it by submitting every new release to Spotify editorial "
            "at least 7 days before release and maintaining release cadence to stay in the "
            "algorithm."
        )
    elif score >= 30:
        rec = (
            f"{editorial} of {total} current playlists are editorial ({editorial_pct:.2f}%). "
            f"With {editorial} editorial placements there is meaningful absolute reach, but "
            "most of the catalogue's playlist presence is organic. Increase pitching "
            "frequency: submit every track (not just singles) to Spotify for Artists "
            "editorial, and work with your label or distributor on personalised playlist "
            "outreach."
        )
    elif editorial >= 50:
        # Critical-but-large: genuinely large absolute editorial reach, low ratio.
        rec = (
            f"{editorial} of {total} current playlists are editorial ({editorial_pct:.2f}%). "
            f"Despite strong absolute editorial reach ({editorial} placements), organic "
            "placements dominate the catalogue, so the editorial share is low. Protect the "
            "editorial relationships you have and keep pitching new releases early, while "
            "auditing whether the large organic footprint reflects healthy fan-driven "
            "playlisting or low-quality adds that dilute the ratio."
        )
    else:
        # Critical-and-small: weak in both absolute and relative terms.
        rec = (
            f"Only {editorial} of {total} current playlists are editorial ({editorial_pct:.2f}%) "
            f"— minimal editorial reach in both absolute ({editorial} placements) and relative "
            "terms. The artist is almost entirely reliant on organic playlist adds. Prioritise "
            "Spotify for Artists editorial pitches for every upcoming release, engage a "
            "playlist plugger, and investigate whether metadata issues (genre tags, mood tags, "
            "release date accuracy) are reducing editorial algorithmic eligibility."
        )

    return {
        "name": "PLAYLIST_REACH",
        "score": score,
        "status": _status(score),
        "detail": {
            "playlists_total": total,
            "playlists_editorial": editorial,
            "editorial_pct": round(editorial_pct, 4),
            "pct_component": round(pct_component, 2),
            "abs_component": round(abs_component, 2),
        },
        "recommendation": rec,
    }


# ---------------------------------------------------------------------------
# Diagnostic 4 — MOOD_COHERENCE
# Measures how consistent the artist's emotional identity is across their top
# tracks.  For each of the top tracks we take the single dominant mood (from
# Musixmatch lyric analysis); coherence is the share of those tracks that hang
# on the SAME dominant mood.  A focused mood profile is easy for recommendation
# engines / radio / mood playlists to slot in; a scattered one is not.
# ---------------------------------------------------------------------------
def _mood_coherence(dominant_moods: list[str], skipped: int) -> dict:
    analyzed = len(dominant_moods)

    distribution: dict[str, int] = {}
    for mood in dominant_moods:
        distribution[mood] = distribution.get(mood, 0) + 1
    # Sort distribution by count desc for stable, readable output.
    distribution = dict(sorted(distribution.items(), key=lambda kv: kv[1], reverse=True))

    dominant_mood = next(iter(distribution), None)
    dominant_mood_count = distribution.get(dominant_mood, 0) if dominant_mood else 0

    if analyzed < 5:
        return {
            "name": "MOOD_COHERENCE",
            "score": 50,
            "status": "insufficient_data",
            "detail": {
                "tracks_analyzed": analyzed,
                "tracks_skipped": skipped,
                "dominant_mood": dominant_mood,
                "dominant_mood_count": dominant_mood_count,
                "mood_distribution": distribution,
            },
            "recommendation": (
                f"Only {analyzed} of the top tracks returned usable lyric mood analysis "
                f"({skipped} skipped — restricted or no analysis available). That is too few "
                "to assess mood coherence reliably. Ensure lyrics are delivered and "
                "unrestricted for the catalogue so mood-based recommendation surfaces "
                "(mood playlists, radio, auto-generated mixes) can classify the artist."
            ),
        }

    coherence = dominant_mood_count / analyzed * 100
    score = round(_clamp(coherence))

    if coherence >= 50:
        status = "good"
        rec = (
            f"{dominant_mood_count}/{analyzed} of the top tracks share a dominant mood of "
            f"'{dominant_mood}' ({coherence:.0f}%). This is a clear, recognisable emotional "
            "identity — recommendation systems, mood playlists and radio can confidently "
            "categorise the artist, which compounds discovery. Keep reinforcing this mood in "
            "metadata, artwork and promotion so the signal stays unambiguous."
        )
    elif coherence >= 25:
        status = "warning"
        rec = (
            f"The most common dominant mood is '{dominant_mood}' but it covers only "
            f"{dominant_mood_count}/{analyzed} of the top tracks ({coherence:.0f}%). The mood "
            "identity is somewhat mixed, which makes algorithmic categorisation less certain. "
            f"Consider leaning into '{dominant_mood}' across metadata and promotion for the "
            "tracks where it fits, to give recommendation engines a stronger primary signal."
        )
    else:
        status = "critical"
        rec = (
            f"The top tracks are spread across many moods — even the most frequent, "
            f"'{dominant_mood}', accounts for just {dominant_mood_count}/{analyzed} "
            f"({coherence:.0f}%). A scattered mood profile makes the artist hard to slot into "
            "mood playlists, radio and algorithmic mixes. Pick a primary mood to lead with and "
            f"lean into it ('{dominant_mood}' is the current front-runner) in metadata, "
            "playlist pitches and promotion so recommendation systems get a clear signal."
        )

    return {
        "name": "MOOD_COHERENCE",
        "score": score,
        "status": status,
        "detail": {
            "tracks_analyzed": analyzed,
            "tracks_skipped": skipped,
            "dominant_mood": dominant_mood,
            "dominant_mood_count": dominant_mood_count,
            "mood_distribution": distribution,
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
    mood_data: dict | None = None,
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
    mood_data : dict | None
        Pre-fetched lyric-analysis result for MOOD_COHERENCE, with keys:
        ``dominant_moods`` (list[str], one dominant mood per successfully analysed track)
        and ``skipped`` (int, count of top tracks whose analysis failed/was unavailable).
        API calls live in the caller; this module stays pure-logic.

    Returns
    -------
    dict with keys:
        overall_score  — int 0–100
        diagnostics    — list of diagnostic dicts
    """
    mood_data = mood_data or {"dominant_moods": [], "skipped": 0}

    d_catalogue = _catalogue_depth(tracks)
    d_concentration = _stream_concentration(tracks)
    d_reach = _playlist_reach(stats)
    d_mood = _mood_coherence(
        mood_data.get("dominant_moods", []),
        mood_data.get("skipped", 0),
    )

    diagnostics = [d_catalogue, d_concentration, d_reach, d_mood]
    overall = round(sum(d["score"] for d in diagnostics) / len(diagnostics))

    return {
        "artist_name": artist_data.get("artist_name", "Unknown"),
        "overall_score": overall,
        "overall_status": _status(overall),
        "diagnostics": diagnostics,
    }
