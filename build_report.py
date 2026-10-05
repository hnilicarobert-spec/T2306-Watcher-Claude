#!/usr/bin/env python3
"""
Run by GitHub Actions (see .github/workflows/scan.yml). Performs one scan
pass and writes docs/index.html — the page GitHub Pages serves for free.

Not meant to be run by hand, though `python3 build_report.py` works fine
for testing locally too.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import chair_watcher as cw
import report

DOCS_DIR = Path(__file__).parent / "docs"


def main():
    config = cw.load_config()

    # Let a GitHub Actions secret override the committed config.json value,
    # so you don't have to put your ntfy topic in a public repo.
    env_topic = os.environ.get("NTFY_TOPIC")
    if env_topic:
        config["ntfy_topic"] = env_topic

    cw.run_once(config)

    found = cw.load_found()
    last_run_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    import json
    try:
        status = json.loads(cw.STATUS_PATH.read_text())
    except Exception:
        status = None
    html = report.render_static(found, last_run_text, source_status=status)

    DOCS_DIR.mkdir(exist_ok=True)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"Wrote {DOCS_DIR / 'index.html'} ({len(found)} listing(s)).")


if __name__ == "__main__":
    main()
