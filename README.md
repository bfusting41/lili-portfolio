# Lili Magaña — creator portfolio

Her bilingual portfolio site. Instagram stats refresh every morning on their own.

## How it fits together

```
Meta API  →  pull_instagram_stats.py  →  stats.json + thumbs/  →  index.html (GitHub Pages)
                  (GitHub Actions, daily)
```

| File | What it does |
| --- | --- |
| `index.html` | The website. Reads `stats.json` and `categories.json`; shows sample content if they're missing. |
| `pull_instagram_stats.py` | Pulls her profile, 30-day stats, top countries and latest 24 posts. |
| `.github/workflows/update-stats.yml` | Runs the script every day around 5:17 am Austin time and saves the results. |
| `categories.json` | Optional: tags posts as beauty, events, travel, food, hosp or life so the filter buttons appear. |
| `stats.json`, `thumbs/` | Written by the script. Don't edit by hand. |

## Secrets (Settings → Secrets and variables → Actions)

- `IG_TOKEN` — the system-user token from the Meta app (never expires)
- `IG_ID` — her Instagram user ID

## Tagging posts by category

Open `stats.json`, copy a post's `"id"`, and add it to `categories.json`:

```json
{
  "18080841944732429": "beauty",
  "17918656446541196": "events"
}
```

Allowed values: `beauty`, `events`, `travel`, `food`, `hosp`, `life`. Untagged posts still show under "All".

## Running it by hand

Actions tab → Update Instagram stats → Run workflow.
