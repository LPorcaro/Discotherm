---
name: Songstats raw_stats + TikTok activity semantics
description: What Songstats raw_stats contains and the deliberate "TikTok active = official presence" scoring decision.
---

Songstats `/artists/stats` returns a list of `{source, data}` blocks; we key them by source into
`raw_stats` (sources seen: spotify, apple_music, amazon, deezer, instagram, tiktok, youtube, shazam,
soundcloud, tidal, beatport, facebook, twitter, songkick, bandsintown, …). Any source block can be
absent for a given artist — always `isinstance(x, dict)` guard before `.get`.

**TikTok: fan creates vs official presence (key distinction).** On the `tiktok` block:
- `videos_total` = number of fan-made videos using the artist's music (FAN engagement, can be huge
  even when the artist has no official TikTok).
- `followers_total` / `profile_videos_total` = the artist's OWN official platform presence.
These genuinely diverge: e.g. an artist had 667K `videos_total` but 0 followers/profile videos.

**Decision — PLATFORM_GAP treats TikTok "active" = official presence** (`followers_total>0 OR
profile_videos_total>0`), NOT `videos_total>0`.
**Why:** the product wants to flag "fans are already creating content but the artist isn't converting
it into platform-level discovery." If "active" keyed on `videos_total`, that case is unreachable
(TikTok would always be active whenever fan content exists). This is a deliberate scoring-behavior
change, not just rec text. When TikTok is inactive but `videos_total>0`, the PLATFORM_GAP rec swaps to
an "amplify existing fan content" message.
**How to apply:** all other platforms still use their single activity field (`value>0`); only TikTok
is special-cased. Fan-engagement UI uses `tiktok.videos_total` (creates) + `shazam.shazams_total`.
