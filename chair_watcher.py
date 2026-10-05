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
# what the chair is and just described it by material/appearance (e.g. just
# "chrome chair" or "metal chair"). Deliberately 2-word material+furniture
# phrases rather than a single bare word like "židle"/"stolička" alone:
# a bare word matches literally every chair ever listed on these sites
# (plastic patio chairs, office chairs, kids' chairs...), which balloons
# scan time for little benefit — the pair with material narrows the raw
# candidate pool while still catching an unbranded listing. Precision from
# here on is the job of the image-similarity score (see config.json), not
# the keyword list — that's deliberate: casting a slightly wider net here
# is low-risk as long as the threshold calibration downstream holds.
BROAD_KEYWORDS_CZ_SK = [
    "trubková židle plátno", "trubkové kreslo", "chromová židle režná",
    "stolička kodreta", "kreslo chróm plátno", "retro trubková stolička",
    "stolička chrom", "stoličky chrom", "stolička kov", "stoličky kov",
    "židle chrom", "židle kov", "kovová židle", "kovová stolička",
    "jídelní židle kov", "jedálenská stolička kov",
    "retro kovová židle", "vintage stolička kov",
] + NAME_KEYWORDS
BROAD_KEYWORDS_DE = [
    "Kodreta stuhl", "Stahlrohr stuhl Leinen Vintage",
    "Chrom Stuhl Vintage", "Metallstuhl Vintage", "verchromter Stuhl",
    "Stahlrohrstuhl retro",
] + NAME_KEYWORDS
BROAD_KEYWORDS_PL = [
    "krzeslo chromowane vintage plotno", "kodreta krzeslo",
    "krzesło chrom", "krzesło metalowe", "krzesła chrom vintage",
    "metalowe krzesło retro",
] + NAME_KEYWORDS


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


_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                    "fbclid", "gclid", "rut", "ref", "igshid"}


def normalize_url(url):
    """Collapses trivial variations of the same URL (scheme/host case,
    trailing slash, tracking params, query-param order) so the same ad
    hashes identically every time instead of looking like a new listing."""
    try:
        from urllib.parse import urlparse, urlencode, parse_qsl
        p = urlparse(url)
        scheme = p.scheme.lower() or "https"
        netloc = p.netloc.lower()
        path = p.path.rstrip("/") or "/"
        q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in _TRACKING_PARAMS]
        q.sort()
        query = urlencode(q)
        return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")
    except Exception:
        return url


def listing_id(url):
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


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


# The exact chair: model number or its designer. Highest tier, always notified.
EXACT_RE = re.compile(r"\bt[\s-]?2306\b|hre[sš][cč][aá]k")
# Other Kodreta model codes seen on real listings (T-2403 armchair, T2407...).
MODEL_CODE_RE = re.compile(r"\bt[\s-]?2[34]\d\d\b")


def name_signal(title, text=""):
    """'exact' (the T2306 itself), 'brand' (Kodreta/Chlebo/another model
    code), or None. The model number may be anywhere in the ad; the
    designer's surname only counts in the TITLE — in descriptions it was
    usually just the seller's own name (see the fire-extinguisher ads)."""
    ft, fx = fold(title), fold(text)
    if re.search(r"\bt[\s-]?2306\b", ft + " " + fx) or re.search(r"hrescak", ft):
        return "exact"
    if text_has_strong_keyword(ft + " " + fx) or MODEL_CODE_RE.search(ft + " " + fx):
        return "brand"
    return None


def text_has_strong_keyword(text):
    t = text.lower()
    return any(k in t for k in STRONG_KEYWORDS)


def text_has_weak_name_keyword(text):
    t = text.lower()
    return any(k in t for k in WEAK_NAME_KEYWORDS)


# ---------------------------------------------------------------------------
# Text filtering
# ---------------------------------------------------------------------------
# Everything below works on "folded" text: lowercase with accents stripped,
# so one stem catches every Czech/Slovak inflection AND sellers who type
# without diacritics. Confirmed necessary from real scan data: listings like
# "Kancelarska stolicka", "Detska jedalenska stolička" and "kancelářských
# židlí" all slipped past the old exact-word list.

import unicodedata


