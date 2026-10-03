#!/usr/bin/env python3
"""
Chair Watcher — watches Czech/Slovak/neighboring marketplaces for a specific
vintage chair (by keywords AND by visual similarity to reference photos), logs
every candidate for browsing in the web dashboard (dashboard.py), and pushes
an iPhone notification via ntfy.sh for the confident matches.

RECOMMENDED WAY TO RUN THIS: see README.md and run `python3 dashboard.py`,
which gives you a mobile-friendly web page (viewable on your iPhone) listing
every candidate found, a "Scan Now" button to trigger an immediate scan from
your phone, AND handles the recurring automatic scanning — all in one process.

This file (chair_watcher.py) is the scanning engine underneath that; it can
also be run standalone (`python3 chair_watcher.py --once` or `--loop-minutes N`)
for a no-dashboard, notification-only, cron-friendly setup if you prefer that.

HOW MATCHING WORKS
-------------------
- Each site module fetches recent listings matching broad keywords (chair-related
  Czech/Slovak/German/Polish words, PLUS "kodreta"/"hreščák" directly — covering
  both sellers who have no idea what they have, and ones who do).
- For each new listing (one we haven't seen before — tracked in seen.json),
  the script downloads the listing photo(s) and computes a CLIP image embedding,
  then compares it to your reference photo embeddings using cosine similarity
  (color-tolerant — it's comparing overall shape/structure, not exact color).
- Two tiers: a listing clearing the higher "notify" threshold, or matching a
  strong keyword, gets you a push notification. A listing clearing the lower
  "list" threshold still gets saved to the dashboard for you to eyeball, just
  without pinging your phone — this is how you see "similar chairs worth a
  second look" without being spammed.
- This is inherently fuzzy. Expect some false positives (other tubular-steel
  chairs will score fairly high) — that's the tradeoff for catching mislabeled
  listings. Tune the two thresholds in config.json if you get too much noise.

LIMITATIONS (read this)
------------------------
- Facebook Marketplace actively blocks scraping and requires a logged-in
  session; the fb module here is best-effort and WILL likely need you to
  paste in fresh cookies periodically, or may stop working entirely. Treat it
  as a bonus, not the core of this tool — Bazoš/Sbazar/OLX/Kleinanzeigen are
  the reliable parts.
- Site HTML changes over time and will eventually break a scraper module.
  If a site stops returning results, that module likely needs a small update.
- This is for personal use monitoring public listing pages you could browse
  yourself — be reasonable with request frequency (defaults are already
  polite: one pass per site per run, small random delays).
"""

import argparse
import json
import os
import re
import sys
import time
import random
import hashlib
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
SEEN_PATH = BASE_DIR / "seen.json"
FOUND_PATH = BASE_DIR / "found_listings.json"
REF_DIR = BASE_DIR / "reference_photos"
MAX_FOUND_STORED = 500

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "cs-CZ,cs;q=0.9,sk;q=0.8,en;q=0.5",
}

# Safe to auto-trust: distinctive brand/model terms with no real-world
# name-collision risk. A hit on any of these alone is treated as a confident
# match regardless of image score.
STRONG_KEYWORDS = [
    "kodreta", "t2306", "t 2306", "chlebo",
]

# NOT auto-trusted on their own: "hreščák"/"hrescak" is a real Slovak surname
# (confirmed via real scan data — unrelated sellers named Jaroslav Hreščák
# turned up selling fire extinguishers, garden sprayers, a motorcycle, none
# of it furniture). Still searched for directly below, since a seller who
# knows the designer's name is a great signal — but a hit on JUST this word
# still has to clear the image-similarity bar like any other listing,
# instead of auto-qualifying as a "Name match".
WEAK_NAME_KEYWORDS = ["hreščák", "hrescak"]

# Name/model search terms — searched directly on every site, in case a
# seller who DOES know what they have used these exact words.
NAME_KEYWORDS = ["kodreta", "jaroslav hreščák", "hreščák", "t2306"]

# Broad, generic terms — these catch listings where the seller has NO idea
# what the chair is and just described it by appearance. Site modules
# combine these with furniture-category filters where the site supports it.
BROAD_KEYWORDS_CZ_SK = [
    "trubková židle plátno", "trubkové kreslo", "chromová židle režná",
    "stolička kodreta", "kreslo chróm plátno", "retro trubková stolička",
] + NAME_KEYWORDS
BROAD_KEYWORDS_DE = ["Kodreta stuhl", "Stahlrohr stuhl Leinen Vintage"] + NAME_KEYWORDS
BROAD_KEYWORDS_PL = ["krzeslo chromowane vintage plotno", "kodreta krzeslo"] + NAME_KEYWORDS


