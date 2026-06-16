---
name: Musixmatch updated_time is not a release date
description: Why a catalogue-recency/staleness signal built on Musixmatch track updated_time is noisy and rarely fires.
---

Musixmatch track `updated_time` marks when Musixmatch last touched *its own record* (metadata edits, reissues, sync deliveries), NOT when the artist released music.

**Observed (as of 2026-06-16):** the most-recent `updated_time` across all tracks was within ~6 weeks for every artist tested — Sufjan Stevens (same day), i cani (~6 weeks), Madonna (4 days) — even though Madonna had no recent new release. The catalogue therefore almost always looks "recently updated".

**Consequence:** a `years_since_update` staleness flag (e.g. the >3yr amber tint on the "Catalogue last updated" line in the report header) will rarely trip for any catalogued artist. Treat this as a metadata-recency signal at best, never as release activity.

**How to apply:** if asked to detect inactive/legacy catalogues or "hasn't released in years", `updated_time` won't do it — look for a real release-date field or a different provider instead.
