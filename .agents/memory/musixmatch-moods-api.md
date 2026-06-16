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