def fold(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _has_any(folded, stems):
    return any(s in folded for s in stems)


# A listing must actually be a CHAIR. Folded stems: židle/židlí/židlička,
# stolička/stoličky/stoliček/stoličiek, křeslo/kreslo/kreslá, krzesło, Stuhl.
# ("stolik"/"stolek" = small table, deliberately NOT here.)
CHAIR_STEMS = ["zidl", "stolic", "kresl", "kresil", "krzes", "stuhl", "sessel", "chair"]

# Other furniture from the collection (tables, sofas, loungers, coat racks...).
FURNITURE_STEMS = ["stol", "stul", "pohovk", "sedack", "gauc", "lehatk", "lavic", "taburet",
                   "vesiak", "vesak", "nabyt", "kreslo", "set", "souprav", "suprav", "tisch",
                   "table", "sofa", "lamp", "regal", "polic"]
# Exclusions that a Kodreta/Chlebo name OVERRIDES (it's part of the collection).
COLLECTION_OVERRIDABLE = {"table, not a chair", "'pohovk'", "'gauc'", "'lehatk'", "'houpac'",
                          "'hojdac'", "'skrin'", "'komoda'", "'barov'", "'otocn'", "wooden"}

# "Wanted" ads — someone BUYING, not selling. Real scan data: your own
# "Kúpim stoličku T2306" / "Koupím židli T2306" ads were the top 8 cards.
WANTED_STEMS = ["kupim", "koupim", "hladam", "hledam", "shanim", "zhanam",
                "poptavam", "suche ", "kaufe ", "szukam", "kupie "]

# Hard category mismatches. Your chair = chrome tubular frame + fabric
# sling seat and back. Every stem here was a real false positive.
EXCLUDE_STEMS = [
    # children
    "detsk", "kinder", "dzieci", "peg perego", "cybex", "stokke", "chicco",
    "vysoka stolick", "jidelni zidlick", "pre diet", "pro diet", "pre deti", "pro deti",
    # office / swivel / wheels
    "kancelar", "buro", "biurow", "otocn", "kolieck", "koleck", "herni ",
    # bar & piano stools, fishing/camping
    "barov", "barhock", "klavir", "rybar", "kemping", "camping",
    # garden / outdoor
    "zahrad", "ogrod", "garten", "teras", "venkovn", "vonkajs", "balkon",
    "ratan", "pozink",
    # medical aids
    "zdravot", "vanov", "toaletn", "sprchov", "invalid",
    # plastic seats (fabric AND leather versions are both wanted)
    "plastov", "plast ",
    # bentwood classics
    "thonet",
    # other furniture
    "lehatk", "houpac", "hojdac", "pohovk", "gauc", "postel", "komoda", "skrin",
]
# Wood words exclude ONLY when nothing metal is mentioned — a chrome chair
# with wooden armrests is still worth seeing.
WOOD_STEMS = ["dreven", "drevo", "dreva", "ohybane", "masiv", "teak", "bukov", "dubov", "rustik"]
METAL_STEMS = ["chrom", "trubk", "trubic", "kov", "ocel", "metal", "stahl", "nerez"]
# Table-only listings (no chair word) — e.g. the Kodreta coffee tables.
TABLE_STEMS = ["stul", "stol ", "stolek", "stolik", "tisch", "table", "jidelni set", "jedalensky set"]

# Bazoš files children's goods under its own subdomain regardless of wording.
EXCLUDE_URL_SUBSTRINGS = ["deti.bazos.", "auto.bazos.", "motorky.bazos.", "stroje.bazos.",
                          "sport.bazos.", "obleceni.bazos.", "oblecenie.bazos.", "pc.bazos.",
                          "mobil.bazos.", "hudba.bazos.", "zvirata.bazos.", "zvierata.bazos."]

_TON_RE = re.compile(r"\bton\b")
# Word-START match only: "kov" must not match inside "bukového"/"teakového".
_METAL_RE = re.compile(r"\b(" + "|".join(METAL_STEMS) + ")")


def is_wanted_ad(title):
    f = " " + fold(title) + " "
    return any((" " + s) in f for s in WANTED_STEMS)


def has_chair_word(text):
    return _has_any(fold(text), CHAIR_STEMS)


def exclusion_reason(title, url=""):
    """Returns why a listing is excluded, or None. Judged on the TITLE (what
    the seller says the item is) — descriptions often mention unrelated
    things ("also selling a table..."), which made description-based
    exclusion too trigger-happy."""
    f = fold(title) + " "
    u = (url or "").lower()
    if is_wanted_ad(title):
        return "wanted ad"
    for s in EXCLUDE_URL_SUBSTRINGS:
        if s in u:
            return f"category {s.split('.')[0]}"
    for s in EXCLUDE_STEMS:
        if s in f:
            return f"'{s.strip()}'"
    if _TON_RE.search(f):
        return "'TON' bentwood"
    if _has_any(f, WOOD_STEMS) and not _METAL_RE.search(f):
        return "wooden"
    if not has_chair_word(f) and _has_any(f, TABLE_STEMS):
        return "table, not a chair"
    return None


def is_excluded(text, url=""):
    return exclusion_reason(text, url) is not None


def clean_title(raw):
    """Aukro link text arrives as '1\\nReal title\\nŽiadne prihodenie\\n10,43 €'
    — keep only the real title line."""
    noise = ["prihoden", "prihoz", "kup ted", "kup teraz", "bezna cena", "rozbalen",
             "pouzit", "zachovan", "top seller", "doprava zdarma"]
    lines = [l.strip() for l in (raw or "").splitlines() if l.strip()]
    good = []
    for l in lines:
        fl = fold(l)
        if re.fullmatch(r"[\d\s.,%+-]+", l):
            continue
        if "€" in l or "kc" in fl.split() or re.search(r"\d\s*kc\b", fl):
            continue
        if any(n in fl for n in noise) and len(l) < 40:
            continue
        good.append(l)
    return (good[0] if good else (lines[0] if lines else raw or "")).strip()


def title_key(title):
    """Identity for cross-site / repost de-duplication: same ad posted on
    bazos.cz and bazos.sk, or re-posted under a new ID, collapses to one."""
    return re.sub(r"[^a-z0-9]", "", fold(clean_title(title)))


# ---------------------------------------------------------------------------
# CLIP image matching: similarity to your photos + "what is this a photo of"
# ---------------------------------------------------------------------------
# Real scan data showed photo-similarity alone CAN'T separate junk: genuine
# chrome chairs scored 0.55-0.70, but kids' highchairs, wooden TON chairs and
# office chairs also scored 0.55-0.60, and Aukro car parts/clothes 0.50-0.53.
# No threshold works when the ranges overlap. So each photo is now also
# CLASSIFIED with CLIP zero-shot: it's compared against text descriptions of
# ~30 kinds of object and the closest kind wins. A photo of a car part is
# far closer to "a photo of a car part" than to "a chrome tubular chair with
# a fabric seat", whatever its raw similarity number to your reference
# photos. This is relative (which description wins), so it doesn't depend
# on hand-tuned absolute thresholds the way similarity does.

CATEGORY_PROMPTS = {
    "target": [
        "a photo of a chair with a chrome tubular steel frame and a fabric seat and backrest",
        "a photo of a vintage chrome tube chair with a canvas sling seat",
        "a photo of a metal tube chair upholstered in woven fabric",
        "a photo of a chair with a chrome tubular steel frame and a leather seat and backrest",
        "a photo of a vintage chrome tube chair with a leather sling seat",
        # The frame is always the same chrome; the upholstery colour varies.
        "a photo of a chrome tube chair with a red, blue, green, brown or black fabric seat",
        "a photo of a chrome tube chair with a black, brown or white leather seat",
    ],
    "chrome_other": [
        "a photo of a chrome cantilever chair",
        "a photo of a chrome tubular armchair",
        "a photo of a metal chair with a padded vinyl seat",
    ],
    "wooden_chair": ["a photo of a wooden chair", "a photo of a bentwood chair"],
    "plastic_chair": ["a photo of a plastic chair"],
    "office_chair": ["a photo of an office chair on wheels"],
    "stool": ["a photo of a bar stool", "a photo of a stool"],
    "kids": ["a photo of a baby high chair"],
    "armchair_sofa": ["a photo of an upholstered armchair", "a photo of a sofa"],
    "outdoor": ["a photo of rattan garden furniture", "a photo of a folding camping chair"],
    "table": ["a photo of a table", "a photo of a dining table with chairs"],
    "other": [
        "a photo of a car part", "a photo of a car", "a photo of a motorcycle",
        "a photo of clothing", "a photo of a tool", "a photo of an electronic device",
        "a photo of wooden planks", "a photo of a household object",
        "a photo of a fire extinguisher", "a photo of a lamp", "a photo of a bench",
    ],
}
GOOD_CATEGORIES = ("target", "chrome_other")


def _grayscale(pil_image):
    return pil_image.convert("L").convert("RGB")


class ImageMatcher:
    """Loads CLIP once. For each listing photo returns similarity to your
    reference photos AND zero-shot category probabilities."""

    def __init__(self, ref_dir):
        import open_clip
        import torch
        self.torch = torch
        print("Loading CLIP model (first run downloads ~350MB, then it's cached)...")
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        self.model.eval()
        tokenizer = open_clip.get_tokenizer("ViT-B-32")

        # One class per chair category: its descriptions are AVERAGED into a
        # single profile (standard CLIP "prompt ensembling"). Summing them
        # instead would favour whichever category has the most descriptions
        # — e.g. "target" with its colour/leather variants. The "other"
        # descriptions stay separate classes: a car part and a coat have
        # nothing in common, so averaging them would describe neither.
        self.prompt_cats = []
        class_embs = []
        with torch.no_grad():
            for cat, ps in CATEGORY_PROMPTS.items():
                t = self.model.encode_text(tokenizer(ps))
                t = t / t.norm(dim=-1, keepdim=True)
                if cat == "other":
                    class_embs.extend(t)
                    self.prompt_cats.extend([cat] * len(ps))
                else:
                    m = t.mean(dim=0)
                    class_embs.append(m / m.norm())
                    self.prompt_cats.append(cat)
            self.text_emb = torch.stack(class_embs)

        self.ref_embeddings = []
        self.ref_gray = []
        from PIL import Image
        try:  # iPhone photos are HEIC by default
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            print("  [i] pillow-heif not installed — .heic reference photos will be skipped")
        for f in sorted(Path(ref_dir).glob("*")):
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"):
                continue
            try:
                img = Image.open(f).convert("RGB")
                self.ref_embeddings.append(self._embed(img))
                self.ref_gray.append(self._embed(_grayscale(img)))
            except Exception as e:
                print(f"  [!] Could not load reference photo {f}: {e}")
        if not self.ref_embeddings:
            sys.exit("No usable reference photos could be loaded.")
        print(f"Loaded {len(self.ref_embeddings)} reference photo(s).")
        # Sanity check printed to the Actions log: your own photos should
        # classify as "target". If they don't, the prompts need adjusting.
        for i, r in enumerate(self.ref_embeddings):
            res = self._classify(r)
            print(f"  reference photo {i + 1}: top category={res['category']} "
                  f"p_target={res['p_target']:.2f} p_chrome={res['p_chrome']:.2f}")

    def _embed(self, pil_image):
        with self.torch.no_grad():
            feats = self.model.encode_image(self.preprocess(pil_image).unsqueeze(0))
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats[0]

    def _classify(self, emb):
        logits = 100.0 * (emb @ self.text_emb.T)
        probs = logits.softmax(dim=-1).tolist()
        by_cat = {}
        for cat, p in zip(self.prompt_cats, probs):
            by_cat[cat] = by_cat.get(cat, 0.0) + p
        top = max(by_cat, key=by_cat.get)
        return {
            "category": top,
            "p_target": by_cat.get("target", 0.0),
            "p_chrome": by_cat.get("target", 0.0) + by_cat.get("chrome_other", 0.0),
        }

    def analyze_image_bytes(self, img_bytes):
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        emb = self._embed(img)
        sim_color = max(float(emb @ r) for r in self.ref_embeddings)
        # Colour-blind comparison: the chrome frame is always the same but
        # the seat fabric/leather comes in many colours. In black-and-white
        # a red or black seat on the same frame looks like your beige one,
        # so the frame's shape decides. Take whichever comparison is higher.
        emb_gray = self._embed(_grayscale(img))
        sim_gray = max(float(emb_gray @ g) for g in self.ref_gray)
        res = self._classify(emb)
        res["sim"] = max(sim_color, sim_gray)
        res["sim_color"], res["sim_gray"] = sim_color, sim_gray
        return res

    def score_image_bytes(self, img_bytes):  # kept for compatibility
        return self.analyze_image_bytes(img_bytes)["sim"]


_MATCHER = {"obj": None, "failed": False}


def get_matcher():
    """Loads CLIP lazily, once per run, only if something needs scoring."""
    if _MATCHER["obj"] is None and not _MATCHER["failed"]:
        try:
            _MATCHER["obj"] = ImageMatcher(REF_DIR)
        except Exception as e:
            print(f"  [!] Image matcher unavailable: {e}")
            _MATCHER["failed"] = True
    return _MATCHER["obj"]


def match_score(sim, p_target):
    """One 0-1 number for ranking: 60% photo similarity (rescaled from its
    real-world 0.45-0.80 range), 40% how confidently CLIP says 'chrome tube
    chair with fabric seat'."""
    if sim is None:
        return None
    s = min(1.0, max(0.0, (sim - 0.45) / 0.35))
    return round(0.6 * s + 0.4 * (p_target or 0.0), 3)


TIER_RANK = {"exact": 4, "keyword": 3, "strong": 2, "similar": 1, "collection": 0}
NOTIFY_TIERS = ("exact", "keyword", "strong")


def evaluate(title, text, url, analysis, config):
    """Single source of truth for whether a listing is shown, and in which
    tier. Used for new listings AND for re-checking stored ones, so old
    entries obey the current rules too. Returns (tier or None, reason).

    Tiers, best first:
      exact      - names the T2306 (or Hreščák in the title) and is a chair
      keyword    - a Kodreta/Chlebo chair
      strong     - photo: chrome tube chair, very similar to your photos
      similar    - photo: chrome tube chair, somewhat similar
      collection - other Kodreta/Chlebo furniture (tables, sofas...), never notified
    """
    reason = exclusion_reason(title, url)
    signal = name_signal(title, text)
    chair = has_chair_word(title)
    furniture = chair or _has_any(fold(title), FURNITURE_STEMS)
    photo_is_junk = analysis is not None and analysis["category"] == "other"

    if reason:
        if signal and furniture and not photo_is_junk and reason in COLLECTION_OVERRIDABLE:
            return "collection", f"collection piece ({reason})"
        return None, f"excluded: {reason}"

    if signal and not photo_is_junk:
        if signal == "exact" and chair:
            return "exact", "T2306 named"
        if chair:
            return "keyword", "Kodreta/Chlebo chair"
        if furniture:
            return "collection", "Kodreta/Chlebo furniture"

    if analysis is None:
        return None, "no photo and no name match"

    sim, p_chrome, p_target = analysis["sim"], analysis["p_chrome"], analysis["p_target"]
    looks_right = analysis["category"] in GOOD_CATEGORIES or p_chrome >= config.get("min_chrome_prob", 0.30)
    if not looks_right:
        return None, f"photo looks like: {analysis['category']}"
    if sim >= config.get("similarity_threshold_notify", 0.55) and p_target >= config.get("min_target_prob_notify", 0.25):
        return "strong", "photo match"
    if sim >= config.get("similarity_threshold_list", 0.50):
        return "similar", "photo similar"
    return None, f"similarity {sim:.2f} too low"


def download_image(url, timeout=15):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Site modules — each returns a list of dicts: {url, title, text, image_urls}
# ---------------------------------------------------------------------------

# General web search queries — this is what covers "search Google/other
# engines too" and doubles as a safety net for marketplaces whose own
# scraper is unreliable or disallows automated access (Sbazar's robots.txt
# explicitly disallows scraping, for instance — this reaches it indirectly
# via search engine indexing instead, which is a different, acceptable
# thing from scraping the site directly). Only the first results page is
# read per query — deliberately shallow and fast, since a ranked search
# engine already puts the best matches first; there's little value digging
# past page 1, and it's what keeps this step quick.
WEB_SEARCH_QUERIES = [
    # The exact chair, anywhere
    "T2306 židle", "T2306 stolička", "\"T 2306\" kodreta", "Hreščák kodreta židle",
    # CZ/SK marketplaces (site: + chair word = individual ads, not overviews)
    "site:bazos.cz kodreta židle", "site:bazos.sk kodreta stolička",
    "site:sbazar.cz kodreta", "site:sbazar.cz chromová židle retro",
    "site:aukro.cz kodreta", "site:aukro.sk kodreta",
    "site:bazar.sk kodreta", "site:vinted.cz kodreta", "site:vinted.sk kodreta",
    "site:onebid.cz kodreta",
    # Czech/Slovak vintage-design dealers that carry Kodreta pieces
    "site:pelmeldesign.cz Chlebo", "site:eterle.cz kodreta", "site:designrobot.cz Chlebo",
    "site:studiolavish.com kodreta",
    # International design marketplaces (Kodreta/Chlebo pieces confirmed on each)
    "site:pamono.com kodreta", "site:1stdibs.com kodreta", "site:vinterior.co Chlebo",
    "site:whoppah.com kodreta", "site:etsy.com kodreta chair", "site:ebay.com kodreta",
    "site:design-market.eu kodreta", "site:catawiki.com kodreta",
    # Neighbouring-country classifieds
    "site:willhaben.at kodreta", "site:allegro.pl kodreta", "site:olx.pl kodreta",
    "site:kleinanzeigen.de kodreta", "site:jofogas.hu kodreta",
]

# Only INDIVIDUAL listing pages pass. Real scan data showed the old
# "trust the whole domain" rule let through Bazoš category pages
# ("Kodreta bazár - Nábytok | Bazoš.sk"), Sbazar search pages and a dozen
# company-directory pages (dnb.com, zoznam.sk, cylex.sk...).
LISTING_URL_PATTERNS = [re.compile(p) for p in [
    r"bazos\.(cz|sk)/inzerat/\d+",
    r"sbazar\.cz/(inzerat/\d+|[^/]+/detail/\d+)",
    r"aukro\.(cz|sk)/[^/?#]+-\d{8,}",
    r"bazar\.sk/.*\d{5,}",
    r"vinted\.[a-z.]+/items/\d+",
    r"onebid\.cz/.+/\d+",
    r"pelmeldesign\.cz/(en/)?obchod/[^/]+",
    r"eterle\.cz/eshop/produkt/",
    r"designrobot\.cz/.+/produkt/",
    r"studiolavish\.com/.*products/",
    r"pamono\.[a-z.]+/[a-z0-9-]+$",
    r"1stdibs\.com/.+/id-[a-z]_\d+",
    r"vinterior\.co/.+sku\d+",
    r"whoppah\.com/products/",
    r"etsy\.com/[a-z/-]*listing/\d+",
    r"ebay\.[a-z.]+/itm/",
    r"design-market\.[a-z]+/\d+-",
    r"catawiki\.com/.*/l/\d+",
    r"willhaben\.at/iad/.+\d{6,}",
    r"allegro\.(pl|cz)/(oferta|produkt)/",
    r"olx\.pl/d/oferta/",
    r"kleinanzeigen\.de/s-anzeige/",
    r"jofogas\.hu/.+\.htm",
    r"facebook\.com/marketplace/item/\d+",
]]


def is_listing_url(url):
    u = (url or "").lower()
    return any(p.search(u) for p in LISTING_URL_PATTERNS)


def site_label(url):
    """Short source name for a web-search hit, e.g. 'pamono', 'sbazar'."""
    host = (url.split("//")[-1].split("/")[0]).lower()
    parts = [p for p in host.split(".") if p not in ("www", "en", "m", "cz", "sk", "com", "co", "pl", "de", "at", "hu", "eu", "uk")]
    return parts[-1] if parts else host


# Sites whose robots.txt disallows automated access: we link to them from
# search results but never fetch their pages ourselves.
NO_FETCH_DOMAINS = ("sbazar.cz", "facebook.com")


def fetch_preview_image(url):
    """The listing's own preview photo (og:image), so web-search hits can be
    photo-checked like everything else. One request per NEW hit only."""
    if any(d in url for d in NO_FETCH_DOMAINS):
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
        img = tag.get("content") if tag else None
        if img and img.startswith("//"):
            img = "https:" + img
        return img if img and img.startswith("http") else None
    except Exception:
        return None


def _looks_like_a_listing(url, text):  # kept for compatibility
    return is_listing_url(url)


def _unwrap_duckduckgo_redirect(href):
    """DuckDuckGo's HTML endpoint wraps result links as
    //duckduckgo.com/l/?uddg=<url-encoded-real-target>&rut=... — unwrap to
    the real destination. Matters for two things: clicking through should
    go straight to the actual listing, and the 'rut' tracking token in the
    wrapper changes per-request, which was silently defeating de-duplication
    (the same real listing hashing differently every time it was re-found)."""
    from urllib.parse import urlparse, parse_qs, unquote
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        target = qs.get("uddg", [None])[0]
        if target:
            return unquote(target)
    return href


def fetch_web_search(queries=None):
    """
    DuckDuckGo's HTML endpoint (no API key, no login) — used instead of a
    real Google API since that needs a paid/registered key. Text-only: no
    image available from a search snippet, so these candidates are judged
    purely on keyword/exclusion matching, not the CLIP score. First page
    of results only (DuckDuckGo already ranks best matches first).
    """
    results = []
    if queries is None:
        # The 4 exact-chair queries every run; the other ~30 rotate in
        # thirds by the hour, so each is still searched every 3 hours
        # without hammering the search engine (or slowing each run).
        always, rest = WEB_SEARCH_QUERIES[:4], WEB_SEARCH_QUERIES[4:]
        slot = int(time.time() // 3600) % 3
        queries = always + rest[slot::3]
    for q in queries:
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": q},
                headers=HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for result in soup.select("div.result")[:10]:  # first page, top 10 per query
                link_tag = result.select_one("a.result__a")
                if not link_tag:
                    continue
                raw_href = link_tag.get("href", "")
                href = _unwrap_duckduckgo_redirect(raw_href)
                title = link_tag.get_text(strip=True)
                snippet_tag = result.select_one("a.result__snippet, div.result__snippet")
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                if not href or not title:
                    continue
                if not is_listing_url(href):
                    continue  # company pages, category/search pages, directories
                # Search engines append " - Bazoš.cz", " | Bazar" etc. to titles
                title = re.split(r"\s+[|\-–]\s+(Bazoš|Bazar|Sbazar|Aukro|Vinted)", title)[0].strip()
                results.append({
                    "url": href,
                    "title": title,
                    "text": f"{title} {snippet}",
                    "image_urls": [],  # filled in later from the page's preview image
                    "source": site_label(href),
                })
            time.sleep(random.uniform(1.0, 2.0))
        except Exception as e:
            print(f"  [!] Web search error for '{q}': {e}")
    return results


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


# A smaller, curated subset for the slow Playwright-driven sites — real
# browser page loads (~3-5s each) make the full broad keyword list too
# slow here. The full list is still used for fast requests-based sites.
AUKRO_KEYWORDS = [
    "kodreta", "t2306", "jaroslav hreščák",
    "trubkové křeslo", "retro trubková stolička", "chromová stolička",
]


def fetch_aukro_playwright(country="cz", keywords=None):
    """
    Aukro.cz / Aukro.sk — listings are loaded via JavaScript, so this uses
    Playwright (a real headless browser) instead of plain requests.

    Real Aukro item URLs look like aukro.cz/{slug}-{long numeric id}
    (confirmed via search engine results, e.g.
    aukro.cz/kresla-kodreta-myjava-design-viliam-2-kus-6968056434) — matched
    here by a trailing-digits pattern rather than a guessed path segment,
    since that's more robust to not knowing the exact search-results page
    markup. If this still returns 0 results, the general web-search module
    (fetch_web_search, using site:aukro.cz) acts as a fallback net for this
    site regardless.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [!] Playwright not installed — run: pip install playwright && playwright install chromium")
        return []

    domain = "aukro.cz" if country == "cz" else "aukro.sk"
    item_url_pattern = re.compile(r"-\d{6,}(?:[/?#]|$)")
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        for kw in (keywords or AUKRO_KEYWORDS):
            try:
                url = f"https://www.{domain}/vysledky-hledani?q={quote(kw)}"
                page.goto(url, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(1.5)
                anchors = [a for a in page.query_selector_all("a[href]")
                           if item_url_pattern.search(a.get_attribute("href") or "")]
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
                    title = clean_title(title)
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


# Vinted's own web app is backed by a public JSON search API
# (/api/v2/catalog/items) that needs only the anonymous session cookie the
# homepage sets — no login. Built from documented third-party usage of this
# API; it couldn't be tested live from here, so if the Actions log shows
# "Vinted ... error" every run, the web-search module's site:vinted.cz /
# site:vinted.sk queries still cover Vinted as a fallback.
VINTED_KEYWORDS = {
    "cz": ["kodreta", "t2306", "chromová židle", "trubková židle", "kovová židle retro", "retro židle chrom"],
    "sk": ["kodreta", "t2306", "chrómová stolička", "kovová stolička retro", "retro stolička chróm", "trubková stolička"],
}


def fetch_vinted(country="cz", keywords=None):
    base = f"https://www.vinted.{country}"
    session = requests.Session()
    session.headers.update({**HEADERS, "Accept": "application/json, text/plain, */*"})
    results = []
    try:
        session.get(base + "/", timeout=20)  # sets the anonymous access cookie
    except Exception as e:
        print(f"  [!] Vinted {country}: could not open homepage: {e}")
        return results
    for kw in (keywords or VINTED_KEYWORDS.get(country, [])):
        try:
            resp = session.get(
                f"{base}/api/v2/catalog/items",
                params={"search_text": kw, "per_page": 48, "page": 1, "order": "newest_first"},
                timeout=20,
            )
            resp.raise_for_status()
            for it in resp.json().get("items", []):
                item_id = it.get("id")
                url = it.get("url") or (f"{base}/items/{item_id}" if item_id else None)
                title = (it.get("title") or "").strip()
                if not url or not title:
                    continue
                if url.startswith("/"):
                    url = base + url
                photo = it.get("photo") or {}
                img = photo.get("url") or (photo.get("thumbnails") or [{}])[0].get("url")
                results.append({
                    "url": url,
                    "title": title,
                    "text": f"{title} {it.get('description') or ''}",
                    "image_urls": [img] if img else [],
                })
            time.sleep(random.uniform(1.0, 2.0))
        except Exception as e:
            print(f"  [!] Vinted {country} error for '{kw}': {e}")
    return results


# sbazar and modry_konik are deliberately NOT registered here:
# - Sbazar's robots.txt explicitly disallows automated access (confirmed
#   directly against the live site) — scraping it would ignore that.
#   fetch_web_search's "site:sbazar.cz" query reaches it indirectly via
#   search engine indexing instead, which is a different, acceptable thing.
# - Modrý Koník turned out to be primarily a CHILDREN'S resale marketplace
#   ("kvalitný detský bazár") — confirmed against the live site. Its
#   furniture section exists but is a poor fit for this search and was a
#   real source of the kids'-chair false positives. Dropped rather than
#   patched. The functions remain defined above in case either is useful
#   again later, just not wired in by default.
SITE_MODULES = {
    "bazos_cz": lambda: fetch_bazos("cz"),
    "bazos_sk": lambda: fetch_bazos("sk"),
    "aukro_cz": lambda: fetch_aukro_playwright("cz"),
    "aukro_sk": lambda: fetch_aukro_playwright("sk"),
    "olx_pl": fetch_olx_pl,
    "kleinanzeigen_de": fetch_kleinanzeigen,
    "vinted_cz": lambda: fetch_vinted("cz"),
    "vinted_sk": lambda: fetch_vinted("sk"),
    "web_search": fetch_web_search,
}


_DEAD_LISTING_PHRASES = [
    "inzerát nenalezen", "inzerát byl smazán", "inzerát bol vymazaný",
    "inzerát neexistuje", "stránka nenalezena", "stránka nebola nájdená",
    "nabídka byla ukončena", "ponuka bola ukončená", "tato nabídka již neni",
    "page not found", "nicht gefunden", "diese anzeige ist nicht mehr",
    "ogłoszenie zostało usunięte", "nie znaleziono strony",
]

LIVENESS_CHECK_BATCH_SIZE = 20  # per run — spreads the cost across runs rather than checking everything at once


def _listing_is_dead(url):
    """True only on a high-confidence signal (404/410, or an explicit
    'removed' phrase on the page) — a network hiccup or timeout does NOT
    count as dead, since a false removal is worse than checking again next
    run."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
    except Exception:
        return None  # couldn't tell — leave it alone, try again next time
    if resp.status_code in (404, 410):
        return True
    if resp.status_code >= 400:
        return None  # ambiguous (403, 503, etc.) — don't remove on this alone
    body = resp.text.lower()
    if any(phrase in body for phrase in _DEAD_LISTING_PHRASES):
        return True
    return False


RESCORE_PER_RUN = 300  # legacy entries re-analyzed per run (one-time migration)


def _analyze(url_of_image):
    m = get_matcher()
    if not m or not url_of_image:
        return None
    try:
        return m.analyze_image_bytes(download_image(url_of_image))
    except Exception as e:
        print(f"  [!] Could not analyze image {url_of_image[:80]}: {e}")
        return None


def _store_analysis(item, analysis):
    if analysis is None:
        item["score"] = None
        item["match"] = None
        return
    item["score"] = round(analysis["sim"], 4)
    item["p_target"] = round(analysis["p_target"], 3)
    item["p_chrome"] = round(analysis["p_chrome"], 3)
    item["category"] = analysis["category"]
    item["match"] = match_score(analysis["sim"], analysis["p_target"])


def _better(a, b):
    """Which of two duplicate entries to keep: higher tier, then higher
    match, then the newer one."""
    rank = TIER_RANK
    ka = (rank.get(a.get("tier"), -1), a.get("match") or a.get("score") or 0, a.get("found_at", ""))
    kb = (rank.get(b.get("tier"), -1), b.get("match") or b.get("score") or 0, b.get("found_at", ""))
    return a if ka >= kb else b


def dedupe(items):
    """Collapses entries sharing a URL or a title; keeps the best of each group."""
    winners, index = [], {}
    for it in items:
        it["id"] = listing_id(it["url"])  # re-hash with the current URL normalizer
        keys = ["id:" + it["id"]]
        tk = title_key(it["title"])
        if tk:
            keys.append("t:" + tk)
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            hit = len(winners)
            winners.append(it)
        else:
            winners[hit] = _better(winners[hit], it)
        for k in keys:
            index[k] = hit
    return winners


def prune_found(found, config):
    """
    Re-applies the CURRENT rules to every stored listing, so tightening the
    rules also cleans up old entries — not just future ones:
      1. title cleanup + de-duplication (same URL, re-posts, cz/sk mirrors)
      2. web-search entries must be individual listing pages
      3. legacy entries (stored before photo classification existed) get
         their photo re-analyzed once, then go through evaluate() like new ones
      4. a bounded liveness check removes listings that have been taken down
    """
    before = len(found)
    for it in found:
        it["title"] = clean_title(it.get("title", ""))
    found = dedupe(found)
    dupes = before - len(found)

    kept, dropped = [], 0
    rescored = 0
    for item in found:
        if str(item.get("site", "")).startswith("web") and not is_listing_url(item["url"]):
            dropped += 1
            continue
        if item.get("image_url") and "category" not in item and rescored < RESCORE_PER_RUN:
            # Cheap text check first: no need to download a photo for
            # something the title already rules out.
            if exclusion_reason(item["title"], item["url"]) is None:
                a = _analyze(item["image_url"])
                if a is not None:  # never wipe the old score on a failed download/model
                    _store_analysis(item, a)
                    rescored += 1
        analysis = None
        if item.get("category"):
            analysis = {"sim": item["score"], "p_target": item.get("p_target", 0.0),
                        "p_chrome": item.get("p_chrome", 0.0), "category": item["category"]}
        elif item.get("image_url") and item.get("score") is not None:
            # Not re-analyzed yet (matcher unavailable or batch cap) — keep
            # the old similarity-only judgment for now.
            # Without a classification, only trust it if the title says chair.
            if has_chair_word(item["title"]):
                cat = "target"
            elif name_signal(item["title"], item.get("text", "")):
                cat = "unclassified"  # possibly a collection piece; not treated as junk
            else:
                cat = "other"
            analysis = {"sim": item["score"], "p_target": 0.0, "p_chrome": 1.0 if cat == "target" else 0.0,
                        "category": cat}
        tier, _ = evaluate(item["title"], item.get("text", ""), item["url"], analysis, config)
        if tier:
            item["tier"] = tier
            item["keyword_hit"] = tier in ("exact", "keyword", "collection")
            item.setdefault("last_checked", item.get("found_at"))
            kept.append(item)
        else:
            dropped += 1

    kept.sort(key=lambda x: x.get("last_checked") or "")
    dead_ids = set()
    for item in kept[:LIVENESS_CHECK_BATCH_SIZE]:
        if _listing_is_dead(item["url"]) is True:
            dead_ids.add(item["id"])
        else:
            item["last_checked"] = time.strftime("%Y-%m-%d %H:%M:%S")
    final = [i for i in kept if i["id"] not in dead_ids]

    print(f"  Clean-up: {dupes} duplicate(s) merged, {dropped} no longer matching the rules, "
          f"{len(dead_ids)} dead link(s), {rescored} older photo(s) re-analyzed. {len(final)} kept.")
    return final


def run_once(config):
    seen = load_seen()
    found = prune_found(load_found(), config)
    known_titles = {title_key(i["title"]) for i in found}
    topic = config.get("ntfy_topic")
    new_matches, new_listed = 0, 0
    notify_worthy = []
    skipped = {}

    all_listings = []
    for site_name in config.get("enabled_sites", list(SITE_MODULES.keys())):
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
        print(f"  -> {len(listings)} results")
        for l in listings:
            l["site"] = site_name
            l["title"] = clean_title(l.get("title", ""))
        all_listings.extend(listings)

    if config.get("facebook_session_path"):
        for l in fetch_facebook_marketplace(config["facebook_session_path"],
                                            location=config.get("facebook_location", "czech-republic")):
            l["site"] = "facebook_marketplace"
            all_listings.append(l)

    # One entry per URL AND per title — the same ad found by several
    # queries, re-posted under a new ID, or mirrored on bazos.cz + bazos.sk.
    unique, keys = [], set()
    for l in all_listings:
        lid, tk = listing_id(l["url"]), title_key(l["title"])
        if lid in keys or tk in keys:
            continue
        keys.update({lid, tk})
        unique.append(l)
    new_listings = [l for l in unique
                    if listing_id(l["url"]) not in seen and title_key(l["title"]) not in known_titles]
    print(f"\n{len(all_listings)} raw -> {len(unique)} unique -> {len(new_listings)} new.\n")

    for l in new_listings:
        lid = listing_id(l["url"])
        seen.add(lid)
        text = l.get("text", "")
        reason = exclusion_reason(l["title"], l["url"])
        analysis = None
        if l["site"] == "web_search":
            l["site"] = "web:" + l.get("source", site_label(l["url"]))
            if not l.get("image_urls") and (reason is None or name_signal(l["title"], text)):
                img = fetch_preview_image(l["url"])
                if img:
                    l["image_urls"] = [img]
        if config.get("use_image_matching", True) and l.get("image_urls") and (reason is None or name_signal(l["title"], text)):
            analysis = _analyze(l["image_urls"][0])
        tier, why = evaluate(l["title"], text, l["url"], analysis, config)
        sc = f"sim={analysis['sim']:.2f} cat={analysis['category']} p_target={analysis['p_target']:.2f}" if analysis else "no photo"
        print(f"  [{l['site']}] {l['title'][:55]!r} | {sc} -> {tier or 'skip'} ({why})")
        if not tier:
            bucket = why.split(":")[0]
            skipped[bucket] = skipped.get(bucket, 0) + 1
            continue

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        item = {"id": lid, "url": l["url"], "title": l["title"], "text": text[:500], "site": l["site"],
                "image_url": (l.get("image_urls") or [None])[0], "tier": tier,
                "keyword_hit": tier in ("exact", "keyword", "collection"), "found_at": now_str, "last_checked": now_str}
        _store_analysis(item, analysis)
        found.append(item)
        known_titles.add(title_key(l["title"]))
        new_listed += 1
        if tier in NOTIFY_TIERS:
            new_matches += 1
            notify_worthy.append(("⭐ " if tier == "exact" else "") + l["title"])

    if skipped:
        print("\nSkipped by reason: " + ", ".join(f"{k}={v}" for k, v in sorted(skipped.items(), key=lambda x: -x[1])))

    # One notification per scan, summarizing everything found.
    if new_matches and topic:
        preview = "\n".join(f"• {t}" for t in notify_worthy[:5])
        more = f"\n…and {new_matches - 5} more" if new_matches > 5 else ""
        exact = any(t.startswith("⭐") for t in notify_worthy)
        send_notification(
            topic,
            title=("T2306 found! " if exact else "") +
                  f"{new_matches} possible chair match{'es' if new_matches != 1 else ''}",
            message=preview + more,
            url=config.get("site_url"),
        )

    found.sort(key=lambda x: x["found_at"], reverse=True)
    save_found(found)
    save_seen(seen)
    print(f"\nDone. {new_listed} new listing(s) added ({new_matches} strong/name matches"
          f"{', 1 notification sent' if new_matches and topic else ''}).")
    return {"new_matches": new_matches, "new_listed": new_listed, "total_new_listings": len(new_listings)}


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
