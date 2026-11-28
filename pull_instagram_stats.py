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

Reads (optional):
  campaigns.json    - brand campaigns for the "Brand results" section: post ID + brand + label
  hidden.json       - post IDs to keep off the website, e.g. a trial reel that slipped through
  pinned.json       - links to her 3 pinned posts, shown in the phones at the top of the site

Output:
  stats.json        - everything the website needs
  thumbs/<id>.jpg   - cover images for her recent reels and campaign posts (Instagram's own image links expire)
  history/          - dated records for the future Statistics tab: account, posts (incl. reel watch time),
                      countries, age/gender/city, tagged posts
"""

import csv
import json
import re
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com"
DISPLAY_POSTS = 24   # newest posts shown on the website's video wall
RECENT_DAYS = 90     # posts newer than this get fresh stats every day
FULL_REFRESH_DAY = 6 # Sunday: refresh stats for every post she has ever made (0 = Monday)
OUT_FILE = Path("stats.json")
THUMB_DIR = Path("thumbs")
HISTORY_DIR = Path("history")  # dated copies kept forever, for the future Statistics tab
CAMPAIGNS_FILE = Path("campaigns.json")  # brand campaigns shown on the site; edit this file to add or remove one
HIDDEN_FILE = Path("hidden.json")        # optional: list of post IDs to keep off the website (still kept in history)
SHOWN_FILE = Path("shown.json")          # optional: trial reels she later shared to followers (links or IDs) — count them as on her profile
REELS_FILE = Path("reels.json")          # reel categories for the site's shelves: Lili's picks by link (+ the script's guesses)
CORRECTIONS_FILE = Path("corrections.json")  # optional: numbers Lili checked in her app for posts the API undercounts
PINNED_FILE = Path("pinned.json")        # optional: her pinned posts (links or IDs) for the phones at the top of the site


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


# ---------- campaigns ----------
def load_campaigns():
    """Read campaigns.json: one entry per brand:
         {"brand", "match": [words to look for in her captions], "category", "en", "es"}
       optional "posts": [links or IDs] to name the exact posts instead of matching captions.
    Missing or broken file = no campaigns."""
    if not CAMPAIGNS_FILE.exists():
        return []
    try:
        rows = json.loads(CAMPAIGNS_FILE.read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict) and r.get("brand") and (r.get("match") or r.get("posts"))]
    except Exception as e:
        print(f"  ! campaigns.json could not be read ({e}); skipping brand results")
        return []


def load_hidden():
    """Read hidden.json: a plain list of post IDs. Missing or broken file = nothing hidden."""
    try:
        return {str(x) for x in json.loads(HIDDEN_FILE.read_text(encoding="utf-8"))} if HIDDEN_FILE.exists() else set()
    except Exception as e:
        print(f"  ! hidden.json could not be read ({e}); ignoring it")
        return set()


def load_pinned():
    """Read pinned.json: a list of post links (https://www.instagram.com/reel/ABC123/) or post IDs, in order.
    Instagram's API doesn't say which posts are pinned, so they're listed by hand."""
    try:
        rows = json.loads(PINNED_FILE.read_text(encoding="utf-8")) if PINNED_FILE.exists() else []
    except Exception as e:
        print(f"  ! pinned.json could not be read ({e}); using her newest posts instead")
        return []
    keys = []
    for r in rows:
        r = str(r).strip()
        parts = [x for x in r.split("?")[0].split("/") if x]
        keys.append(parts[-1] if "instagram.com" in r and parts else r)  # link -> its shortcode, ID stays as is
    return keys[:3]


CAMPAIGNS = load_campaigns()
PINNED = load_pinned()
HIDDEN = load_hidden()
def post_key(value):
    """A post ID stays as is; an Instagram link (…/p/ABC123/?igsh=…) becomes its code, ABC123."""
    v = str(value).strip()
    parts = [x for x in v.split("?")[0].split("/") if x]
    return parts[-1] if "instagram.com" in v and parts else v


def load_shown():
    """Read shown.json: links or IDs of trial reels she later shared to her followers. Instagram still reports those
    as 'not shared to feed', so without this list they'd be treated as hidden trial reels."""
    try:
        rows = json.loads(SHOWN_FILE.read_text(encoding="utf-8")) if SHOWN_FILE.exists() else []
        return {post_key(r) for r in rows}
    except Exception as e:
        print(f"  ! shown.json could not be read ({e}); ignoring it")
        return set()


SHOWN = load_shown()





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


def breakdown(insights_json, key="country"):
    """Turn a demographics response into a sorted list of {<key>, count, share}."""
    try:
        results = insights_json["data"][0]["total_value"]["breakdowns"][0]["results"]
    except (TypeError, KeyError, IndexError):
        return []
    rows = [{key: r["dimension_values"][0], "count": r["value"]} for r in results]
    total = sum(r["count"] for r in rows) or 1
    for r in rows:
        r["share"] = round(r["count"] / total * 100, 1)
    return sorted(rows, key=lambda r: r["count"], reverse=True)


def country_breakdown(insights_json):
    return breakdown(insights_json, "country")


def demographics(metric, dim, **extra):
    """Follower or engaged-audience demographics split by age, gender, city or country."""
    return breakdown(call(f"{IG_ID}/insights", metric=metric, period="lifetime",
                          metric_type="total_value", breakdown=dim, **extra), dim)


def link_taps(since_ts, until_ts):
    """Taps on the links in her bio (asked on its own so a problem here can't block anything else)."""
    out = total_value(call(f"{IG_ID}/insights", metric="profile_links_taps", period="day",
                           metric_type="total_value", since=since_ts, until=until_ts))
    return {"profile_links_taps": out["profile_links_taps"]} if "profile_links_taps" in out else {}


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

account_30d.update(link_taps(since, until))

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

# age, gender and top cities (tracked daily for the Statistics tab; not shown on the main site yet)
print("Pulling audience age, gender and cities...")
demo = {}
for who, metric, extra in (("followers", "follower_demographics", {}),
                           ("engaged", "engaged_audience_demographics", {"timeframe": "this_month"})):
    for dim in ("age", "gender", "city"):
        demo[f"{who}_by_{dim}"] = demographics(metric, dim, **extra)

# ---------- 4. every post, plus per-post stats ----------
print("Pulling her full post list...")
# total_like_count includes likes from ads/boosts made from the post (like_count leaves those out).
# If Instagram ever rejects that field, fall back to the plain list so the run still works.
BASE_FIELDS = "id,caption,media_type,media_product_type,is_shared_to_feed,permalink,thumbnail_url,media_url,timestamp,like_count,comments_count"
fields = BASE_FIELDS + ",total_like_count"
all_media, after = [], None
while True:
    page = call(f"{IG_ID}/media", fields=fields, limit=100, **({"after": after} if after else {}))
    if not page and not all_media and fields != BASE_FIELDS:
        print("  ! total_like_count not available; using like_count (leaves out likes from ads)")
        fields = BASE_FIELDS
        continue
    if not page:
        break
    all_media += page.get("data", [])
    after = (page.get("paging") or {}).get("cursors", {}).get("after")
    if not (page.get("paging") or {}).get("next") or not after:
        break


def shortcode_of(m):
    return ([x for x in (m.get("permalink") or "").split("/") if x][-1:] or [""])[0]


def on_profile(m):
    """False for trial reels (shown only to non-followers, never on her profile) and anything in hidden.json.
    Instagram has no 'trial reel' field; reels not shared to her feed are the closest signal."""
    if m["id"] in HIDDEN:
        return False
    if m["id"] in SHOWN or shortcode_of(m) in SHOWN:
        return True  # a trial reel she later posted to her followers
    return not (m.get("media_product_type") == "REELS" and m.get("is_shared_to_feed") is False)


public_ids = [m["id"] for m in all_media if on_profile(m)]
show_ids = set(public_ids[:DISPLAY_POSTS])
not_feed = sum(1 for m in all_media if m.get("media_product_type") == "REELS" and m.get("is_shared_to_feed") is False)
print(f"  found {len(all_media)} posts: {len(public_ids)} on her profile, {not_feed} reels not shared to her feed (likely trial reels), "
      f"{len(HIDDEN & {m['id'] for m in all_media})} hidden by hand, "
      f"{sum(1 for m in all_media if m['id'] in SHOWN or shortcode_of(m) in SHOWN)} trial reels marked as posted")
print(f"  her profile's own post count: {profile.get('media_count')}")


# brand campaigns come only from her own posts: posts on her profile whose caption mentions the brand
# (trial reels are skipped, so the numbers match what people see on her profile)
campaign_ids = []
for c in CAMPAIGNS:
    named = {post_key(x) for x in c.get("posts", [])}
    terms = [t.lower() for t in c.get("match", []) if t]
    if named:
        ids = [m["id"] for m in all_media if m["id"] in named or shortcode_of(m) in named]
    else:
        # hashtags are ignored: she often hashtags brands on personal posts just for reach; a real brand post @tags or names them
        text = lambda m: re.sub(r"#[^\s#]+", " ", (m.get("caption") or "").lower())
        # a term listed WITH a "#" (e.g. "#pinkpartner") is a hashtag she uses for that brand, so it's matched as a hashtag
        # handles ("@brand") match with or without the "@" (the API often drops it), as a whole word so "@heb" can't hit "hebrew"
        def hit(m, t):
            if t.startswith("#"):
                return t in (m.get("caption") or "").lower()
            if t.startswith("@"):
                return re.search(r"(?<![a-z0-9_.])@?" + re.escape(t[1:]) + r"(?![a-z0-9_])", text(m)) is not None
            return t in text(m)
        ids = [m["id"] for m in all_media if on_profile(m) and any(hit(m, t) for t in terms)]
    skip = {post_key(x) for x in c.get("exclude", [])}
    if skip:
        ids = [i for i in ids if i not in skip and shortcode_of(next(m for m in all_media if m["id"] == i)) not in skip]
    campaign_ids.append(ids)
CAMPAIGN_IDS = {i for ids in campaign_ids for i in ids}

# ---------- reel categories (the shelves on the site) ----------
# One category per reel. Lili's picks in reels.json win; the rest are guessed from caption words; reels with no
# category stay off the site. Within a category the best-performing reels show first.
CATS = ["skincare", "haircare", "makeup", "travel", "hospitality", "lifestyle"]
CAT_WORDS = {
    "skincare": ["skincare", "skin care", "serum", "moisturizer", "sunscreen", "spf", "acne", "breakout", "pimple", "cleanser",
                 "toner", "retinol", "dark spots", "hyperpigmentation", "pores", "eye cream", "face mask", "glass skin",
                 "madhippiebeauty", "madhippie_atx", "mad hippie", "esteelauder", "estée lauder", "estee lauder", "cuidado de la piel"],
    "haircare": ["haircare", "hair care", "shampoo", "conditioner", "dry shampoo", "batiste", "batiste_us", "hair mask", "scalp",
                 "frizz", "blowout", "hair routine", "hair oil", "cocoandeve", "coco & eve", "coco and eve", "cabello"],
    "makeup": ["makeup", "make up", "maquillaje", "grwm", "get ready with me", "lipstick", "lip combo", "lip liner", "lip gloss",
               "lipgloss", "lashes", "mascara", "foundation", "concealer", "blush", "eyeliner", "eyeshadow", "contour", "morphe",
               "ultabeauty", "ulta", "glam", "nails", "btartboxnails", "fragrance", "perfume", "origenfragrances", "beauty"],
    "travel": ["travel", "trip", "flight", "airport", "skyscanner", "vacation", "itinerary", "passport", "app", "viaje", "vuelo"],
    "hospitality": ["restaurant", "brunch", "dinner", "lunch", "menu", "chef", "tasting", "foodie", "pizza", "tacos", "taqueria",
                    "pancakes", "cafe", "coffee", "matcha", "cocktail", "cocktails", "wine", "prosecco", "hotel", "suite",
                    "staycation", "resort", "spa", "toastique", "acquabistecca", "acqua bistecca", "jettspizza", "yachtclubatx",
                    "capebottleroom", "hoteltrinityatx", "jpspancakecompany", "taqueriade10", "taqueriadediez", "heb",
                    "bellissimaprosecco", "comida", "restaurante",
                    "food", "eats", "dish", "dessert", "drinks", "happy hour", "seafood", "sushi", "burger", "steak",
                    "pasta", "breakfast", "bakery", "bites", "desayuno", "cena", "postre"],
    "lifestyle": ["outfit", "ootd", "fit check", "style", "fashion", "day in my life", "vlog", "weekend",
                  "gym", "workout", "vspink", "#pinkpartner", "zeelool", "glasses", "eyewear",
                  "victoriassecret", "victoria's secret", "victoria’s secret", "fashion show"],
}


def word_hit(text, term):
    return re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text) is not None


def guess_cat(caption):
    # like brand matching, hashtags are ignored (they're often copied onto unrelated posts) unless the word is listed with "#"
    full = (caption or "").lower()
    text = re.sub(r"#[^\s#]+", " ", full)
    scores = {c: sum(word_hit(full, w[1:]) and ("#" + w[1:]) in full if w.startswith("#") else word_hit(text, w)
                     for w in CAT_WORDS[c]) for c in CATS}
    best = max(CATS, key=lambda c: (scores[c], -CATS.index(c)))
    return best if scores[best] else None


try:
    REELS_CFG = json.loads(REELS_FILE.read_text(encoding="utf-8")) if REELS_FILE.exists() else {}
except Exception as e:
    print(f"  ! reels.json could not be read ({e}); using the script's guesses only")
    REELS_CFG = {}
picked = {post_key(x): c for c in CATS + ["none"] for x in REELS_CFG.get(c, [])}
use_guesses = REELS_CFG.get("use_guesses", True)
only_picks = set(REELS_CFG.get("only_picks", []))  # categories that show ONLY the links Lili listed (no guesses)
reel_cat, reel_cat_source = {}, {}
for m in all_media:
    k1, k2 = m["id"], shortcode_of(m)
    if k1 in picked or k2 in picked:
        c = picked.get(k1, picked.get(k2))
        if c != "none":
            reel_cat[m["id"]], reel_cat_source[m["id"]] = c, "Lili"
    elif use_guesses and on_profile(m) and m.get("media_product_type") == "REELS":
        c = guess_cat(m.get("caption"))
        if c and c not in only_picks:
            reel_cat[m["id"]], reel_cat_source[m["id"]] = c, "guess"

# old posts barely change, so: newest posts and campaign posts every day, everything once a week (and on the very first run)
first_run = not (HISTORY_DIR / "daily_posts.csv").exists()
full_refresh = first_run or datetime.now(timezone(timedelta(hours=-6))).weekday() == FULL_REFRESH_DAY
recent_cutoff = now - timedelta(days=RECENT_DAYS)
print("  refreshing stats for " + ("every post (weekly full refresh)" if full_refresh else f"posts from the last {RECENT_DAYS} days + campaign posts"))

THUMB_DIR.mkdir(exist_ok=True)
all_posts = []
for i, m in enumerate(all_media):
    posted = m.get("timestamp")
    posted_dt = datetime.fromisoformat(posted.replace("+0000", "+00:00")) if posted else None
    shortcode = [shortcode_of(m)]
    is_campaign = bool({m["id"], shortcode[0]} & (CAMPAIGN_IDS | set(PINNED))) or m["id"] in reel_cat  # same daily care
    shown = m["id"] in show_ids
    refresh = full_refresh or shown or is_campaign or (posted_dt and posted_dt >= recent_cutoff)
    stats = {}
    if refresh:
        stats = total_value(call(f"{m['id']}/insights", metric="views,reach,saved,shares,total_interactions"))
        time.sleep(0.2)  # stay well under Instagram's rate limits
        if m.get("media_product_type") == "REELS":
            # watch time is asked separately so a problem here can't wipe out the views above
            stats.update(total_value(call(f"{m['id']}/insights",
                                          metric="ig_reels_avg_watch_time,ig_reels_video_view_total_time")))
            time.sleep(0.2)

    # cover images only for posts shown on the site (Instagram's image links expire after a few days)
    thumb_file = None
    thumb_url = m.get("thumbnail_url") or m.get("media_url")
    # category reels get their cover after we know which ones make each tab's top 12 (brand and pinned posts always get one now)
    shelf = m["id"] in reel_cat and not ({m["id"], shortcode[0]} & (CAMPAIGN_IDS | set(PINNED)))
    if (shown or (is_campaign and not shelf)) and thumb_url and (m.get("thumbnail_url") or m.get("media_type") != "VIDEO"):
        try:
            img = requests.get(thumb_url, timeout=30)
            if img.ok:
                thumb_file = f"thumbs/{m['id']}.jpg"
                Path(thumb_file).write_bytes(img.content)
        except Exception:
            pass

    caption = (m.get("caption") or "").strip()
    all_posts.append({
        "id": m["id"],
        "type": m.get("media_product_type") or m.get("media_type"),  # REELS, FEED, ...
        "media_type": m.get("media_type"),                            # VIDEO, IMAGE, CAROUSEL_ALBUM
        "title": caption.split("\n")[0][:90],  # first line of the caption
        "caption": caption,
        "permalink": m.get("permalink"),
        "posted": posted,
        "thumb": thumb_file,
        # direct video link for the site's player; Instagram's links expire after a few days, so this is refreshed every run
        # Lili's own copy in videos/<code>.mp4 wins: Instagram gives no video link for reels with licensed music
        "video": (f"videos/{shortcode[0]}.mp4" if shortcode[0] and Path(f"videos/{shortcode[0]}.mp4").exists()
                  else m.get("media_url") if m.get("media_type") == "VIDEO" else None),
        "refreshed": bool(refresh),
        "on_profile": on_profile(m),
        "_turl": thumb_url if (m.get("thumbnail_url") or m.get("media_type") != "VIDEO") else None,
        "shortcode": shortcode[0],
        "shared_to_feed": m.get("is_shared_to_feed"),
        "likes": m.get("total_like_count") or m.get("like_count", 0),  # incl. likes from ads/boosts
        "likes_organic": m.get("like_count", 0),
        "comments": m.get("comments_count", 0),
        "views": stats.get("views", 0),
        "reach": stats.get("reach", 0),
        "saves": stats.get("saved", 0),
        "shares": stats.get("shares", 0),
        "interactions": stats.get("total_interactions", 0),
        # Instagram reports watch time in milliseconds
        "avg_watch_s": round(stats.get("ig_reels_avg_watch_time", 0) / 1000, 1),
        "total_watch_min": round(stats.get("ig_reels_video_view_total_time", 0) / 60000, 1),
    })

public_posts = [p for p in all_posts if p["on_profile"]]
# ---------- 4a. corrections from her app (for posts Instagram's API undercounts) ----------
# corrections.json: [{"post": link, "as_of": "YYYY-MM-DD", "likes": 4369, "views": 128000}] (comments optional).
# Shown value = her checked number + whatever the API has added since that date, so it keeps growing every day.
# The raw API numbers are still what goes into history/, so each day's growth is measured from real API data.
try:
    CORRECTIONS = json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8")) if CORRECTIONS_FILE.exists() else []
except Exception as e:
    print(f"  ! corrections.json could not be read ({e}); ignoring it")
    CORRECTIONS = []
FIELDS = ("likes", "views", "comments")
for p in all_posts:
    p["api"] = {f: p[f] for f in FIELDS}
if CORRECTIONS:
    past = []
    if (HISTORY_DIR / "daily_posts.csv").exists():
        with (HISTORY_DIR / "daily_posts.csv").open(newline="", encoding="utf-8") as f:
            past = list(csv.DictReader(f))
    print("  Corrections from her app (+ API growth since):")
    for c in CORRECTIONS:
        key = post_key(c.get("post", ""))
        p = next((q for q in all_posts if q["id"] == key or q["shortcode"] == key), None)
        if not p:
            print(f"  ! correction for {c.get('post')} - post not found")
            continue
        as_of = str(c.get("as_of", ""))
        rows = sorted((r for r in past if r.get("post_id") == p["id"] and r.get("date", "") >= as_of), key=lambda r: r["date"])
        base = {f: int(float(rows[0][f])) if rows and rows[0].get(f) not in (None, "") else p["api"][f] for f in FIELDS}
        parts = []
        for f in FIELDS:
            if isinstance(c.get(f), (int, float)):
                growth = max(0, p["api"][f] - base[f])
                p[f] = int(c[f]) + growth
                parts.append(f"{f} {int(c[f]):,} + {growth:,} = {p[f]:,}")
        p["corrected"] = True
        print(f"      {p['permalink']} (checked {as_of}): " + " · ".join(parts))

posts = public_posts[:DISPLAY_POSTS]  # what the website shows: newest posts that are actually on her profile

# ---------- 4b. brand campaigns: every matching post on her profile, added up per brand ----------
by_id = {p["id"]: p for p in all_posts}
SUM = ("views", "reach", "likes", "comments", "saves", "shares", "interactions")
campaigns = []
for c, ids in zip(CAMPAIGNS, campaign_ids):
    ps = [by_id[i] for i in ids if i in by_id]
    if not ps:
        print(f"  ! {c['brand']}: no posts on her profile matched {c.get('match') or c.get('posts')}; left off the site")
        continue
    top = max(ps, key=lambda p: p["views"])
    dates = sorted(p["posted"] for p in ps if p["posted"])
    campaigns.append({
        "brand": c["brand"], "category": c.get("category", ""), "en": c.get("en", ""), "es": c.get("es", ""),
        "post_count": len(ps),
        "first_posted": dates[0] if dates else None, "last_posted": dates[-1] if dates else None,
        "id": top["id"], "permalink": top["permalink"], "thumb": top["thumb"], "video": top["video"],  # the best-performing post represents the card
        **{k: sum(p[k] or 0 for p in ps) for k in SUM},
        "post_links": [p["permalink"] for p in sorted(ps, key=lambda p: p["views"], reverse=True)],
        "_posts": sorted(ps, key=lambda p: p["views"], reverse=True),  # log only, removed before saving
    })
campaigns.sort(key=lambda r: r["views"], reverse=True)
# each card gets its own cover: if a brand's best post is already another (bigger) brand's cover, use its next-best post
used_covers = set()
for c in campaigns:
    pick = next((p for p in c["_posts"] if p["id"] not in used_covers), c["_posts"][0])
    used_covers.add(pick["id"])
    c.update({"id": pick["id"], "permalink": pick["permalink"], "thumb": pick["thumb"], "video": pick["video"]})
# total across all brand campaigns, counting a post tagged with two brands only once
campaigns_total_views = sum(p["views"] or 0 for p in {p["id"]: p for c in campaigns for p in c["_posts"]}.values())
print(f"  {len(campaigns)} of {len(CAMPAIGNS)} campaigns found")

# brands she @mentions that aren't campaigns yet, to help spot ones worth adding
covered = [t.lower() for c in CAMPAIGNS for t in c.get("match", [])]
mention_counts = {}
for p in public_posts:
    for h in set(re.findall(r"@([A-Za-z0-9._]+)", p["caption"])):
        h = h.rstrip(".").lower()
        if h and not any(h in t or t.lstrip("@#") in h for t in covered):
            mention_counts[h] = mention_counts.get(h, 0) + 1
top_mentions = sorted(mention_counts.items(), key=lambda kv: -kv[1])[:15]

# ---------- 4c. the three phones at the top of the site: her pinned posts (or newest, if pinned.json is empty) ----------
pinned = []
for key in PINNED:
    p = by_id.get(key) or next((q for q in all_posts if q["shortcode"] == key), None)
    if p:
        pinned.append(p)
    else:
        print(f"  ! pinned post {key} not found on her account")
pinned += [p for p in posts if p["thumb"] and p not in pinned][:3 - len(pinned)]  # fill any gap with her newest posts
pinned_out = [{k: p[k] for k in ("id", "permalink", "thumb", "title", "type")} for p in pinned]
print(f"  phones at the top: {len(pinned_out)} " + ("pinned posts" if PINNED else "newest posts (pinned.json is empty)"))

# per-reel tags (reels.json "formats"): {tag: [links, or a few words from the caption]} overrides the category's usual tag
FORMAT_NAMES = ("problem_solution", "testimonial", "experience", "personal_style")
squash = lambda x: " ".join(str(x).lower().split())
FORMAT_RULES = [(f, post_key(x), squash(x)) for f in FORMAT_NAMES for x in REELS_CFG.get("formats", {}).get(f, [])]
format_hits = {}


def format_of(p):
    for f, key, words in FORMAT_RULES:
        if p["id"] == key or p["shortcode"] == key or (" " in words and words in squash(p["caption"])):
            format_hits[words] = format_hits.get(words, 0) + 1
            return f
    return None


# ---------- 4e. reel shelves: top reels per category, highest views first ----------
SHELF_MAX = 12
followers_now = profile.get("followers_count", 0) or 0
shelves, shelf_posts = {}, []
for c in CATS:
    ps = sorted((p for p in all_posts if reel_cat.get(p["id"]) == c), key=lambda p: p["views"], reverse=True)
    if not ps:
        continue
    top = ps[:SHELF_MAX]
    for p in top:
        if not p["thumb"] and p.get("_turl"):
            try:
                img = requests.get(p["_turl"], timeout=30)
                if img.ok:
                    p["thumb"] = f"thumbs/{p['id']}.jpg"
                    Path(p["thumb"]).write_bytes(img.content)
            except Exception:
                pass
    shelf_posts += top
    inter = sum(p["interactions"] for p in ps) / len(ps)
    shelves[c] = {
        "count": len(ps), "views": sum(p["views"] for p in ps), "avg_views": round(sum(p["views"] for p in ps) / len(ps)),
        "engagement_pct": round(inter / followers_now * 100, 2) if followers_now else 0,
        "items": [{k: p[k] for k in ("id", "permalink", "thumb", "video", "title", "views", "likes", "comments", "posted", "type", "media_type")}
                  | {"caption": p["caption"][:600], "picked": reel_cat_source.get(p["id"]) == "Lili", "format": format_of(p)} for p in top],
    }

# remove cover images for posts that dropped off the website
keep = {p["thumb"] for p in posts + campaigns + pinned + shelf_posts if p["thumb"]}
for f in THUMB_DIR.glob("*.jpg"):
    if f"thumbs/{f.name}" not in keep:
        f.unlink()

# ---------- 4d. posts where other accounts (often brands) tagged her ----------
print("Pulling posts she's tagged in...")
tagged = (call(f"{IG_ID}/tags", fields="id,username,caption,permalink,timestamp,media_type,like_count,comments_count", limit=50) or {}).get("data", [])
print(f"  {len(tagged)} tagged posts" if tagged else "  none returned (this needs the instagram_manage_comments permission; see meta-api-setup.md)")

# ---------- 5. figures for the website ----------
followers = profile.get("followers_count", 0) or 0
reels = [p for p in posts if p["type"] == "REELS"]
avg_reel_views = round(sum(p["views"] for p in reels) / len(reels)) if reels else 0
avg_interactions = (sum(p["interactions"] for p in posts) / len(posts)) if posts else 0
timed = [p for p in reels if p["avg_watch_s"]]
avg_watch_s = round(sum(p["avg_watch_s"] for p in timed) / len(timed), 1) if timed else 0
engagement_rate = round(avg_interactions / followers * 100, 2) if followers else 0

# posts per month, averaged over the last 90 days
recent_count = sum(1 for p in public_posts if p["posted"] and
                   datetime.fromisoformat(p["posted"].replace("+0000", "+00:00")) >= recent_cutoff)
posts_per_month = round(recent_count / (RECENT_DAYS / 30), 1)

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
        "avg_reel_watch_s": avg_watch_s,
    },
    "last_30_days": account_30d,
    "audience": {
        "engaged_by_country": engaged_by_country,
        "followers_by_country": followers_by_country,
        **demo,
    },
    "campaigns": [{k: v for k, v in c.items() if k != "_posts"} for c in campaigns],
    "campaigns_total_views": campaigns_total_views,
    "reels": shelves,
    # the Photos page shows every image in photos/, in file-name order (p01.jpg, p02.jpg, ...)
    # (sub-folders count too, e.g. photos/photos/p01.jpg from a folder upload)
    "photos": sorted((f.relative_to("photos").as_posix() for f in Path("photos").rglob("*")
                      if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")),
                     key=lambda x: x.split("/")[-1]) if Path("photos").is_dir() else [],
    "pinned": pinned_out,
    "posts": posts,
}

