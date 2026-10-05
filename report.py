"""
Renders found_listings.json into a static, mobile-friendly HTML page.
No server involved — this file is written to docs/index.html and served
for free by GitHub Pages. Kept separate from chair_watcher.py so the
rendering logic is reusable and easy to tweak.

A few things (which-items-are-new, dismissed listings, sort order) live
entirely in the PHONE'S OWN localStorage, not in this generated file or
the repo — there's no server to write that state back to. That means:
- "NEW" and "removed" state is per-device/per-browser. Clearing Safari's
  site data, or opening the page on a different phone, resets it.
- The underlying data in found_listings.json is untouched either way —
  "remove" only hides a card on your screen, it doesn't affect whether
  the scanner could re-surface that URL (it can't, separately, because
  seen.json already prevents re-discovering the same URL again).
"""

import html

TIER_RANK = {"exact": 4, "keyword": 3, "strong": 2, "similar": 1, "collection": 0}

CARD_TEMPLATE = """
<div class="card" data-id="{id}" data-score="{score_for_sort}" data-tier-rank="{tier_rank}" data-found-at="{found_at_raw}">
  <button class="dismiss-btn" aria-label="Remove this listing" title="Remove">✕</button>
  <a href="{url}" target="_blank" rel="noopener">
    {img_html}
    <div class="card-body">
      <span class="badge badge-{tier}">{tier_label}</span>
      <div class="title">{title}</div>
      <div class="meta">{site} · {score_label} · {found_at}</div>
    </div>
  </a>
</div>
"""

