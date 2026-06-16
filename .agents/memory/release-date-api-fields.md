---
name: Release-date API field availability
description: Where a real release date can/can't be sourced for the Discotherm app (Musixmatch vs Songstats)
---

# Release-date signals across our music APIs

**Musixmatch has NO usable release date on our plan.** The `track.search` track object
keys contain no release field at all — only `updated_time`, which marks when *their record*
was last edited (metadata edits, reissues, sync deliveries), NOT a release. It is misleading
as a recency signal (a 2012 track can show a 2017–2025 `updated_time`). The artist object
only has `begin_date`/`end_date` (career span, usually empty/`0000-00-00`). `artist.albums.get`
/ `album.get` require a paid plan and return empty for us.

**Songstats `/artists/stats` has no date fields** in any of its ~18 source blocks.

**Songstats `/artists/catalog` is the real source.** It returns a genuine per-track
`release_date` (plus `title`, `artists[]`, `isrcs`, `tracks_total`, `next_url` pagination,
`limit` up to 100, returned newest-first). This is what to use for "last release".

**Why:** investigated when replacing the misleading "Catalogue last updated" (Musixmatch
`updated_time`) indicator with a real "Last release" date.

**Caveat — remix pollution:** for large artists the top of the catalogue is flooded with
third-party DJ/fan remixes/edits that credit the artist (e.g. Rihanna's newest catalogue
entries are "(… Remix)" / "(… Edit)" uploads, some with placeholder `default-track.svg`
avatars). There is no `type` field to distinguish single/album/remix. So "newest release of
any kind" surfaces a remix as a superstar's latest. Product decision (current): accept that
and show the newest of any kind. If this needs to change later, filter by title heuristic
(exclude remix/edit/version/live/demo) and/or paginate beyond the first 100.
