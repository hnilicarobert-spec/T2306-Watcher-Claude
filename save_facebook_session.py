#!/usr/bin/env python3
"""
Run this ONCE to let chair_watcher.py access Facebook Marketplace.

It opens a real, visible browser window. Log into Facebook normally in that
window (enter your email/password, solve any 2FA/checkpoint Facebook shows
you), then come back to this terminal and press Enter. Your login session
gets saved to facebook_session.json, which chair_watcher.py then reuses —
you won't need to log in again until Facebook eventually expires the session
(if that happens, just re-run this script).

Strongly recommended: use a secondary/throwaway Facebook account for this,
not your main one — automated access can occasionally trigger security
checks on an account.

Usage:
    pip install playwright
    playwright install chromium
    python3 save_facebook_session.py
"""
from playwright.sync_api import sync_playwright

OUTPUT = "facebook_session.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://www.facebook.com/login")
    print("\nA browser window has opened.")
    print("Log into Facebook in that window (use a secondary account if you have one).")
    input("Once you're fully logged in and see your Facebook feed, press Enter here...\n")
    context.storage_state(path=OUTPUT)
    browser.close()

print(f"Saved login session to {OUTPUT}")
print(f'Now set "facebook_session_path": "{OUTPUT}" in config.json to enable Marketplace scanning.')