OUT_FILE.write_text(json.dumps(stats_out, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- 6. history: keep a dated copy of every day ----------
def upsert_csv(path, rows, key_fields):
    """Add rows to a CSV, replacing any existing rows with the same key (so a re-run on the same day doesn't duplicate)."""
    if not rows:
        return
    existing = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    new_keys = {tuple(str(r[k]) for k in key_fields) for r in rows}
    kept = [r for r in existing if tuple(str(r.get(k, "")) for k in key_fields) not in new_keys]
    fields = list(rows[0].keys())
    for r in kept:  # keep any columns older files had
        fields += [k for k in r.keys() if k not in fields]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept + rows)


print("Saving history...")
HISTORY_DIR.mkdir(exist_ok=True)
(HISTORY_DIR / "snapshots").mkdir(exist_ok=True)
today = datetime.now(timezone(timedelta(hours=-6))).date().isoformat()  # Austin date

# yesterday's own numbers (not a rolling 30-day total), so daily trends can be charted later
day_end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
day_start = day_end - timedelta(days=1)
yesterday = total_value(call(
    f"{IG_ID}/insights",
    metric="views,reach,accounts_engaged,total_interactions,likes,comments,shares,saves,profile_views",
    period="day",
    metric_type="total_value",
    since=int(day_start.timestamp()),
    until=int(day_end.timestamp()),
))

# new followers gained yesterday (asked separately so a hiccup here can't block the rest)
yesterday.update(total_value(call(
    f"{IG_ID}/insights", metric="follower_count", period="day",
    since=int(day_start.timestamp()), until=int(day_end.timestamp()),
)))

yesterday.update(link_taps(int(day_start.timestamp()), int(day_end.timestamp())))

# 1) full copy of today's stats.json
(HISTORY_DIR / "snapshots" / f"{today}.json").write_text(
    json.dumps(stats_out, indent=2, ensure_ascii=False), encoding="utf-8")

# 2) one row per day for the account
upsert_csv(HISTORY_DIR / "daily_account.csv", [{
    "date": today,
    "followers": followers,
    "following": profile.get("follows_count", ""),
    "total_posts": profile.get("media_count", ""),
    **{f"yesterday_{k}": v for k, v in yesterday.items() if not isinstance(v, (dict, list))},
    **{f"last30_{k}": v for k, v in account_30d.items() if not isinstance(v, (dict, list))},
}], ["date"])

# 3) one row per post per day, so each post's growth curve builds up over time (campaign posts included every day)
upsert_csv(HISTORY_DIR / "daily_posts.csv", [{
    "date": today, "post_id": p["id"], "posted": p["posted"], "type": p["type"], "on_profile": p["on_profile"],
    "views": p["api"]["views"], "reach": p["reach"], "likes": p["api"]["likes"], "likes_organic": p["likes_organic"], "comments": p["api"]["comments"],
    "saves": p["saves"], "shares": p["shares"], "interactions": p["interactions"],
    "avg_watch_s": p["avg_watch_s"], "total_watch_min": p["total_watch_min"],
} for p in all_posts if p["refreshed"]], ["date", "post_id"])

# 4) one row per country per day
upsert_csv(HISTORY_DIR / "daily_countries.csv",
    [{"date": today, "audience": "engaged", **r} for r in engaged_by_country]
    + [{"date": today, "audience": "followers", **r} for r in followers_by_country],
    ["date", "audience", "country"])

# 4b) age, gender and city shares per day
upsert_csv(HISTORY_DIR / "daily_demographics.csv", [
    {"date": today, "audience": name.split("_by_")[0], "dimension": dim, "value": r[dim], "count": r["count"], "share": r["share"]}
    for name, rows in demo.items() for dim in [name.split("_by_")[1]] for r in rows
], ["date", "audience", "dimension", "value"])

# 4c) tagged posts, one row each (likes and comments updated on each run)
upsert_csv(HISTORY_DIR / "tagged.csv", [{
    "post_id": t["id"], "by": t.get("username", ""), "posted": t.get("timestamp", ""),
    "media_type": t.get("media_type", ""), "likes": t.get("like_count", ""), "comments": t.get("comments_count", ""),
    "permalink": t.get("permalink", ""), "caption": (t.get("caption") or "").strip(), "last_seen": today,
} for t in tagged], ["post_id"])

# 5) post details (caption, link) once per post, updated if they change
upsert_csv(HISTORY_DIR / "posts.csv", [{
    "post_id": p["id"], "posted": p["posted"], "type": p["type"], "media_type": p["media_type"],
    "on_profile": p["on_profile"], "shared_to_feed": p["shared_to_feed"],
    "permalink": p["permalink"], "caption": p["caption"],
} for p in all_posts], ["post_id"])

# ---------- summary ----------
print("\nDone. Saved stats.json")
print(f"  @{stats_out['profile']['username']}  ·  {followers:,} followers  ·  {len(all_posts)} posts in history "
      f"({len(public_posts)} on her profile), {len(posts)} on the site ({len(reels)} reels)")
print(f"  Videos for the site player: {sum(1 for p in posts if p['video'])} of {len(posts)} posts have a playable link")
print(f"  Avg reel views: {avg_reel_views:,}   Engagement rate: {engagement_rate}%   Posts/month: {posts_per_month}   Avg reel watch: {avg_watch_s}s")
for name, rows in demo.items():
    if rows:
        print(f"  {name}: " + ", ".join(f"{list(r.values())[0]} {r['share']}%" for r in rows[:4]))
    else:
        print(f"  {name}: not returned")
if account_30d:
    print("  Last 30 days: " + ", ".join(f"{k} {v:,}" for k, v in account_30d.items()))
top = engaged_by_country or followers_by_country
if top:
    print("  Top countries: " + ", ".join(f"{r['country']} {r['share']}%" for r in top[:5]))
trials = sorted((p for p in all_posts if not p["on_profile"] and p["id"] not in HIDDEN), key=lambda p: p["likes"], reverse=True)[:5]
if trials:
    print("  Most-liked reels treated as trials (if one is on her profile, add its link to shown.json): "
          + ", ".join(f"{p['permalink']} ({p['likes']:,} likes)" for p in trials))
boosted = [p for p in all_posts if p["likes"] > p["likes_organic"]]
if boosted:
    print(f"  {len(boosted)} posts have extra likes from ads/boosts (counted in the totals), e.g. "
          + ", ".join(f"{p['permalink']} {p['likes_organic']:,} -> {p['likes']:,}" for p in boosted[:3]))
def collab_names(media_id):
    """Accounts invited as collaborators on a post (asked on its own; a problem here can't affect anything else)."""
    data = (call(f"{media_id}/collaborators", fields="username,invite_status") or {}).get("data", [])
    return ", ".join(f"@{d.get('username')}" + ("" if d.get("invite_status") in (None, "Accepted") else f" ({d.get('invite_status')})") for d in data)


print("  Reel shelves (Lili's picks marked *; move or remove any with reels.json):")
for c in CATS:
    if c in shelves:
        sh = shelves[c]
        print(f"    {c}: {sh['count']} reels, {sh['views']:,} views, avg {sh['avg_views']:,}, engagement {sh['engagement_pct']}%")
        for it in sh["items"]:
            print(f"      {'*' if it['picked'] else ' '} {it['permalink']}  {it['views']:,} views{'  [' + it['format'] + ']' if it['format'] else ''}  — {it['title'][:50]}")
    else:
        print(f"    {c}: no reels yet")
no_vid = [it for c in CATS if c in shelves for it in shelves[c]["items"] if not it["video"] and it["media_type"] == "VIDEO"]
print(f"  Reels on the site with no video file (they open Instagram's player and don't preview; to fix one, upload the reel to "
      f"videos/ named <code>.mp4, e.g. videos/DSTxMCvDQcW.mp4): {len(no_vid)}")
for it in no_vid:
    print(f"      {it['permalink']}  -> videos/{it['permalink'].rstrip('/').split('/')[-1]}.mp4")
for f, key, words in FORMAT_RULES:
    if words not in format_hits:
        print(f"  ! tag rule {f}: '{words[:60]}' didn't match any reel on the site (check the link or caption words)")
none_reels = [p for p in public_posts if p["type"] == "REELS" and p["id"] not in reel_cat]
print(f"  Reels with no category (kept off the site): {len(none_reels)}"
      + (", e.g. " + ", ".join(p["permalink"] for p in none_reels[:5]) if none_reels else ""))
for c in campaigns:
    print(f"  Campaign {c['brand']}: {c['post_count']} post(s), {c['views']:,} views, {c['likes']:,} likes, {c['comments']:,} comments")
    for p in c["_posts"]:
        ins = total_value(call(f"{p['id']}/insights", metric="likes"))
        collab = collab_names(p["id"])
        print(f"      {p['permalink']}  views {p['views']:,} · likes {p['likes']:,} (organic {p['likes_organic']:,}"
              f"{', insights ' + format(ins['likes'], ',') if 'likes' in ins else ''}) · comments {p['comments']:,}"
              f"{' · collab with ' + collab if collab else ''}")
        # does a "trial" copy of this same video exist? (same caption opening, posted within 30 days)
        key = " ".join(p["caption"].lower().split())[:60]
        if key:
            pdt = datetime.fromisoformat(p["posted"].replace("+0000", "+00:00")) if p["posted"] else None
            for q in all_posts:
                if q["on_profile"] or q["id"] == p["id"] or " ".join(q["caption"].lower().split())[:60] != key:
                    continue
                qdt = datetime.fromisoformat(q["posted"].replace("+0000", "+00:00")) if q["posted"] else None
                gap = abs((pdt - qdt).days) if pdt and qdt else None
                if gap is None or gap <= 30:
                    if not q["refreshed"]:  # older posts only refresh on Sundays; get today's numbers for this check
                        q["views"] = total_value(call(f"{q['id']}/insights", metric="views")).get("views", q["views"])
                    print(f"         ↳ matching trial copy: {q['permalink']}  views {q['views']:,} · likes {q['likes']:,} · comments {q['comments']:,}"
                          f" · posted {q['posted'][:10] if q['posted'] else '?'} (profile copy {p['posted'][:10] if p['posted'] else '?'})")
if top_mentions:
    print("  Other accounts she @mentions most (possible campaigns): " + ", ".join(f"@{h} ({n})" for h, n in top_mentions))
