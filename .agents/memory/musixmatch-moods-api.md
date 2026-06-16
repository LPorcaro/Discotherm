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
real "i cani" tracks).

# Musixmatch fragments one real artist across MANY duplicate `artist_id` records

`artist.search` returns multiple records that share the **identical** `artist_name`.
Two distinct phenomena, both must be handled when using `f_artist_id`:

1. **Homonyms / empties:** e.g. "i cani" → `89843183` (0 tracks) AND `13315249`
   (48 tracks); the empty one can rank first, so `artist_list[0].artist_id` yields 0.
2. **Catalogue fragmentation (the big one):** a single popular artist's catalogue is
   **split across dozens of same-name records**, each holding a small slice. "Katy Perry"
   → 25+ exact-name records: one has 238 tracks, one 8, several 1, most 0. NO single
   `artist_id` holds the full catalogue, so "pick the one record with the most tracks"
   still loses almost everything (gave 1 track).

**Why the app long used `q_artist`+exact-name filter:** it accidentally *aggregated by
name* across all fragments — the only reason mainstream artists returned full catalogues.

**Correct approach (what the app now does):** `artist.search` a WIDE page (page_size=30),
probe each candidate's `f_artist_id` (page_size=1), pick a canonical record that has
tracks for the display name, then **aggregate `track.search?f_artist_id` across every
record whose name == canonical name (case-insensitive) AND has tracks, dedupe by
`track_id`**. Exact-name match excludes "feat./variant" acts (e.g. "I CANI DEL DISAGIO",
"Osso & Sufjan Stevens") so precision is kept. Results: i cani=48, Sufjan=443, Katy=251.
`artist_rating` is `null` on this plan, so it can't disambiguate. Trade-off: more API
calls per report (probes + pages × records); acceptable per product owner.
