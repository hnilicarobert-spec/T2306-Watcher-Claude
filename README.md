# Chair Watcher — T2306 (Hreščák / Kodreta Myjava)

Watches Czech, Slovak, Polish and German marketplace listings for your
chair — by keyword ("kodreta", "Jaroslav Hreščák", "T2306") AND by visual
similarity to your reference photos — and publishes a mobile-friendly page
listing every candidate found, with each one opening the original listing
in one tap.

**No server, no computer, no credit card, anywhere.** This runs entirely
on GitHub's free infrastructure: **GitHub Actions** does the scanning on a
schedule (free, unlimited minutes for a public repo), and **GitHub Pages**
hosts the results page (also free). Nothing needs to be "on" — there's no
computer or server for you to manage or pay for.

**👉 Full setup walkthrough: `SETUP.md`.** This file explains what's here
and how it works; `SETUP.md` is the beginner, step-by-step tutorial.

**Sites covered:**
- Bazoš.cz / Bazoš.sk
- Sbazar.cz
- Modrý Koník (modrykonik.sk) — best-effort, see note below
- Aukro.cz / Aukro.sk — via a real headless browser, no login
- OLX.pl (Poland)
- Kleinanzeigen.de (Germany)
- Facebook Marketplace — optional, best-effort, see "Facebook Marketplace" below

---

## How it works

- Every ~30 minutes (configurable), a free GitHub Actions workflow spins up,
  runs `build_report.py`, and shuts down again. There's no process running
  in between — nothing to pay for, nothing to leave on.
- `build_report.py` runs the actual scan (`chair_watcher.py`), then renders
  the results into `docs/index.html` using `report.py`.
- The workflow commits the updated results back to the repo and publishes
  `docs/index.html` via GitHub Pages — a plain web address you open on your
  iPhone, like any website.
- **To "push a search" from your phone**: open the repo's **Actions** tab
  (works fine in mobile Safari, or the official GitHub app) → "Scan for
  chair" → **Run workflow**. A fresh scan starts immediately; refresh the
  Pages link a minute or two later to see new results.

## Matching logic

- Every site is searched using both broad appearance-based terms (Czech/
  Slovak/Polish/German) AND the direct names "kodreta", "Jaroslav Hreščák",
  "T2306" — covering sellers who know what they have and ones who don't.
- Each new listing's photo is compared to your 3 reference photos using
  CLIP (an image-understanding model) for overall shape/structure
  similarity — a different fabric color on an otherwise matching frame
  should still score reasonably well.
- **Strong match** or **keyword hit** → pushes a notification to your
  phone (if you set up ntfy — see `SETUP.md`) and appears on the page.
- **Similar** (clears a lower bar) → appears on the page only, no
  notification — so you get a broader set of "worth a second look" chairs
  without being spammed.
- Tune the two thresholds (`similarity_threshold_notify`,
  `similarity_threshold_list`) in `config.json` after watching a few scans.

## Facebook Marketplace (optional)

Facebook fights automated access, and a login session is sensitive — it
must never be committed to the repo in plain form, especially since this
repo is public. The safe path:

1. On your own computer, run `python3 save_facebook_session.py` once (needs
   `pip install playwright` and `playwright install chromium` locally).
   Log into Facebook in the browser window that opens — ideally a
   secondary account, not your main one.
2. This creates `facebook_session.json`. Base64-encode it and store it as
   a GitHub **secret** named `FACEBOOK_SESSION_B64` (see `SETUP.md` for the
   exact commands) — never commit the raw file.
3. The workflow decodes that secret back into a temporary file on the
   runner for each scan, then discards it — it never touches the public
   repo.

If the session expires (Facebook logs it out periodically), just redo
Step 1–2 to refresh the secret. Treat this as a bonus channel — expect it
to need occasional attention.

## Limitations, honestly

- **This repo is public.** That's what makes Pages and unlimited Actions
  minutes free. It means anyone with the repo's link can see the listings
  found so far (`docs/index.html`, `found_listings.json`) — low-sensitivity
  content (furniture listings), but worth knowing. Your `ntfy_topic` and
  any Facebook session are kept out via secrets regardless (see above).
- **Facebook Marketplace**: fights automation actively, best-effort only.
- **Modrý Koník & Aukro**: built without the ability to load the live sites
  to verify exact page structure, so selectors are best-effort. If either
  returns 0 results every single scan, share a listing's page source and
  the selectors can be corrected.
- **Schedule timing**: GitHub may delay scheduled runs by a few minutes
  during busy periods — normal, free-tier behavior, not a bug.
- **Site redesigns**: marketplace sites change their HTML periodically,
  which eventually breaks one site's scraper while others keep working —
  that site's section in `chair_watcher.py` then needs a small update.
- This is for personal-use monitoring of public listing pages you could
  browse yourself — defaults are already polite (delays between requests).

## Files

- `SETUP.md` — **start here.** Full beginner setup tutorial.
- `.github/workflows/scan.yml` — the schedule + "Run workflow" button definition.
- `build_report.py` — entry point the workflow runs: scans, then builds the page.
- `report.py` — turns scan results into the static HTML page.
- `chair_watcher.py` — the scanning engine (site modules, keyword + image matching).
- `save_facebook_session.py` — one-time local helper for the optional Facebook channel.
- `config.example.json` → copy to `config.json` — your settings (not your secrets).
- `reference_photos/` — your 3 chair photos used for visual matching.
- `found_listings.json`, `seen.json` — auto-generated and auto-committed by the workflow; this is the scan's memory between runs.
- `docs/index.html` — auto-generated; the page GitHub Pages serves.

## Improving matches with more reference photos

The scanner compares every listing photo against everything in
`reference_photos/`. The most useful additions are:
- the chair **in leather** (the current photos are all fabric),
- **ordinary phone photos** of the chair at home, the kind sellers take
  (the current ones are studio shots on a white background),
- a **side or back view**,
- a **pair or set** of the chairs together.

Upload them in GitHub into `reference_photos/` (Add file → Upload files).
Use JPG or PNG; any file name works. Every scan's Actions log prints a check
line for each reference photo ("top category=target"). If one of yours shows
something else, that photo is confusing the model and is better removed.