_ENV_CONFIG_MAP = {
    "NTFY_TOPIC": ("ntfy_topic", str),
    "SIMILARITY_THRESHOLD_NOTIFY": ("similarity_threshold_notify", float),
    "SIMILARITY_THRESHOLD_LIST": ("similarity_threshold_list", float),
    "USE_IMAGE_MATCHING": ("use_image_matching", lambda v: v.strip().lower() in ("1", "true", "yes")),
    "ENABLED_SITES": ("enabled_sites", lambda v: [s.strip() for s in v.split(",") if s.strip()]),
    "FACEBOOK_SESSION_PATH": ("facebook_session_path", str),
    "FACEBOOK_LOCATION": ("facebook_location", str),
}


def load_config():
    """
    Loads config.json if present, then lets environment variables override
    or fill in individual settings (see _ENV_CONFIG_MAP above). This is what
    lets the GitHub Actions workflow pass your ntfy topic in as a secret
    (see .github/workflows/scan.yml) instead of committing it to the repo.
    """
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text())

    for env_key, (config_key, caster) in _ENV_CONFIG_MAP.items():
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        try:
            config[config_key] = caster(raw)
        except Exception as e:
            print(f"  [!] Could not parse env var {env_key}={raw!r}: {e}")

    if not config:
        sys.exit(
            "No configuration found. Either copy config.example.json to config.json "
            "and edit it, or set the equivalent environment variables (see CLOUD_SETUP.md)."
        )
    return config


def load_seen():
    if SEEN_PATH.exists():
        return set(json.loads(SEEN_PATH.read_text()))
    return set()


def save_seen(seen):
    SEEN_PATH.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=0))


def load_found():
    """All listings ever flagged as a match or possible match — read by the
    iPhone-facing web dashboard to show a browsable list."""
    if FOUND_PATH.exists():
        try:
            return json.loads(FOUND_PATH.read_text())
        except Exception:
            return []
    return []


def save_found(items):
    items = items[:MAX_FOUND_STORED]
    FOUND_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2))