HEAD_AND_STYLE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chair Watcher</title>
  <style>
    :root { color-scheme: light dark; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 16px; background: #f2f1ed; color: #222; max-width: 600px; margin: 0 auto;
    }
    h1 { font-size: 1.3em; margin: 16px 0 4px 0; }
    .status { font-size: 0.85em; color: #666; margin-bottom: 10px; }
    .scan-hint {
      background: #eef3ee; border-radius: 10px; padding: 10px 14px; font-size: 0.85em;
      margin-bottom: 14px; color: #2b6b4f;
    }
    .scan-hint a { color: #2b6b4f; font-weight: 600; }
    .sort-controls { display: flex; gap: 8px; margin-bottom: 16px; }
    .sort-btn {
      flex: 1; padding: 9px 0; border-radius: 10px; border: 1px solid #ccc;
      background: white; font-size: 0.85em; font-weight: 600; color: #444;
    }
    .sort-btn.active { background: #2b6b4f; color: white; border-color: #2b6b4f; }
    .empty { color: #777; padding: 30px 0; text-align: center; }
    .card {
      background: white; border-radius: 14px; overflow: hidden; margin-bottom: 14px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.12); position: relative;
    }
    .card a { text-decoration: none; color: inherit; display: block; }
    .card img { width: 100%; height: 200px; object-fit: cover; display: block; background: #ddd; }
    .card-body { padding: 10px 14px 14px 14px; }
    .title { font-weight: 600; margin: 6px 0 4px 0; line-height: 1.3; }
    .meta { font-size: 0.8em; color: #777; }
    .badge {
      display: inline-block; font-size: 0.7em; font-weight: 700; text-transform: uppercase;
      padding: 3px 8px; border-radius: 6px; letter-spacing: 0.03em; margin-right: 6px;
    }
    .badge-keyword { background: #ffe4b8; color: #8a5600; }
    .badge-strong { background: #c9f2d8; color: #146c3a; }
    .badge-similar { background: #e3e3e3; color: #555; }
    .sources { background: rgba(127,127,127,0.08); border-radius: 10px; padding: 8px 12px; margin-bottom: 12px; font-size: 0.82em; }
    .sources summary { font-weight: 600; cursor: pointer; }
    .src-row { display: flex; justify-content: space-between; gap: 10px; padding: 4px 0; border-top: 1px solid rgba(127,127,127,0.15); }
    .src-note { color: #777; text-align: right; }
    .badge-exact { background: #2b6b4f; color: #fff; }
    .badge-collection { background: #e4e0f5; color: #4a3d8f; }
    .badge-new { background: #ff5c5c; color: white; }
    .dismiss-btn {
      position: absolute; top: 8px; right: 8px; z-index: 5;
      width: 28px; height: 28px; border-radius: 50%; border: none;
      background: rgba(0,0,0,0.55); color: white; font-size: 0.85em; line-height: 1;
    }
    @media (prefers-color-scheme: dark) {
      body { background: #15161a; color: #eee; }
      .card { background: #1f2127; }
      .meta { color: #999; }
      .status { color: #999; }
      .scan-hint { background: #10281c; color: #8fd6ab; }
      .scan-hint a { color: #8fd6ab; }
      .sort-btn { background: #1f2127; border-color: #444; color: #ccc; }
    }
  </style>
</head>
<body>
  <h1>🪑 Chair Watcher</h1>
"""

SORT_CONTROLS = """
  <div class="sort-controls">
    <button class="sort-btn active" id="sort-match">Best match</button>
    <button class="sort-btn" id="sort-newest">Newest</button>
  </div>
"""

SCRIPT = """
<script>
(function() {
  var VIEWED_KEY = 'cw_viewed_ids';
  var DISMISSED_KEY = 'cw_dismissed_ids';

  function getSet(key) {
    try { return new Set(JSON.parse(localStorage.getItem(key) || '[]')); }
    catch (e) { return new Set(); }
  }
  function saveSet(key, set) {
    try { localStorage.setItem(key, JSON.stringify(Array.from(set))); } catch (e) {}
  }

  var viewed = getSet(VIEWED_KEY);
  var dismissed = getSet(DISMISSED_KEY);
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var container = document.getElementById('cards-container');

  function updateCount() {
    var visibleCount = cards.filter(function(c) { return c.style.display !== 'none'; }).length;
    var el = document.getElementById('visible-count');
    if (el) el.textContent = visibleCount;
  }

  cards.forEach(function(card) {
    var id = card.dataset.id;
    if (dismissed.has(id)) {
      card.style.display = 'none';
      return;
    }
    if (!viewed.has(id)) {
      var badgeEl = card.querySelector('.badge');
      if (badgeEl) {
        var newBadge = document.createElement('span');
        newBadge.className = 'badge badge-new';
        newBadge.textContent = 'NEW';
        badgeEl.parentNode.insertBefore(newBadge, badgeEl);
      }
    }
  });

  // Mark everything currently on the page as viewed, so next visit only
  // genuinely new listings since now get the NEW badge.
  cards.forEach(function(card) { viewed.add(card.dataset.id); });
  saveSet(VIEWED_KEY, viewed);

  document.querySelectorAll('.dismiss-btn').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.preventDefault();
      var card = btn.closest('.card');
      dismissed.add(card.dataset.id);
      saveSet(DISMISSED_KEY, dismissed);
      card.style.display = 'none';
      updateCount();
    });
  });

  function applySort(mode) {
    var sorted = cards.slice().sort(function(a, b) {
      if (mode === 'newest') {
        return b.dataset.foundAt.localeCompare(a.dataset.foundAt);
      }
      var ta = parseInt(a.dataset.tierRank, 10), tb = parseInt(b.dataset.tierRank, 10);
      if (tb !== ta) return tb - ta;
      return parseFloat(b.dataset.score) - parseFloat(a.dataset.score);
    });
    sorted.forEach(function(c) { container.appendChild(c); });
  }

  var matchBtn = document.getElementById('sort-match');
  var newestBtn = document.getElementById('sort-newest');
  if (matchBtn && newestBtn) {
    matchBtn.addEventListener('click', function() {
      applySort('match');
      matchBtn.classList.add('active');
      newestBtn.classList.remove('active');
    });
    newestBtn.addEventListener('click', function() {
      applySort('newest');
      newestBtn.classList.add('active');
      matchBtn.classList.remove('active');
    });
  }

  updateCount();
})();
</script>
"""


def _confidence_sort_key(item):
    """Highest-probability first. A keyword/brand-name hit is the most
    confident signal we have (ranked above pure image-similarity tiers),
    then sorted by the actual CLIP score within each tier — so a keyword
    match that ALSO has a strong photo match (e.g. score 0.94) still sorts
    above one with no photo at all, and "similar" entries sort by how close
    they actually scored rather than by when they were found."""
    return (TIER_RANK.get(item.get("tier"), -1), _sort_value(item))


def _sort_value(item):
    """Combined match (photo similarity + 'chrome tube with fabric seat'
    confidence) when available, else raw similarity, else last."""
    if item.get("match") is not None:
        return item["match"]
    if item.get("score") is not None:
        return item["score"] - 0.5  # older entries without a classification yet
    return -1


SOURCE_NAMES = {
    "bazos_cz": "Bazoš.cz", "bazos_sk": "Bazoš.sk", "aukro_cz": "Aukro.cz", "aukro_sk": "Aukro.sk",
    "vinted_cz": "Vinted.cz", "vinted_sk": "Vinted.sk", "olx_pl": "OLX.pl", "kleinanzeigen_de": "Kleinanzeigen.de",
    "web_search": "DuckDuckGo search", "tavily": "Tavily search", "serpapi": "Google (SerpApi)",
    "rss": "Google Alerts",
}


def _sources_panel(status):
    if not status or not status.get("sources"):
        return ""
    rows = []
    for key, st in status["sources"].items():
        name = html.escape(SOURCE_NAMES.get(key, key))
        errs = st.get("errors") or []
        n, added = st.get("results", 0), st.get("added", 0)
        if errs and n == 0:
            mark, note = "⚠️", html.escape(errs[0])
        elif n == 0:
            mark, note = "·", "no results this run"
        else:
            mark, note = "✓", f"{n} result(s), {added} new added"
            if errs:
                note += " · " + html.escape(errs[0])
        rows.append(f'<div class="src-row"><span>{mark} {name}</span><span class="src-note">{note}</span></div>')
    return (f'<details class="sources"><summary>Sources — last checked {html.escape(status.get("checked_at", ""))}</summary>'
            + "".join(rows) + "</details>")


def render_static(found, last_run_text, repo_actions_url=None, source_status=None):
    found = sorted(found, key=_confidence_sort_key, reverse=True)
    status_line = (
        f"Last scan: {last_run_text} · "
        f'<span id="visible-count">{len(found)}</span> candidate(s) shown · '
        f"tap Remove on anything irrelevant to hide it for good"
    )

    scan_hint = """
  <div class="scan-hint">
    Want to check right now instead of waiting for the next automatic scan?
    Open this repo's <strong>Actions</strong> tab on GitHub (works fine from
    your iPhone's browser or the GitHub app) → select "Scan for chair" →
    tap <strong>Run workflow</strong>. New results appear here a minute or two later.
  </div>
"""

    if not found:
        cards_html = ('<div class="empty">No candidate listings yet. '
                       'Trigger a scan from the Actions tab, or wait for the next automatic run.</div>')
    else:
        tier_labels = {"exact": "T2306", "keyword": "Kodreta chair", "strong": "Strong match",
                       "similar": "Similar", "collection": "Kodreta collection"}
        tier_rank_map = TIER_RANK
        cards = []
        for item in found:
            m = item.get("match")
            if m is not None:
                score_label = f"match {round(m * 100)}%"
                if item.get("p_target") is not None:
                    score_label += f" · chrome+fabric {round(item['p_target'] * 100)}%"
            elif item.get("score") is not None:
                score_label = f"similarity {item['score']:.2f}"
            else:
                score_label = "named in ad, no photo"
            score_for_sort = _sort_value(item)
            img_html = (
                f'<img src="{html.escape(item["image_url"], quote=True)}" loading="lazy" alt="">'
                if item.get("image_url") else ""
            )
            cards.append(CARD_TEMPLATE.format(
                id=item["id"],
                score_for_sort=score_for_sort,
                tier_rank=tier_rank_map.get(item.get("tier"), -1),
                found_at_raw=item.get("found_at", ""),
                url=html.escape(item["url"], quote=True),
                img_html=img_html,
                tier=item.get("tier", "similar"),
                tier_label=tier_labels.get(item.get("tier"), "Match"),
                title=html.escape(item["title"]),
                site=html.escape(str(item["site"])),
                score_label=score_label,
                found_at=item.get("found_at", ""),
            ))
        cards_html = "\n".join(cards)

    body = (
        f'  <div class="status">{status_line}</div>\n'
        + _sources_panel(source_status)
        + scan_hint
        + SORT_CONTROLS
        + f'  <div id="cards-container">\n{cards_html}\n  </div>\n'
    )

    return HEAD_AND_STYLE + body + SCRIPT + "\n</body>\n</html>\n"
