"""
Pull Lili Magaña's Instagram stats from the Meta Graph API and save them to stats.json
for her portfolio website.

Needs two secrets (never paste them into this file):
  IG_TOKEN  - the system-user access token from the Meta app
  IG_ID     - her Instagram user ID (the number under her account in "Generate access tokens")

Where the secrets come from:
  - Google Colab: click the key icon in the left sidebar, add IG_TOKEN and IG_ID,
    and turn on "Notebook access" for both.
  - GitHub Actions (step 3): repository secrets, passed in as environment variables.

Output:
  stats.json        - everything the website needs
  thumbs/<id>.jpg   - cover images for her recent reels (Instagram's own image links expire)
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com"
RECENT_POSTS = 24  # how many recent posts to pull for the video wall
OUT_FILE = Path("stats.json")
THUMB_DIR = Path("thumbs")


# ---------- secrets ----------
def get_secret(name):
    """Read a secret from Colab's key panel, or from an environment variable."""
    try:
        from google.colab import userdata  # only exists inside Colab
        value = userdata.get(name)
        if value:
            return value.strip()
    except Exception:
        pass
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing secret {name}. Add it in Colab's key panel or as an environment variable.")
    return value


TOKEN = get_secret("IG_TOKEN")
IG_ID = get_secret("IG_ID")


# ---------- API helper ----------
def call(path, **params):
    """GET a Graph API path. Returns the JSON, or None (with a printed warning) on an API error."""
    params["access_token"] = TOKEN
    for attempt in range(3):
        resp = requests.get(f"{GRAPH}/{path}", params=params, timeout=30)
        data = resp.json()
        if "error" not in data:
            return data
        err = data["error"]
        # rate limit or temporary error: wait and retry
        if err.get("code") in (1, 2, 4, 17, 32, 613) and attempt < 2:
            time.sleep(5 * (attempt + 1))
            continue
        print(f"  ! {path.split('?')[0]}: {err.get('message')}")  # never prints the token
        return None
    return None


def total_value(insights_json):
    """Turn an insights response into {metric_name: number}."""
    out = {}
    for item in (insights_json or {}).get("data", []):
        if "total_value" in item:
            out[item["name"]] = item["total_value"].get("value", 0)
        elif item.get("values"):
            out[item["name"]] = item["values"][-1].get("value", 0)
    return out


def country_breakdown(insights_json):
    """Turn a demographics response into a sorted list of {country, count, share}."""
    try:
        results = insights_json["data"][0]["total_value"]["breakdowns"][0]["results"]
    except (TypeError, KeyError, IndexError):
        return []
    rows = [{"country": r["dimension_values"][0], "count": r["value"]} for r in results]
    total = sum(r["count"] for r in rows) or 1
    for r in rows:
        r["share"] = round(r["count"] / total * 100, 1)
    return sorted(rows, key=lambda r: r["count"], reverse=True)


# ---------- 1. profile ----------
print("Pulling profile...")
profile = call(
    IG_ID,
    fields="username,name,biography,followers_count,follows_count,media_count,profile_picture_url,website",
) or {}
if not profile.get("username"):
    # stop here so yesterday's stats.json stays on the site (GitHub will email you that the run failed)
    raise SystemExit("Could not read her profile. Check that IG_TOKEN and IG_ID are correct and the token is still valid.")

# ---------- 2. account stats, last 30 days ----------
print("Pulling account stats (last 30 days)...")
now = datetime.now(timezone.utc)
since = int((now - timedelta(days=29)).timestamp())
until = int(now.timestamp())
account_30d = total_value(call(
    f"{IG_ID}/insights",
    metric="views,reach,accounts_engaged,total_interactions,likes,comments,shares,saves,profile_views",
    period="day",
    metric_type="total_value",
    since=since,
    until=until,
))

# ---------- 3. audience countries ----------
print("Pulling audience countries...")
followers_by_country = country_breakdown(call(
    f"{IG_ID}/insights",
    metric="follower_demographics",
    period="lifetime",
    metric_type="total_value",
    breakdown="country",
))
engaged_by_country = country_breakdown(call(
    f"{IG_ID}/insights",
    metric="engaged_audience_demographics",
    period="lifetime",
    timeframe="this_month",
    metric_type="total_value",
    breakdown="country",
))

