---
name: Scale-aware diagnostic recommendations
description: How discoverability-check tailors recommendation TEXT (not scores) to artist audience size.
---

# Scale-aware recommendations (discoverability-check/scoring.py)

PLAYLIST_REACH, STREAM_CONCENTRATION, and CATALOGUE_DEPTH recommendation **text** is tier-aware.
Scores/thresholds are identical across tiers — only the prose framing changes.

**Tier classification** (`_artist_tier`): from Songstats Spotify `monthly_listeners_current`
(fallback `followers_total`, default `emerging` when absent):
- emerging `<100k`, mid-tier `100k–5M`, major `>5M`.

**Framing convention:**
- emerging → keep actionable pitching/to-do advice.
- mid-tier/major → reframe the *same numbers* as a STRUCTURAL finding (a property of how
  editorial/playlist/metadata systems work at scale), explicitly NOT a failing of the artist's team.

**Why:** for established acts, "submit richsync files" / "pitch to editorial" reads as naive;
the insight at scale is structural (editorial slot scarcity, hits-dominate-catalogue dynamics,
legacy back-catalogue metadata gaps), not a task list.

**How to apply:** if adding a new diagnostic or editing rec text, thread `tier` through and branch on
`established = tier in ("mid-tier","major")`. Never let tiering touch the score math.

**Gotcha:** the `/artist/report` response's top-level `raw_stats` does NOT expose
`spotify.monthly_listeners_current` (it reads None there), but `compute_discoverability_score`
DOES receive the full stats — so tiering works even though that debug field looks empty.
To inspect real audience numbers, curl `/artist/stats` (returns flattened `spotify` dict).