def listing_id(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def send_notification(topic, title, message, url=None):
    try:
        headers = {"Title": title.encode("utf-8")}
        if url:
            headers["Click"] = url
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
    except Exception as e:
        print(f"  [!] Failed to send notification: {e}")


def text_has_strong_keyword(text):
    t = text.lower()
    return any(k in t for k in STRONG_KEYWORDS)


def text_has_weak_name_keyword(text):
    t = text.lower()
    return any(k in t for k in WEAK_NAME_KEYWORDS)


# ---------------------------------------------------------------------------
# CLIP-based image similarity
# ---------------------------------------------------------------------------

class ImageMatcher:
    """Loads CLIP once, embeds the reference photos, and scores new images
    against them by cosine similarity."""

    def __init__(self, ref_dir):
        import open_clip
        import torch
        self.torch = torch
        print("Loading CLIP model (first run downloads ~350MB, then it's cached)...")
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        self.model.eval()
        self.ref_embeddings = []
        ref_files = list(Path(ref_dir).glob("*"))
        if not ref_files:
            sys.exit(f"No reference photos found in {ref_dir} — add at least one photo of the chair.")
        from PIL import Image
        for f in ref_files:
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            try:
                img = Image.open(f).convert("RGB")
                emb = self._embed(img)
                self.ref_embeddings.append(emb)
            except Exception as e:
                print(f"  [!] Could not load reference photo {f}: {e}")
        if not self.ref_embeddings:
            sys.exit("No usable reference photos could be loaded.")
        print(f"Loaded {len(self.ref_embeddings)} reference photo(s).")

    def _embed(self, pil_image):
        with self.torch.no_grad():
            tensor = self.preprocess(pil_image).unsqueeze(0)
            feats = self.model.encode_image(tensor)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats[0]

    def score_image_bytes(self, img_bytes):
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        emb = self._embed(img)
        best = max(float(self.torch.cosine_similarity(emb.unsqueeze(0), r.unsqueeze(0))) for r in self.ref_embeddings)
        return best


def download_image(url, timeout=15):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Site modules — each returns a list of dicts: {url, title, text, image_urls}
# ---------------------------------------------------------------------------

def fetch_bazos(country="cz", keywords=None):
    """Bazoš.cz / Bazoš.sk — simple server-rendered HTML, easy to parse."""
    domain = "bazos.cz" if country == "cz" else "bazos.sk"
    base = f"https://www.{domain}"
    results = []
    for kw in (keywords or BROAD_KEYWORDS_CZ_SK):
        try:
            url = f"{base}/search.php?hledat={quote(kw)}&rubriky=www&hlokalita=&humkreis=25&cenaod=&cenado=&order=&kitx=ano"
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for ad in soup.select("div.inzeraty"):
                link_tag = ad.select_one("h2.nadpis a")
                if not link_tag:
                    continue
                href = link_tag.get("href", "")
                if href.startswith("/"):
                    href = base + href
                title = link_tag.get_text(strip=True)
                desc_tag = ad.select_one("div.popis")
                desc = desc_tag.get_text(strip=True) if desc_tag else ""
                img_tag = ad.select_one("img")
                img_urls = []
                if img_tag and img_tag.get("src"):
                    src = img_tag["src"]
                    if src.startswith("//"):
                        src = "https:" + src
                    if "einzeraty/thumb" not in src and "noimage" not in src:
                        img_urls.append(src)
                results.append({"url": href, "title": title, "text": f"{title} {desc}", "image_urls": img_urls})
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            print(f"  [!] Bazoš {country} error for '{kw}': {e}")
    return results


def fetch_sbazar(keywords=None):
    """Sbazar.cz — has a search API-ish endpoint under the hood; fall back to HTML search."""
    results = []
    for kw in (keywords or BROAD_KEYWORDS_CZ_SK):
        try:
            url = f"https://www.sbazar.cz/hledej?q={quote(kw)}"
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for ad in soup.select("a[href*='/inzerat/']"):
                href = ad.get("href", "")
                if href.startswith("/"):
                    href = "https://www.sbazar.cz" + href
                title = ad.get_text(strip=True)
                if not title:
                    continue
                img_tag = ad.select_one("img")
                img_urls = []
                if img_tag and img_tag.get("src"):
                    img_urls.append(img_tag["src"])
                results.append({"url": href, "title": title, "text": title, "image_urls": img_urls})
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            print(f"  [!] Sbazar error for '{kw}': {e}")
    return results


def fetch_olx_pl(keywords=None):
    """OLX.pl (Poland) — server-rendered search results."""
    results = []
    for kw in (keywords or BROAD_KEYWORDS_PL):
        try:
            url = f"https://www.olx.pl/oferty/q-{quote(kw)}/"
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.select("a[href*='/oferta/']"):
                href = card.get("href", "")
                if href.startswith("/"):
                    href = "https://www.olx.pl" + href
                title = card.get_text(strip=True)
                if not title:
                    continue
                img_tag = card.select_one("img")
                img_urls = [img_tag["src"]] if img_tag and img_tag.get("src") else []
                results.append({"url": href, "title": title, "text": title, "image_urls": img_urls})
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            print(f"  [!] OLX.pl error for '{kw}': {e}")
    return results


def fetch_modry_konik(keywords=None):
    """
    Modrý Koník (modrykonik.sk) — Slovak secondhand classifieds (general
    goods, including furniture). Server-rendered HTML search.

    NOTE: built without the ability to load the live site in this sandbox,
    so the CSS selectors below are a best-effort guess at a typical
    classifieds-site layout. If this module returns 0 results, open a
    search results page on modrykonik.sk in your browser, right-click a
    listing -> Inspect, and tell me what the real container/link selectors
    look like so I can correct this function.
    """
    results = []
    base = "https://www.modrykonik.sk"
    for kw in (keywords or BROAD_KEYWORDS_CZ_SK):
        try:
            url = f"{base}/bazar/hladat/?q={quote(kw)}"
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Generic fallback approach: any <a> whose href looks like a
            # listing detail page, paired with the nearest heading text and image.
            candidates = soup.select("a[href*='/bazar/'][href*='-']")
            for a in candidates:
                href = a.get("href", "")
                if href.startswith("/"):
                    href = base + href
                title = a.get_text(strip=True)
                if not title or len(title) < 3:
                    continue
                container = a.find_parent(["article", "div", "li"]) or a
                img_tag = container.select_one("img") if container else None
                img_urls = []
                if img_tag and img_tag.get("src"):
                    src = img_tag["src"]
                    if src.startswith("//"):
                        src = "https:" + src
                    img_urls.append(src)
                results.append({"url": href, "title": title, "text": title, "image_urls": img_urls})
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            print(f"  [!] Modrý Koník error for '{kw}': {e}")
    return results


def fetch_aukro_playwright(country="cz", keywords=None):
    """
    Aukro.cz / Aukro.sk — listings are loaded via JavaScript, so this uses
    Playwright (a real headless browser) instead of plain requests.

    NOTE: built without the ability to load the live site in this sandbox,
    so the selectors are a best-effort guess. If it returns 0 results,
    the debug screenshot/html dump (see debug_dir) will help pin down the
    real selectors to fix.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [!] Playwright not installed — run: pip install playwright && playwright install chromium")
        return []

    domain = "aukro.cz" if country == "cz" else "aukro.sk"
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        for kw in (keywords or BROAD_KEYWORDS_CZ_SK):
            try:
                url = f"https://www.{domain}/vysledky-hledani?q={quote(kw)}"
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(1.5)
                anchors = page.query_selector_all("a[href*='/nabidka/'], a[href*='/ponuka/'], a[href*='/item/']")
                for a in anchors:
                    href = a.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = f"https://www.{domain}" + href
                    title = (a.inner_text() or "").strip()
                    if not title:
                        title_attr = a.get_attribute("title")
                        title = title_attr.strip() if title_attr else ""
                    if not title:
                        continue
                    img_el = a.query_selector("img")
                    img_urls = []
                    if img_el:
                        src = img_el.get_attribute("src") or img_el.get_attribute("data-src")
                        if src:
                            img_urls.append(src)
                    results.append({"url": href, "title": title, "text": title, "image_urls": img_urls})
            except Exception as e:
                print(f"  [!] Aukro {country} error for '{kw}': {e}")
            time.sleep(random.uniform(1.5, 3))
        browser.close()
    return results


def fetch_kleinanzeigen(keywords=None):
    """Kleinanzeigen.de (Germany, formerly eBay Kleinanzeigen)."""
    results = []
    for kw in (keywords or BROAD_KEYWORDS_DE):
        try:
            url = f"https://www.kleinanzeigen.de/s-{quote(kw.replace(' ', '-'))}/k0"
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.select("article.aditem"):
                link_tag = card.select_one("a.ellipsis")
                if not link_tag:
                    continue
                href = link_tag.get("href", "")
                if href.startswith("/"):
                    href = "https://www.kleinanzeigen.de" + href
                title = link_tag.get_text(strip=True)
                img_tag = card.select_one("img")
                img_urls = [img_tag["src"]] if img_tag and img_tag.get("src") else []
                results.append({"url": href, "title": title, "text": title, "image_urls": img_urls})
            time.sleep(random.uniform(1, 2))
        except Exception as e:
            print(f"  [!] Kleinanzeigen error for '{kw}': {e}")
    return results


def fetch_facebook_marketplace(session_path=None, keywords=None, location="czech-republic"):
    """
    BEST-EFFORT ONLY. Facebook actively fights scraping, including browser
    automation — this can break at any time, get your session logged out,
    or get flagged. Use a throwaway/secondary Facebook account if possible,
    never your main one, and don't run this too frequently.

    Requires a saved login session created once via save_facebook_session.py
    (see README). session_path should point at that script's output
    (facebook_session.json).
    """
    if not session_path or not Path(session_path).exists():
        print("  [i] Skipping Facebook Marketplace (no saved session — run save_facebook_session.py first).")
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [!] Playwright not installed — run: pip install playwright && playwright install chromium")
        return []

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=session_path, user_agent=HEADERS["User-Agent"])
        page = context.new_page()
        for kw in (keywords or BROAD_KEYWORDS_CZ_SK):
            try:
                url = f"https://www.facebook.com/marketplace/{location}/search?query={quote(kw)}"
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(2)
                anchors = page.query_selector_all("a[href*='/marketplace/item/']")
                for a in anchors:
                    href = a.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = "https://www.facebook.com" + href
                    href = href.split("?")[0]
                    title = (a.inner_text() or "").strip()
                    if not title:
                        continue
                    img_el = a.query_selector("img")
                    img_urls = []
                    if img_el:
                        src = img_el.get_attribute("src")
                        if src:
                            img_urls.append(src)
                    results.append({"url": href, "title": title, "text": title, "image_urls": img_urls})
            except Exception as e:
                print(f"  [!] Facebook Marketplace error for '{kw}': {e}")
            time.sleep(random.uniform(3, 5))
        context.close()
        browser.close()
    return results


SITE_MODULES = {
    "bazos_cz": lambda: fetch_bazos("cz"),
    "bazos_sk": lambda: fetch_bazos("sk"),
    "sbazar": fetch_sbazar,
    "modry_konik": fetch_modry_konik,
    "aukro_cz": lambda: fetch_aukro_playwright("cz"),
    "aukro_sk": lambda: fetch_aukro_playwright("sk"),
    "olx_pl": fetch_olx_pl,
    "kleinanzeigen_de": fetch_kleinanzeigen,
}


def run_once(config):
    seen = load_seen()
    found = load_found()
    matcher = None
    # Two tiers: "notify" = confident enough to push a notification;
    # "list" = looser — shown in the dashboard for you to eyeball, but no push.
    # This is what lets you browse "similar chairs" without getting spammed.
    notify_threshold = config.get("similarity_threshold_notify", config.get("similarity_threshold", 0.30))
    list_threshold = config.get("similarity_threshold_list", 0.20)
    topic = config.get("ntfy_topic")
    new_matches = 0
    new_listed = 0
    total_new_listings = 0

    enabled_sites = config.get("enabled_sites", list(SITE_MODULES.keys()))

    all_listings = []
    for site_name in enabled_sites:
        fn = SITE_MODULES.get(site_name)
        if not fn:
            print(f"  [!] Unknown site '{site_name}' in config, skipping.")
            continue
        print(f"Fetching {site_name}...")
        try:
            listings = fn()
        except Exception as e:
            print(f"  [!] {site_name} failed entirely: {e}")
            listings = []
        print(f"  -> {len(listings)} listings seen (before de-dup)")
        for l in listings:
            l["site"] = site_name
        all_listings.extend(listings)

    if config.get("facebook_session_path"):
        fb_listings = fetch_facebook_marketplace(
            config["facebook_session_path"],
            location=config.get("facebook_location", "czech-republic"),
        )
        for l in fb_listings:
            l["site"] = "facebook_marketplace"
        all_listings.extend(fb_listings)

    new_listings = [l for l in all_listings if listing_id(l["url"]) not in seen]
    total_new_listings = len(new_listings)
    print(f"\n{total_new_listings} new listing(s) to check across all sites.\n")

    if new_listings and config.get("use_image_matching", True):
        matcher = ImageMatcher(REF_DIR)

    for l in new_listings:
        lid = listing_id(l["url"])
        seen.add(lid)
        keyword_hit = text_has_strong_keyword(l["text"])
        weak_name_hit = text_has_weak_name_keyword(l["text"])  # logged only, doesn't auto-qualify a tier
        best_score = None
        if matcher and l.get("image_urls"):
            try:
                img_bytes = download_image(l["image_urls"][0])
                best_score = matcher.score_image_bytes(img_bytes)
            except Exception as e:
                print(f"  [!] Could not score image for {l['url']}: {e}")

        if keyword_hit:
            tier = "keyword"
        elif best_score is not None and best_score >= notify_threshold:
            tier = "strong"
        elif best_score is not None and best_score >= list_threshold:
            tier = "similar"
        else:
            tier = None

        score_str = f"{best_score:.3f}" if best_score is not None else "n/a"
        weak_note = " | weak_name_hit=True (not auto-trusted)" if weak_name_hit and not keyword_hit else ""
        print(f"  [{l['site']}] {l['title'][:60]!r} | keyword_hit={keyword_hit} | score={score_str}{weak_note} -> {tier or 'skip'}")

        if tier:
            found.append({
                "id": lid,
                "url": l["url"],
                "title": l["title"],
                "site": l["site"],
                "image_url": (l.get("image_urls") or [None])[0],
                "score": best_score,
                "tier": tier,
                "found_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            new_listed += 1
            if tier in ("keyword", "strong") and topic:
                new_matches += 1
                reason = "keyword match" if keyword_hit else f"visual similarity {score_str}"
                send_notification(
                    topic,
                    title="Possible chair match!",
                    message=f"{l['title']}\n({reason})\n{l['url']}",
                    url=l["url"],
                )

    found.sort(key=lambda x: x["found_at"], reverse=True)
    save_found(found)
    save_seen(seen)
    print(f"\nDone. {new_matches} notification(s) sent, {new_listed} listing(s) added to dashboard, "
          f"out of {total_new_listings} new listings checked.")
    return {"new_matches": new_matches, "new_listed": new_listed, "total_new_listings": total_new_listings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (default behavior).")
    parser.add_argument("--loop-minutes", type=int, default=0,
                         help="If set, keep running forever, sleeping this many minutes between passes "
                              "(alternative to using cron/Task Scheduler).")
    args = parser.parse_args()

    config = load_config()

    if args.loop_minutes:
        while True:
            run_once(config)
            print(f"\nSleeping {args.loop_minutes} minutes...\n")
            time.sleep(args.loop_minutes * 60)
    else:
        run_once(config)


if __name__ == "__main__":
    main()
