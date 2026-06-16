---
name: Musixmatch artist disambiguation
description: How to reliably resolve a query name to an artist's real high-volume catalogue on Musixmatch despite duplicate/ghost records.
---

Resolving a name to the correct Musixmatch artist_id is the hard part of this app. Two
provider behaviors make the naive approach fail:

- Musixmatch splits one real artist across many same-name records: 1-track "ghost"
  duplicates plus fragmentation, and separate "feat./variant" acts (e.g. "Taylor Swift
  feat. Bon Iver").
- `artist.search?q_artist` is NON-DETERMINISTIC and frequently omits the artist's real
  high-volume record from its first page entirely. Verified: across consecutive runs the
  738-track "Taylor Swift" record (id 259675) was absent from the top 30, whose max
  catalogue was only ~30 tracks. So "pick first/any/max among artist.search candidates"
  cannot work — the record isn't there.

**Working resolution strategy (`_resolve_artist_records` / `_select_canonical`):**
1. Build the candidate pool from TWO sources: `artist.search?q_artist` AND artist_ids mined
   from a rating-sorted `track.search?q_artist` (top ~2 pages). The real record's tracks
   dominate the rating-sorted track list, so this surfaces the id artist.search misses.
2. Probe each candidate's true catalogue size via `message.header.available` on a
   `track.search?f_artist_id&page_size=1` request (no pagination needed).
3. Drop aggregator/placeholder entities ("various artists", "soundtrack", "unknown artist", …)
   case-insensitively BEFORE selection — they carry huge volume and otherwise win on count
   alone (this is what made "Franco 126" resolve to "Various Artists"). Log each drop.
4. Pick canonical by NAME match first, track count only as a tiebreaker AMONG name matches.
   Name matching is accent/punctuation/spacing-insensitive (`_fuzzy` = accent-fold + lowercase
   + strip non-alnum). Merge exact and fuzzy into ONE name-matched pool, then `max(pool, key=count)`
   — do NOT prioritize exact-first, or a 1-track exact homonym "Celine Dion" beats the real
   520-track "Céline Dion". Fall back to global max, then first candidate.
5. LAZY space-stripped fallback: only when step 4 finds NO name match, retry with a despaced
   query ("Franco 126" → "Franco126"); Musixmatch indexes some acts without spaces. Lazy is
   essential — see rate-limit gotcha below.
6. Aggregate tracks across ALL same-(canonical)-name records (exact `_norm` match) via `f_artist_id`.

**RATE-LIMIT GOTCHA (cost real debugging time):** Musixmatch throttles bursts. Each query fires
a burst of ~50-100 concurrent `_probe` calls; when throttled, a probe's error path returns 0,
which silently UNDERCOUNTS the real record and corrupts selection (e.g. Katy Perry flapping
251→2→105 across runs, or empty responses). Doubling calls (eager despaced-variant for every
spaced query) tipped it over the edge. Two consequences: (a) make the despaced fallback lazy,
not eager; (b) **always test disambiguation in ISOLATION (one query at a time), not in a tight
loop** — loop failures are throttling artifacts, not logic bugs. Each name resolves correctly alone.

**How to apply:** any change to artist resolution must keep both candidate sources, the aggregator
filter, fuzzy name-match-before-count, and lazy despaced fallback. `compute_discoverability_score`
only reads `artist_name`, so a minimal `{artist_id, artist_name}` canonical is safe.
