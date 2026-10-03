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

## Changing the scan frequency

Open `.github/workflows/scan.yml`, find this line:
```yaml
- cron: "*/30 * * * *"
```
`*/30` means every 30 minutes. Change to `*/15` for every 15 minutes, `*/60`
for hourly, etc. Commit the change — it takes effect on the next run.

## Updating the code later

If I send you an updated `.py` file: open that file in your repo, click the
pencil (✏️) icon to edit, paste in the new content, and commit. No local
setup needed at all.
