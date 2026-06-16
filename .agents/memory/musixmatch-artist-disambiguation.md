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

**Working resolution strategy (`_resolve_artist_records`):**
1. Build the candidate pool from TWO sources: `artist.search?q_artist` AND artist_ids mined
   from a rating-sorted `track.search?q_artist` (top ~2 pages). The real record's tracks
   dominate the rating-sorted track list, so this surfaces the id artist.search misses.
2. Probe each candidate's true catalogue size via `message.header.available` on a
   `track.search?f_artist_id&page_size=1` request (no pagination needed).
3. Pick canonical = highest track count **among candidates whose name exactly matches the
   query** (case/space-normalized). The exact-name gate is essential: fuzzy `track.search`
   also surfaces unrelated high-volume artists (searching "Katy Perry" pulls in Pat Boone
   ~1914 and Bob Marley ~1128 tracks). Fall back to global max, then first candidate.
4. Aggregate tracks across ALL same-(canonical)-name records with tracks via `f_artist_id`.

**Why:** the previous "first candidate with ≥1 track" picked a 1-track ghost; even "max
among artist.search candidates" fails because the real record is missing from that endpoint.

**How to apply:** any change to artist resolution must keep both candidate sources and the
exact-name gate. `compute_discoverability_score` only reads `artist_name` from the artist
dict, so a minimal `{artist_id, artist_name}` canonical (as built from track.search) is safe.
