---
name: Musixmatch lyric moods API shape
description: What track.lyrics.analysis.get actually returns for moods, vs common spec assumptions
---

# Musixmatch `track.lyrics.analysis.get` — moods

The `analysis.moods` object contains ONLY `main_moods`: an **ordered list of mood
strings** (most prominent first), e.g. `["heartbreak","nostalgia","love",...]`.
There are **no numeric per-mood values** anywhere in the response.

The actual mood vocabulary is the API's own (love, nostalgia, heartbreak,
reflection, despair, joy, hope, angst, empowerment, etc.). It does **not** match
the "25 documented Musixmatch mood labels" (peaceful, tender, sentimental,
melancholy, somber, easygoin, romantic, … aggressive) that specs often assume.

**Why this matters / how to apply:** any feature that needs `{label: value}` mood
weights must DERIVE the value (we use rank: weight = `n - rank`, most prominent
highest), and any feature seeded with the 25-label list must add API-returned
moods dynamically — the 25 labels end up as all-zero dimensions in practice.

Other `analysis` top-level keys: `themes`, `rating`, `meaning`, `moderation`,
`religion`, `entities`.

# `track.search` is fuzzy on `q_artist`, not exact

`track.search?q_artist=<name>` is a **tokenized full-text** match, NOT an exact
artist match. For short/common-token names (e.g. "i cani") the token "i" matches
masses of unrelated popular artists (The 1975, Korol i Shut, Bob Marley, i-dle…);
sorted by global `s_track_rating=desc` those popular tracks fill all pages and
**bury the real artist's catalogue**. For "i cani", 500 results contained only 1
genuine track — the app's exact-name filter was correct, the input was polluted.

**The true catalogue lives under `track.search?f_artist_id=<id>`** (returned ~48
real "i cani" tracks). **Why the app still uses `q_artist`:** homonyms. `artist.search`
returns multiple records with the identical `artist_name` (e.g. "i cani" → 89843183
with 0 tracks AND 13315249 with 48 tracks), and the empty one can rank first — so
naively picking `artist_list[0].artist_id` for `f_artist_id` can yield 0 tracks.
Any switch to `f_artist_id` must pick the homonym whose `f_artist_id` actually
returns tracks (e.g. probe each candidate, choose the one with the most tracks).
`artist_rating` is `null` on this plan, so it can't disambiguate homonyms.