# ---------- 4. recent posts + per-post stats ----------
print(f"Pulling last {RECENT_POSTS} posts...")
media = call(
    f"{IG_ID}/media",
    fields="id,caption,media_type,media_product_type,permalink,thumbnail_url,media_url,timestamp,like_count,comments_count",
    limit=RECENT_POSTS,
) or {"data": []}

THUMB_DIR.mkdir(exist_ok=True)
posts = []
for m in media.get("data", []):
    stats = total_value(call(f"{m['id']}/insights", metric="views,reach,saved,shares,total_interactions"))

    # save a copy of the cover image, since Instagram's image links expire after a few days
    thumb_url = m.get("thumbnail_url") or m.get("media_url")
    thumb_file = None
    if thumb_url and (m.get("thumbnail_url") or m.get("media_type") != "VIDEO"):
        try:
            img = requests.get(thumb_url, timeout=30)
            if img.ok:
                thumb_file = f"thumbs/{m['id']}.jpg"
                Path(thumb_file).write_bytes(img.content)
        except Exception:
            pass

    caption = (m.get("caption") or "").strip()
    posts.append({
        "id": m["id"],
        "type": m.get("media_product_type") or m.get("media_type"),  # REELS, FEED, ...
        "title": caption.split("\n")[0][:90],  # first line of the caption
        "caption": caption,
        "permalink": m.get("permalink"),
        "posted": m.get("timestamp"),
        "thumb": thumb_file,
        "likes": m.get("like_count", 0),
        "comments": m.get("comments_count", 0),
        "views": stats.get("views", 0),
        "reach": stats.get("reach", 0),
        "saves": stats.get("saved", 0),
        "shares": stats.get("shares", 0),
        "interactions": stats.get("total_interactions", 0),
    })

# remove cover images for posts that are no longer in the latest batch
keep = {p["thumb"] for p in posts if p["thumb"]}
for f in THUMB_DIR.glob("*.jpg"):
    if f"thumbs/{f.name}" not in keep:
        f.unlink()

# ---------- 5. figures for the website ----------
followers = profile.get("followers_count", 0) or 0
reels = [p for p in posts if p["type"] == "REELS"]
avg_reel_views = round(sum(p["views"] for p in reels) / len(reels)) if reels else 0
avg_interactions = (sum(p["interactions"] for p in posts) / len(posts)) if posts else 0
engagement_rate = round(avg_interactions / followers * 100, 2) if followers else 0

# posts per month, over the span of the posts pulled
posts_per_month = 0
if len(posts) >= 2:
    first = datetime.fromisoformat(posts[-1]["posted"].replace("+0000", "+00:00"))
    days = max((now - first).days, 1)
    posts_per_month = round(len(posts) / days * 30, 1)

stats_out = {
    "updated": now.isoformat(timespec="seconds"),
    "profile": {
        "username": profile.get("username"),
        "name": profile.get("name"),
        "bio": profile.get("biography"),
        "followers": followers,
        "following": profile.get("follows_count"),
        "posts": profile.get("media_count"),
    },
    "headline": {
        "followers": followers,
        "avg_reel_views": avg_reel_views,
        "engagement_rate_pct": engagement_rate,
        "posts_per_month": posts_per_month,
    },
    "last_30_days": account_30d,
    "audience": {
        "engaged_by_country": engaged_by_country,
        "followers_by_country": followers_by_country,
    },
    "posts": posts,
}

OUT_FILE.write_text(json.dumps(stats_out, indent=2, ensure_ascii=False), encoding="utf-8")

# ---------- summary ----------
print("\nDone. Saved stats.json")
print(f"  @{stats_out['profile']['username']}  ·  {followers:,} followers  ·  {len(posts)} posts pulled ({len(reels)} reels)")
print(f"  Avg reel views: {avg_reel_views:,}   Engagement rate: {engagement_rate}%   Posts/month: {posts_per_month}")
if account_30d:
    print("  Last 30 days: " + ", ".join(f"{k} {v:,}" for k, v in account_30d.items()))
top = engaged_by_country or followers_by_country
if top:
    print("  Top countries: " + ", ".join(f"{r['country']} {r['share']}%" for r in top[:5]))
