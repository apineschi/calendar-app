# Instructions

Setup (one-time) and day-to-day operations. See [ARCHITECTURE.md](ARCHITECTURE.md)
for how the pieces fit together.

---

## One-time setup

### 1. Enable GitHub Pages

1. On GitHub, open `apineschi/calendar-app` > **Settings** > **Pages**.
2. Under **Build and deployment**, set **Source** to "Deploy from a branch",
   branch `main`, folder `/docs`. Save.
3. After the first push (step 5 below), your dashboard will be live at
   `https://apineschi.github.io/calendar-app/`.

### 2. Create a GitHub fine-grained token (so the Worker can write to your repo)

1. On GitHub: **Settings** (your account, not the repo) > **Developer
   settings** > **Personal access tokens** > **Fine-grained tokens** >
   **Generate new token**.
2. **Repository access**: "Only select repositories" > `calendar-app`.
3. **Permissions**: **Repository permissions** > **Contents** > **Read and
   write**. Leave everything else at no access.
4. Generate, copy the token for step 3.

### 3. Pick an app password

Any password you'll remember — this is what stops strangers from calling
your Worker and writing junk into your events. You'll type it once on your
phone.

### 4. Deploy the Worker

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com), sign up free
   (no card needed), go to **Workers & Pages** > **Create** > **Create
   Worker**. Give it a name like `calendar-app` and deploy the default
   template.
2. Click **Edit code** (the in-browser editor). Delete the placeholder
   content and paste in the contents of this project's `worker/worker.js`.
   Click **Deploy**.
3. Go to the Worker's **Settings** > **Variables and Secrets**. Add:
   - `GITHUB_TOKEN` — from step 2. Encrypt it.
   - `APP_SECRET` — from step 3. Encrypt it.
   - `GITHUB_OWNER` — `apineschi`
   - `GITHUB_REPO` — `calendar-app`
   - `ALLOWED_ORIGIN` — `https://apineschi.github.io`
   Save/deploy after adding these.
4. Note your Worker's URL, shown at the top of its page — something like
   `https://calendar-app.<your-subdomain>.workers.dev`.

### 5. Wire the Worker URL into the front end and push

1. In this local folder, open `docs/index.html`.
2. Find the line `const WORKER_URL = "REPLACE_WITH_YOUR_WORKER_URL";` and
   replace the placeholder with your actual Worker URL from step 4 (keep the
   quotes).
3. Commit and push (e.g. via GitHub Desktop).
4. Wait a minute for GitHub Pages to build, then visit
   `https://apineschi.github.io/calendar-app/`.

### 6. Set up email alerts

Same pattern as `job-scraper`. In the `calendar-app` repo on GitHub:
**Settings** > **Secrets and variables** > **Actions** > **New repository
secret**, add:
- `EMAIL_USERNAME` — a Gmail address
- `EMAIL_PASSWORD` — a Gmail [app password](https://myaccount.google.com/apppasswords)
  (not your normal Gmail password)
- `EMAIL_TO` — where alerts should be sent (can be the same address)

### 7. (Optional) Enable the Workers AI extraction fallback

Only needed if you find the built-in date extraction (JSON-LD, then text
heuristics) is missing dates on sites you care about — everything still
works without this, those events just show "date pending" for longer.

1. In the Cloudflare dashboard, note your **Account ID** (right sidebar of
   any Workers page).
2. Create an API token: **My Profile** > **API Tokens** > **Create Token** >
   use the "Workers AI" template (or a custom token with Workers AI: Edit
   permission).
3. In the `calendar-app` GitHub repo: **Settings** > **Secrets and
   variables** > **Actions**, add `CLOUDFLARE_ACCOUNT_ID` and
   `CLOUDFLARE_API_TOKEN`.

### 8. Subscribe to the calendar feed

1. Open `https://apineschi.github.io/calendar-app/`, and copy the feed URL
   shown under "Subscribe on Samsung Calendar" (it's
   `https://apineschi.github.io/calendar-app/calendar.ics`).
2. In Google Calendar (web): **Other calendars** > **+** > **From URL**,
   paste it, click **Add calendar**.
3. On your phone, open the Samsung Calendar app > menu > **Manage
   calendars** — the Google account's calendars (including this new one)
   should already be listed. Toggle it on or off whenever you like.

You're set up. Adding an event on the dashboard should show up there
immediately (date "pending" until the next scan); dates get filled in
automatically once a month, or immediately if you trigger the workflow
manually (next section).

---

## Day-to-day operations

### Add an event

Open `https://apineschi.github.io/calendar-app/` (worth adding to your
phone's home screen), paste the event's URL into the "Add an event" box,
optionally fill in a name/tags/notes/free-or-paid, and click **Add event**.
It'll ask for your app password the first time and remember it after that.
The date shows as "pending" until the next check.

### Check a newly-added event's date right away

Rather than waiting for the monthly schedule: on GitHub, go to the
`calendar-app` repo > **Actions** tab > **Monthly Event Check** > **Run
workflow**. Takes a minute or two; refresh the dashboard afterward.

### Edit tags, notes, or fix a wrong field

On the dashboard, click **Edit** on the event's card, change whatever needs
changing (including typing in a date yourself if you found it somewhere the
scanner didn't catch), and **Save**. Manually entering a date counts as a
confirmed sighting, same as the scanner finding one — it resets the
"can't find it" alert for that event.

### Delete an event

Click **Delete** on its card, confirm. Also regenerates the calendar feed
immediately.

### Change the app password

Update `APP_SECRET` in the Worker's Settings > Variables. Then on your
phone, clear that site's browser data (or just try an edit — it'll fail with
"Wrong password" and prompt you again automatically).

### Turn off email alerts entirely

Remove the `EMAIL_USERNAME`/`EMAIL_PASSWORD`/`EMAIL_TO` secrets from the
repo's Actions secrets — the workflow step is conditional on an alert firing
and skips silently if the mail action's credentials are missing.
