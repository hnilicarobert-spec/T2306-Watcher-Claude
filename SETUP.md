# Setup tutorial — no computer, no server, no credit card

Everything runs on GitHub's free infrastructure. You do this setup once,
entirely in a web browser — no terminal, no git commands, no coding.
Total time: about 15–20 minutes.

---

## Step 1 — Create a free GitHub account

1. Go to [github.com/signup](https://github.com/signup) and create an
   account (just an email + password — no card anywhere in this flow).
2. Verify your email when prompted.

## Step 2 — Create a new repository

1. Click the **+** in the top-right corner → **New repository**.
2. Name it something like `chair-watcher`.
3. Set it to **Public** (this is required for everything to stay free —
   see the "Limitations" section in `README.md` for what that means).
4. Leave everything else as default, click **Create repository**.

## Step 3 — Upload the project files

You're now on your new empty repository's page.

1. Click **uploading an existing file** (or "Add file" → "Upload files").
2. Drag in every file and folder from the `chair_watcher` folder you were
   given — including the hidden `.github` folder, `reference_photos/`,
   and everything else. (If your file browser hides folders starting with
   a dot, see the note at the bottom of this step.)
3. Scroll down, write any commit message (e.g. "Initial upload"), click
   **Commit changes**.

**If your browser's upload dialog won't show the `.github` folder:** GitHub's
drag-and-drop upload accepts folders fine — just make sure you're dragging
the whole `chair_watcher` folder's contents (not zipping it first). If a
folder picker hides dot-folders, drag directly from your file manager
(Finder/Explorer) instead of using an "Open" dialog, or install
[GitHub Desktop](https://desktop.github.com) (free, no card) and use that
to publish the folder instead — it has no trouble with hidden folders.

## Step 4 — Add your ntfy notification topic as a secret

This keeps your notification channel private even though the repo is public.

1. Install the free **ntfy** app on your iPhone (App Store). Open it, tap
   **+**, subscribe to a topic name that's unique and hard to guess (e.g.
   `robert-t2306-chair-hunt-8821`). No account needed.
2. On GitHub, go to your repo → **Settings** tab → **Secrets and
   variables** → **Actions** → **New repository secret**.
3. Name: `NTFY_TOPIC`. Value: the exact topic name from step 1. Click
   **Add secret**.

(Skip this step if you don't want push notifications — the results page
works fine either way, you'd just need to check it manually.)

## Step 5 — Enable GitHub Pages

1. Still in **Settings**, click **Pages** in the left sidebar.
2. Under "Build and deployment" → **Source**, select **GitHub Actions**
   (not "Deploy from a branch").
3. That's it — nothing to save separately, this takes effect immediately.

## Step 6 — Run it for the first time

1. Go to the **Actions** tab on your repo.
2. You should see a workflow called **"Scan for chair"**. Click it.
3. Click **Run workflow** (a dropdown button on the right) → **Run workflow**
   again to confirm.
4. Wait 2–5 minutes. Refresh the page — you'll see a run in progress, then
   a green checkmark when it finishes. (The very first run is the slowest,
   since it downloads the image-matching model; later runs are faster.)

If it shows a red ❌ instead, click into the run to see which step failed
— usually self-explanatory (e.g. a typo in a secret name). Paste the error
back to me if you're stuck.

## Step 7 — Find and open your results page

1. Go to **Settings → Pages** again. Near the top you'll see "Your site is
   live at" with a link like:
   ```
   https://yourusername.github.io/chair-watcher/
   ```
2. Open that link — on your computer first to confirm it works, then on
   your iPhone.
3. On your iPhone in Safari, tap **Share → Add to Home Screen** to save it
   as an app icon.

That's your dashboard. It updates automatically every ~30 minutes, forever,
with no computer or server involved.

## Step 8 — "Push a search" from your iPhone, any time

1. Open your repo in Safari or the **GitHub mobile app** (free, on the App
   Store — handy for this specifically).
2. Go to the **Actions** tab → **"Scan for chair"** → **Run workflow**.
3. A new scan starts within seconds. Check the results page again in a
   minute or two.

This is the full equivalent of a "scan now" button — just one tab further
in, and it works from literally anywhere with no server to wake up.

---

## Calibrating match sensitivity (do this after your first few scans)

Open your results page and look at anything tagged **"Similar"** — these
are the weaker matches. If you see things that are clearly not your chair
at all, edit `config.json` in the repo (click the file → pencil icon →
edit → commit) and raise `"similarity_threshold_list"` slightly (e.g.
0.20 → 0.23). If nothing is showing up at all after a few scans, lower it
instead. Every edit you commit takes effect on the next scheduled or
manually triggered run.

## Reliable hourly scheduling (fixes "it only runs every few hours")

GitHub's own documentation calls its scheduled workflow trigger
"best-effort, not guaranteed" — in practice, frequent schedules on public
repos are known (as of 2026, widely reported on GitHub's own community
forum) to get delayed by hours or dropped entirely during platform load.
That's almost certainly why a 30-minute schedule was actually landing
every ~3 hours. Changing the cron interval doesn't fix this — the fix is
to stop relying on GitHub's native scheduler as the only trigger.

The reliable approach: a free external cron service calls the GitHub API
directly to start the workflow, on a real, dependable schedule. GitHub's
native `schedule:` trigger (now set to hourly in `scan.yml`) stays in
place too, as a free backup in case the external one is ever missed —
having both costs nothing and only helps.

**Step 1 — Create a GitHub Personal Access Token (fine-grained, minimal scope)**

1. On GitHub, go to **Settings** (your account, top-right avatar menu) →
   **Developer settings** → **Personal access tokens** → **Fine-grained tokens**.
2. Click **Generate new token**. Name it `chair-watcher-trigger`.
3. **Resource owner**: you. **Repository access**: "Only select
   repositories" → choose this repo specifically — not all repos.
4. **Permissions** → **Repository permissions** → find **Actions** → set
   to **Read and write**. Leave everything else as default (no access).
5. Set an **expiration** (GitHub requires one for fine-grained tokens; a
   year out is fine). Generate, then **copy the token** — you won't be
   able to see it again. Treat it like a password.

**Step 2 — Create a free cron-job.org account**

1. Sign up at [cron-job.org](https://cron-job.org) — free, no credit card.
2. Create a new cron job:
   - **URL**: `https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO/actions/workflows/scan.yml/dispatches`
     (replace with your actual username/repo name)
   - **Request method**: `POST`
   - **Schedule**: every hour
   - Under **Advanced** → **Headers**, add two headers:
     - `Authorization` → `Bearer YOUR_TOKEN_FROM_STEP_1`
     - `Accept` → `application/vnd.github+json`
   - Under **Advanced** → **Request body**, set it to: `{"ref":"main"}`
     (or whatever your default branch is called)
3. Save and enable the job.

This token lives only in your private cron-job.org account — never in the
public repo — so it's safe even though the repo itself is public.

**Step 3 — Verify it**

Wait a bit past the next hour mark, then check your repo's Actions tab.
You should see a new run whose trigger says "Scheduled" via the API (shows
the same as a normal dispatch). If cron-job.org's job history shows a
successful `204` response, the trigger worked — GitHub just didn't create
a visible difference in the UI between this and a manual run.

## Changing the scan frequency

Open `.github/workflows/scan.yml`, find this line:
```yaml
- cron: "0 * * * *"
```
This is the free native-GitHub backup schedule (hourly). The reliable
primary schedule is the cron-job.org job from the section above — change
its interval there instead if you want more/less frequent scans; GitHub's
own throttling on public repos means asking the native trigger for
anything much more frequent than hourly tends not to be honored anyway.

## Updating the code later

If I send you an updated `.py` file: open that file in your repo, click the
pencil (✏️) icon to edit, paste in the new content, and commit. No local
setup needed at all.

## Getting results from more websites (recommended)

GitHub's servers are "datacenter" computers, and several sites (DuckDuckGo,
Vinted, sometimes Aukro) block those. The **Sources** panel at the top of your
results page shows each site's status after every scan: ✓ working,
⚠️ blocked (with the reason), · no results.

To reach the sites that block scraping, add search engines that work through
an official API. Both are free with no credit card, and the scanner never
exceeds their free monthly allowance. You can add one, both, or neither.

### Tavily (1,000 free searches/month) — about 10 minutes
1. Sign up at **tavily.com** (email or Google login, no card).
2. Copy your API key from the dashboard (starts with `tvly-`).
3. In your GitHub repo: **Settings → Secrets and variables → Actions →
   New repository secret**. Name: `TAVILY_API_KEY`, value: the key.

### SerpApi (250 free Google searches/month) — about 10 minutes
1. Sign up at **serpapi.com** (no card).
2. Copy your API key from the dashboard.
3. Add it as a secret named `SERPAPI_KEY` the same way.

These search the whole web, so they also find ads on Sbazar, Vinted, Aukro,
Facebook and dealer sites that the scanner can't open directly.

### Google Alerts (free, unlimited, no key)
1. Go to **google.com/alerts** (signed in with Google).
2. Type a search, e.g. `kodreta židle`, click **Show options**, set
   **Deliver to: RSS feed**, then **Create alert**.
3. Click the RSS icon next to the alert and copy its address.
4. In `config.json`, paste it into `"rss_feeds"`, e.g.
   `"rss_feeds": ["https://www.google.com/alerts/feeds/123/456"]`.
   Good alerts to create: `kodreta židle`, `kodreta stolička`, `kodreta křeslo`,
   `T2306`, `Chlebo kodreta`.

### The marketplaces' own alerts (best for Facebook, Sbazar, Vinted)
Some sites block every automated reader, but their own apps can notify you
directly, and nothing beats that for speed:
- **Facebook Marketplace**: search "kodreta" → tap **Save search** / turn on notifications.
- **Sbazar**: search, then **Hlídat** (watch) — emails/app alerts on new ads.
- **Vinted**: search "kodreta" → **Save search** → notifications on.
- **Bazoš**: "Hlídací pes" (bazos.cz) / "Strážny pes" (bazos.sk) at the bottom of a search.
- **Aukro**: search → **Uložit hledání**.
