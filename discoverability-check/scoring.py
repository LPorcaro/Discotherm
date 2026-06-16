"""
Pure-logic discoverability scoring. No API calls.

Usage:
    from scoring import compute_discoverability_score
    result = compute_discoverability_score(artist_data, tracks, stats)
"""

from __future__ import annotations

import math

import numpy as np

# The 25 mood labels documented for Musixmatch lyric analysis. The live API
# actually returns its own ordered ``main_moods`` vocabulary (e.g. "love",
# "nostalgia"), so these seed the vector space and any unseen moods returned by
# the API are appended dynamically (see ``_mood_coherence``).
BASE_MOODS = [
    "peaceful", "tender", "sentimental", "melancholy", "somber", "easygoin",
    "romantic", "sophisticated", "cool", "gritty", "upbeat", "empowering",
    "sensual", "yearning", "serious", "lively", "stirring", "fiery", "urgent",
    "brooding", "excited", "rowdy", "energizing", "defiant", "aggressive",
]


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
# tracks.  Each track is turned into a mood VECTOR over a shared label space
# (the 25 BASE_MOODS plus any moods the API returns, added dynamically). A
# mood's weight is derived from its rank in the track's ordered ``main_moods``
# list (most prominent = highest), 0 if absent. We take the catalogue centroid
# (mean vector) and measure how tightly each track clusters around it via cosine
# similarity. A high average similarity = a consistent emotional profile that is
# easy for recommendation engines to categorise; a low one = a scattered profile.
# ---------------------------------------------------------------------------
def _mood_coherence(track_moods: list[list[str]], skipped: int) -> dict:
    # Keep only tracks that actually returned moods.
    valid = [moods for moods in track_moods if moods]
    analyzed = len(valid)

    if analyzed < 5:
        return {
            "name": "MOOD_COHERENCE",
            "score": 50,
            "status": "insufficient_data",
            "detail": {
                "tracks_analyzed": analyzed,
                "tracks_skipped": skipped,
                "avg_cosine_similarity": None,
                "top_moods": [],
            },
            "recommendation": (
                f"Only {analyzed} of the top tracks returned usable lyric mood analysis "
                f"({skipped} skipped — restricted or no analysis available). That is too few "
                "to assess mood-profile consistency reliably. Ensure lyrics are delivered and "
                "unrestricted for the catalogue so mood-based recommendation surfaces "
                "(mood playlists, radio, auto-generated mixes) can classify the artist."
            ),
        }

    # Build the shared mood vocabulary: 25 base labels + any extras seen.
    vocab = list(BASE_MOODS)
    seen = set(vocab)
    for moods in valid:
        for mood in moods:
            if mood not in seen:
                seen.add(mood)
                vocab.append(mood)
    index = {mood: i for i, mood in enumerate(vocab)}

    # One rank-weighted vector per track: first (most prominent) mood gets the
    # highest weight, decreasing down the ordered list; absent moods stay 0.
    matrix = np.zeros((analyzed, len(vocab)), dtype=float)
    for row, moods in enumerate(valid):
        n = len(moods)
        for rank, mood in enumerate(moods):
            matrix[row, index[mood]] = n - rank

    centroid = matrix.mean(axis=0)
    centroid_norm = np.linalg.norm(centroid)

    if centroid_norm == 0:
        avg_cosine = 0.0
    else:
        track_norms = np.linalg.norm(matrix, axis=1)
        sims = []
        for row in range(analyzed):
            if track_norms[row] == 0:
                continue
            sims.append(float(matrix[row] @ centroid) / (track_norms[row] * centroid_norm))
        avg_cosine = float(np.mean(sims)) if sims else 0.0

    score = round(_clamp(avg_cosine * 100))

    # Top 3 moods by average weight across the catalogue (for UI context).
    mean_weights = matrix.mean(axis=0)
    ranked = sorted(
        ((vocab[i], mean_weights[i]) for i in range(len(vocab)) if mean_weights[i] > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )
    top_moods = [mood for mood, _ in ranked[:3]]
    top_phrase = ", ".join(top_moods) if top_moods else "n/a"

    if score >= 50:
        status = "good"
        rec = (
            f"Across the top {analyzed} tracks the artist's mood profile is highly consistent "
            f"(average similarity {avg_cosine:.2f}) — their tracks share a coherent emotional "
            f"identity led by {top_phrase}. That consistency lets recommendation systems, mood "
            "playlists and radio confidently categorise the artist, compounding discovery. "
            "Keep reinforcing this profile in metadata, artwork and promotion."
        )
    elif score >= 25:
        status = "warning"
        rec = (
            f"The artist's mood profile is moderately consistent across the top {analyzed} "
            f"tracks (average similarity {avg_cosine:.2f}); the catalogue leans toward "
            f"{top_phrase} but varies noticeably track to track, which makes algorithmic "
            "genre/mood matching less precise. Consider foregrounding the dominant moods in "
            "metadata and promotion for the tracks where they fit to sharpen the signal."
        )
    else:
        status = "critical"
        rec = (
            f"The artist's mood profile varies significantly across the top {analyzed} tracks "
            f"(average similarity {avg_cosine:.2f}), which may make algorithmic genre/mood "
            f"matching less precise — even the catalogue's strongest moods ({top_phrase}) are "
            "not consistent enough to anchor a recognisable identity. Pick a primary mood to "
            "lead with and reinforce it in metadata, playlist pitches and promotion so "
            "recommendation systems get a clearer signal."
        )

    return {
        "name": "MOOD_COHERENCE",
        "score": score,
        "status": status,
        "detail": {
            "tracks_analyzed": analyzed,
            "tracks_skipped": skipped,
            "avg_cosine_similarity": round(avg_cosine, 4),
            "top_moods": top_moods,
        },
        "recommendation": rec,
    }


# ---------------------------------------------------------------------------
# Diagnostic 5 — PLATFORM_GAP
# Measures how broadly the artist is actually *active* across the major
# discovery surfaces, using the Songstats raw_stats already fetched. We only
# score platforms that Songstats actually returned for this artist; for each we
# define a "meaningful activity" threshold against whatever field best signals
# real presence (not just catalogue existence). Score = active / available.
#
# Target platforms and chosen activity thresholds (field → why):
#   spotify     monthly_listeners_current > 0  — live listening audience
#   apple_music playlists_total          > 0  — appears in Apple's playlist graph
#   tiktok      videos_total             > 0  — tracks/sounds being created with
#   youtube     video_views_total        > 0  — watched video presence
#   shazam      shazams_total            > 0  — being Shazam'd (ambient discovery)
#   deezer      followers_total          > 0  — an actual Deezer following
#   soundcloud  streams_total            > 0  — real plays on SoundCloud
# ---------------------------------------------------------------------------
# (raw_stats source key, activity field, display name, discovery role for recs)
PLATFORM_GAP_TARGETS = [
    ("spotify", "monthly_listeners_current", "Spotify",
     "build monthly listeners through release cadence and editorial pitching"),
    ("apple_music", "playlists_total", "Apple Music",
     "pursue Apple Music editorial playlist placements for curated discovery"),
    ("tiktok", "videos_total", "TikTok",
     "seed the catalogue as a TikTok sound to chase viral, creator-driven reach"),
    ("youtube", "video_views_total", "YouTube",
     "publish/claim official and Shorts content to capture search and recommendation traffic"),
    ("shazam", "shazams_total", "Shazam",
     "drive radio/sync/ambient plays so listeners Shazam the tracks they hear"),
    ("deezer", "followers_total", "Deezer",
     "grow a Deezer following and pitch its editorial playlists, especially in EU markets"),
    ("soundcloud", "streams_total", "SoundCloud",
     "upload and engage on SoundCloud to tap its early-adopter and remix discovery culture"),
]


def _platform_gap(stats: dict, logger=None) -> dict:
    available: list[str] = []   # display names present in raw_stats
    active: list[str] = []
    inactive: list[str] = []
    missing: list[str] = []     # target platforms Songstats didn't return at all

    for source_key, field, display, _role in PLATFORM_GAP_TARGETS:
        data = stats.get(source_key)
        if not isinstance(data, dict):
            missing.append(display)
            continue
        available.append(display)
        value = data.get(field) or 0
        if isinstance(value, (int, float)) and value > 0:
            active.append(display)
        else:
            inactive.append(display)

    # Surface Songstats' real coverage gaps in the logs.
    if missing and logger is not None:
        logger.info(
            "PLATFORM_GAP: %d/%d target platforms missing entirely from Songstats "
            "raw_stats: %s", len(missing), len(PLATFORM_GAP_TARGETS), ", ".join(missing)
        )

    total = len(available)
    if total == 0:
        return {
            "name": "PLATFORM_GAP",
            "score": 50,
            "status": "insufficient_data",
            "detail": {
                "platforms_checked": [],
                "platforms_active": [],
                "platforms_inactive": [],
            },
            "recommendation": (
                "Songstats returned none of the tracked discovery platforms (Spotify, Apple "
                "Music, TikTok, YouTube, Shazam, Deezer, SoundCloud) for this artist, so "
                "cross-platform presence cannot be assessed. Confirm the artist is correctly "
                "matched on Songstats and that their profiles are linked."
            ),
        }

    score = round(_clamp(len(active) / total * 100))

    if score >= 70:
        status = "good"
    elif score >= 40:
        status = "warning"
    else:
        status = "critical"

    role_map = {d: role for _k, _f, d, role in PLATFORM_GAP_TARGETS}
    if inactive:
        gaps = "; ".join(f"{name} — {role_map[name]}" for name in inactive)
        gap_sentence = (
            f"Inactive on {len(inactive)} of {total} available platforms. Close the gaps: {gaps}."
        )
    else:
        gap_sentence = (
            f"Active on all {total} available platforms — a broad, healthy discovery footprint."
        )

    if status == "good":
        rec = (
            f"Strong cross-platform presence: active on {len(active)}/{total} platforms "
            f"({score}%). {gap_sentence} Maintain this breadth so the artist surfaces wherever "
            "listeners discover music."
        )
    elif status == "warning":
        rec = (
            f"Uneven cross-platform presence: active on {len(active)}/{total} platforms "
            f"({score}%). {gap_sentence} Prioritise the platforms above whose audience best fits "
            "the artist to widen discovery."
        )
    else:
        rec = (
            f"Concentrated on too few platforms: active on only {len(active)}/{total} "
            f"({score}%). {gap_sentence} Relying on a narrow set of surfaces caps discovery — "
            "expand onto the missing platforms above."
        )

    return {
        "name": "PLATFORM_GAP",
        "score": score,
        "status": status,
        "detail": {
            "platforms_checked": available,
            "platforms_active": active,
            "platforms_inactive": inactive,
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
    logger=None,
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
        ``track_moods`` (list[list[str]], the ordered ``main_moods`` list for each
        successfully analysed track) and ``skipped`` (int, count of top tracks whose
        analysis failed/was unavailable). API calls live in the caller; this module
        stays pure-logic.
    logger : logging.Logger | None
        Optional logger; PLATFORM_GAP uses it to record which target platforms are
        missing entirely from Songstats' response (its real coverage gaps).

    Returns
    -------
    dict with keys:
        overall_score  — int 0–100
        diagnostics    — list of diagnostic dicts
    """
    mood_data = mood_data or {"track_moods": [], "skipped": 0}

    d_catalogue = _catalogue_depth(tracks)
    d_concentration = _stream_concentration(tracks)
    d_reach = _playlist_reach(stats)
    d_mood = _mood_coherence(
        mood_data.get("track_moods", []),
        mood_data.get("skipped", 0),
    )
    d_platform = _platform_gap(stats, logger=logger)

    diagnostics = [d_catalogue, d_concentration, d_reach, d_mood, d_platform]
    # Average only over diagnostics with usable data; insufficient_data ones are
    # excluded so they neither help nor hurt the overall score.
    valid = [d for d in diagnostics if d["status"] != "insufficient_data"]
    overall = round(sum(d["score"] for d in valid) / len(valid)) if valid else 0

    return {
        "artist_name": artist_data.get("artist_name", "Unknown"),
        "overall_score": overall,
        "overall_status": _status(overall),
        "diagnostics": diagnostics,
    }
