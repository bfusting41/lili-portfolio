# Lili Magaña — creator portfolio

Her bilingual portfolio site at **lilimagana.com**. Instagram stats refresh every morning on their own. Every number on the site comes from her own Instagram account through the daily script — the one exception is `corrections.json` (below), for posts Instagram's API undercounts.

## How it fits together

```
Meta API  →  pull_instagram_stats.py  →  stats.json + thumbs/  →  index.html (GitHub Pages, lilimagana.com)
                  (GitHub Actions, daily)
```

| File | What it does |
| --- | --- |
| `index.html` | The website. Reads `stats.json` and `categories.json`; shows sample content if they're missing (the Brand results section hides instead). |
| `favicon.svg`, `favicon-32.png`, `apple-touch-icon.png` | The LM tab icon (and the icon if someone saves the site to an iPhone home screen). |
| `pull_instagram_stats.py` | Pulls her profile, 30-day stats, audience breakdowns, her posts (newest 24 on her profile; 12 show on the site), video links for the reel player, brand campaigns found from her captions, and her pinned posts. |
| `campaigns.json` | The "Brand results" cards: one entry per brand, with the words to look for in her captions. |
| `pinned.json` | Links to her 3 pinned posts, shown in the phones at the top of the site. |
| `corrections.json` | Numbers Lili checked in her app for posts the API undercounts; they keep growing daily. |
| `shown.json` | Links to trial reels she later posted to her followers, so they count as on her profile. |
| `hidden.json` | Optional: a list of post IDs to keep off the site. |
| `.github/workflows/update-stats.yml` | Runs the script every day around 5:17 am Austin time and saves the results. |
| `categories.json` | Optional: tags posts as beauty, events, travel, food, hosp or life so the filter buttons appear. |
| `CNAME` | Created by GitHub for the custom domain. Leave it. |
| `stats.json`, `thumbs/`, `history/` | Written by the script. Don't edit by hand. |

## Secrets (Settings → Secrets and variables → Actions)

- `IG_TOKEN` — the system-user token from the Meta app (never expires)
- `IG_ID` — her Instagram user ID

## Domain

lilimagana.com is registered at Cloudflare and points to GitHub Pages: 4 A records and 4 AAAA records for `@`, and a CNAME for `www` → the GitHub Pages address. **All DNS records must stay "DNS only" (grey cloud)** — Cloudflare's proxy (orange cloud) stops GitHub from serving the site and its HTTPS certificate. Ignore Cloudflare's banner suggesting proxying. HTTPS is enforced in the repo's Settings → Pages. Don't start the "Visibility" Enterprise trial there — the site must stay public.

## Brand results (campaigns.json)

Each brand is found from her own posts: the script looks for posts **on her profile** (trial reels skipped) whose caption contains any of the `match` words, and adds up their views, likes and comments. **Hashtags are ignored** — she often hashtags brands on personal posts for reach, so only an @tag or the brand's name in the caption counts. Likes include likes from ads/boosts (Instagram's `total_like_count`).

```json
{"brand": "Skyscanner", "match": ["skyscanner"], "category": "travel", "en": "Travel storytelling", "es": "Historia de viaje"}
```

- `match` — lowercase words or handles to look for, e.g. `"@brandhandle"` or the brand name. Partial matches count (`"zeelool"` also matches `@zeelool_official`). Include the curly-apostrophe spelling for names like Victoria’s Secret.
- `"exclude": [links]` — leave specific posts out of that brand.
- `"posts": [links]` — name the exact posts instead of matching captions.
- `en` / `es` — the short label under the brand name.

The run log lists each brand with every post it matched — views, likes (total / organic / insights), comments, collaborators, and any matching "trial copy" — so you can check them against her profile.

Allowed categories: `beauty`, `events`, `travel`, `food`, `hosp`, `life`.

## Corrections (corrections.json)

Some reels — notably trial reels she later posted to her followers — are badly undercounted by Instagram's API (e.g. the Victoria's Secret reel: API 512 likes / 17.5K views vs 4,369 likes / 128K in her app). For those, Lili checks the real numbers in her app once:

```json
[{"post": "https://www.instagram.com/reel/DeqL5YbNz5f/", "as_of": "2026-11-25", "likes": 4369, "views": 128000}]
```

The site shows **her number + whatever the API adds after `as_of`**, so it keeps growing every day. `comments` is optional. `history/` keeps the raw API numbers, so the growth is always measured from real data. The log prints each correction as "checked number + growth = shown".

## The three phones at the top (pinned posts)

Instagram's API doesn't say which posts are pinned, so they're listed by hand. On Instagram, open each pinned post → ⋯ → Copy link, and paste the three links into `pinned.json`, in the order they appear on her profile (left to right). Links with `?igsh=...` on the end are fine. If a link isn't found, the phones fill in with her newest posts.

## Trial reels

Instagram's API returns trial reels (shown only to non-followers, not on her profile) with her other posts and has no field that marks them as trials. The script treats reels that aren't shared to her feed as trial reels and keeps them off the website, out of the averages and out of Brand results. They're still saved in `history/` (column `on_profile` = False).

The log shows the split, e.g. `found 400 posts: 269 on her profile, 131 reels not shared to her feed` — compare with the post count on her profile (272).

**Trial reels she later posted** are still reported as trials. Add their links to `shown.json` and they count as on her profile. The log lists **"Most-liked reels treated as trials"** — check those first.

If a post shows up that shouldn't, put its ID in `hidden.json`.

## Reel player

Each run saves the direct video link for every reel shown on the site (Instagram's links expire after a few days, so the daily run keeps them fresh). Hovering a reel plays a silent preview with a small control bar (play/pause, mute, volume); clicking opens a full player. Reels Instagram doesn't give a video link for (mostly reels with licensed music) open Instagram's own embedded player instead. Photos show their image. The log line `Videos for the site player: N of 24` shows how many have a direct link.

## Tagging posts by category

Open `stats.json`, copy a post's `"id"`, and add it to `categories.json`:

```json
{"18080841944732429": "beauty", "17918656446541196": "events"}
```

## Chosen by hand (not numbers)

- Which brands count as campaigns — `campaigns.json`. Pinned posts — `pinned.json`. Posted trial reels — `shown.json`. Hidden posts — `hidden.json`.
- Brand names in the scrolling partner row — `BRANDS` in index.html.
- App-checked numbers for undercounted posts — `corrections.json` (the one exception to "every number from the API").
- YouTube and TikTok aren't shown until those APIs are connected.

## Contact form

GitHub Pages can't send email, so the inquiry form opens the visitor's email app with the message pre-filled to Lilimagana19@gmail.com. A "Copy text" button is there as a fallback.

## Running it by hand

Actions tab → Update Instagram stats → Run workflow.
