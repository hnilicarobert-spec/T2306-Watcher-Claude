"""
Renders found_listings.json into a static, mobile-friendly HTML page.
No server involved — this file is written to docs/index.html and served
for free by GitHub Pages. Kept separate from chair_watcher.py so the
rendering logic is reusable and easy to tweak.
"""

CARD_TEMPLATE = """
<div class="card">
  <a href="{url}" target="_blank" rel="noopener">
    {img_html}
    <div class="card-body">
      <div class="badge badge-{tier}">{tier_label}</div>
      <div class="title">{title}</div>
      <div class="meta">{site} · {score_label} · {found_at}</div>
    </div>
  </a>
</div>
"""

PAGE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chair Watcher</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 16px; background: #f2f1ed; color: #222; max-width: 600px; margin: 0 auto;
    }}
    h1 {{ font-size: 1.3em; margin: 16px 0 4px 0; }}
    .status {{ font-size: 0.85em; color: #666; margin-bottom: 10px; }}
    .scan-hint {{
      background: #eef3ee; border-radius: 10px; padding: 10px 14px; font-size: 0.85em;
      margin-bottom: 18px; color: #2b6b4f;
    }}
    .scan-hint a {{ color: #2b6b4f; font-weight: 600; }}
    .empty {{ color: #777; padding: 30px 0; text-align: center; }}
    .card {{
      background: white; border-radius: 14px; overflow: hidden; margin-bottom: 14px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.12);
    }}
    .card a {{ text-decoration: none; color: inherit; display: block; }}
    .card img {{ width: 100%; height: 200px; object-fit: cover; display: block; background: #ddd; }}
    .card-body {{ padding: 10px 14px 14px 14px; }}
    .title {{ font-weight: 600; margin: 6px 0 4px 0; line-height: 1.3; }}
    .meta {{ font-size: 0.8em; color: #777; }}
    .badge {{
      display: inline-block; font-size: 0.7em; font-weight: 700; text-transform: uppercase;
      padding: 3px 8px; border-radius: 6px; letter-spacing: 0.03em;
    }}
    .badge-keyword {{ background: #ffe4b8; color: #8a5600; }}
    .badge-strong {{ background: #c9f2d8; color: #146c3a; }}
    .badge-similar {{ background: #e3e3e3; color: #555; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #15161a; color: #eee; }}
      .card {{ background: #1f2127; }}
      .meta {{ color: #999; }}
      .status {{ color: #999; }}
      .scan-hint {{ background: #10281c; color: #8fd6ab; }}
      .scan-hint a {{ color: #8fd6ab; }}
    }}
  </style>
</head>
<body>
  <h1>🪑 Chair Watcher</h1>
  <div class="status">{status_line}</div>
  <div class="scan-hint">
    Want to check right now instead of waiting for the next automatic scan?
    Open this repo's <strong>Actions</strong> tab on GitHub (works fine from
    your iPhone's browser or the GitHub app) → select "Scan for chair" →
    tap <strong>Run workflow</strong>. New results appear here a minute or two later.
  </div>
  {cards_html}
</body>
</html>
"""


def _confidence_sort_key(item):
    """Highest-probability first. A keyword/brand-name hit is the most
    confident signal we have (ranked above pure image-similarity tiers),
    then sorted by the actual CLIP score within each tier — so a keyword
    match that ALSO has a strong photo match (e.g. score 0.94) still sorts
    above one with no photo at all, and "similar" entries sort by how close
    they actually scored rather than by when they were found."""
    tier_rank = {"keyword": 2, "strong": 1, "similar": 0}.get(item.get("tier"), -1)
    score = item.get("score")
    score_for_sort = score if score is not None else -1  # no photo available sorts last within its tier
    return (tier_rank, score_for_sort)


def render_static(found, last_run_text, repo_actions_url=None):
    found = sorted(found, key=_confidence_sort_key, reverse=True)
    status_line = f"Last scan: {last_run_text} · {len(found)} total candidate(s) tracked, sorted by match strength"

    if not found:
        cards_html = ('<div class="empty">No candidate listings yet. '
                       'Trigger a scan from the Actions tab, or wait for the next automatic run.</div>')
    else:
        tier_labels = {"keyword": "Name match", "strong": "Strong match", "similar": "Similar"}
        cards = []
        for item in found:
            score = item.get("score")
            score_label = f"similarity {score:.2f}" if score is not None else "keyword match"
            img_html = (
                f'<img src="{item["image_url"]}" loading="lazy" alt="">'
                if item.get("image_url") else ""
            )
            cards.append(CARD_TEMPLATE.format(
                url=item["url"],
                img_html=img_html,
                tier=item.get("tier", "similar"),
                tier_label=tier_labels.get(item.get("tier"), "Match"),
                title=item["title"],
                site=item["site"],
                score_label=score_label,
                found_at=item.get("found_at", ""),
            ))
        cards_html = "\n".join(cards)

    return PAGE_TEMPLATE.format(status_line=status_line, cards_html=cards_html)
