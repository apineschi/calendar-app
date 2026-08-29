# Architecture

How this system is built and why. For setup and day-to-day changes, see
[INSTRUCTIONS.md](INSTRUCTIONS.md) instead.

## Design constraint: zero paid infrastructure

Same constraint as `job-scraper` and `meal-tracker`: everything here runs on
free tiers, no paid API, no server you're renting. The pieces:

- **GitHub Actions** — runs the monthly re-check on a schedule, does all the
  scraping work.
- **A git-committed JSON file** (`docs/events.json`) — the database.
- **GitHub Pages** (serving `docs/`) — the dashboard. Static HTML/JS, reads
  `events.json` and `calendar.ics` directly.
- **A single Cloudflare Worker** — the only thing that ever holds secrets or
  writes data in real time (adding/editing/deleting an event from the
  dashboard). Everything else is read-only static files.
- **A generated `.ics` feed** (`docs/calendar.ics`), subscribed to once in
  Google Calendar — this is the Samsung Calendar sync path `meal-tracker`'s
  ARCHITECTURE.md named but never built. Samsung Calendar automatically
  mirrors Google account calendars, so no native Android code is needed, and
  toggling the calendar on/off is just the native checkbox in either app's
  calendar list.
- If the built-in extraction heuristics (below) both fail, an optional last
  resort calls **Cloudflare Workers AI** (free tier) rather than the
  Anthropic API — per-request billing there would violate the $0 constraint,
  and Workers AI's free daily allowance fails closed instead of ever
  charging. This is a pure accuracy improvement, never a dependency: it's
  skipped entirely if `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN` aren't
  set as repo secrets.

## Why "couldn't find a date" has to be a normal state, not an error

A real test against `glastonburyfestivals.co.uk` (2026-08-28) found no
structured data at all — just prose mentioning a "Fallow 2026" year and a
"2027 tickets" prize draw, with no parseable date anywhere on the page. Real
festival sites routinely go months between one year's event ending and the
next year's date being announced. `scanner/extractor.py` treats "found
nothing" as a normal return value (`{}`), not an exception, and the whole
alert system (`scanner/alerts.py`) exists specifically to surface this
gracefully instead of silently going stale.

## End-to-end flow

```mermaid
flowchart TD
    A["docs/index.html\nAdd event form"] -->|"POST /add-event\n+ X-App-Secret"| B["Cloudflare Worker"]
    B --> C["GitHub Contents API\nwrite docs/events.json\n+ docs/calendar.ics"]
    D["Scheduled trigger\n(cron, 1st of month)\nor workflow_dispatch"] --> E["main.py"]
    E --> F["scanner/extractor.py\nscrape_event(url) per event:\nJSON-LD -> OpenGraph ->\ntext heuristics -> Workers AI"]
    F --> G["merge found fields\n(never overwrite with null)"]
    G --> H["scanner/alerts.py\ncompute_alert() per event"]
    H -->|"alert fired"| I["notify/email.py digest"]
    I --> J["dawidd6/action-send-mail\n(Gmail SMTP)"]
    G --> K["scanner/ics.py\nbuild_calendar()"]
    K --> L["docs/calendar.ics"]
    G --> M["docs/events.json"]
    L --> N["git commit + push\n(bot identity)"]
    M --> N
    C --> O["GitHub Pages"]
    N --> O
    O --> P["Browser dashboard\n(docs/index.html)"]
    L -.->|"Add calendar > From URL,\none-time"| Q["Google Calendar"]
    Q -->|"auto-mirrors"| R["Samsung Calendar\n(toggle on/off natively)"]
```

Two independent write paths, same split as `meal-tracker`: the Worker
handles anything you do in real time (add/edit/delete, notes, tags), the
scheduled scan handles the actual re-checking of event pages. A brand-new
event added via the Worker starts with `start_date: null` ("date pending" on
the dashboard) — the next scheduled run, or a manual `workflow_dispatch`
from the Actions tab, fills it in. The scraping logic lives in exactly one
place (Python); the Worker's `buildIcs()` is a small JS port of
`scanner/ics.py`'s ICS-building logic (kept in sync manually), needed so the
Worker's own writes update the calendar immediately rather than waiting for
the next monthly scan.

## Repo layout

```
calendar-app/
  main.py                      # orchestrator, mirrors job-scraper/main.py
  requirements.txt
  scanner/
    extractor.py                # scrape_event(url) -> partial fields
    ics.py                        # events.json -> calendar.ics
    alerts.py                     # "can't find next date" logic
  notify/
    email.py                      # alert digest (adapted from job-scraper)
  worker/
    worker.js                     # Cloudflare Worker, mirrors meal-tracker's
  docs/                           # GitHub Pages root — the only public folder
    index.html                    # dashboard
    events.json                   # the database
    calendar.ics                  # generated feed
    manifest.json                  # lets index.html be "installed" to a home screen
    icons/                          # 🔮 favicon/home-screen icon set, same idea as job-scraper's
  .github/workflows/
    monthly-check.yml              # the actual cron job
  INSTRUCTIONS.md
  ARCHITECTURE.md
```

## The event record

```json
{
  "id": "glastonbury",
  "name": "Glastonbury Festival",
  "url": "https://www.glastonburyfestivals.co.uk/",
  "location": "Worthy Farm, Pilton, Somerset",
  "start_date": "2027-06-23",
  "end_date": "2027-06-27",
  "is_free": false,
  "price_text": "£375 + booking fee",
  "tags": ["music", "festival"],
  "notes": "Try the October resale window if the main sale sells out.",
  "last_known_year": 2025,
  "last_checked": "2026-08-28T09:00:00+00:00",
  "alert_sent_for_year": null,
  "calendar_uid": "glastonbury@calendar-app.apineschi"
}
```

- `start_date`/`end_date`/`location`/`is_free`/`price_text` are `null` until
  actually found — `main.py` merges in whatever `scrape_event()` returns
  field-by-field, and never lets a missing field overwrite a previously
  stored value ("couldn't find it this run" isn't the same as "it's now
  unknown").
- `last_known_year` is the year of the most recently *confirmed* date — set
  the first time any date is found, and the baseline `alerts.py` measures 3
  months back from. A manual date correction via `/edit-event` also updates
  it, same as a scan finding one.
- `alert_sent_for_year` dedupes the "can't find it" email so it only fires
  once per missing year, not every month once triggered. Cleared as soon as
  a date for that year (or later) shows up.
- `calendar_uid` is a stable per-event UID so regenerating `calendar.ics`
  updates the existing entry in Google Calendar rather than creating a
  duplicate on its next poll.
- Tags are free-text, entered on the dashboard — kept separate from
  `is_free`/`price_text` since those were asked for as their own fields, not
  as part of the type-of-event tagging.

## Event extraction (`scanner/extractor.py`)

Layered, cheapest and most reliable first, same philosophy as
`job-scraper/scrapers/base.py`'s label-scanning approach but generic (no
per-site modules, since URLs here are arbitrary, user-supplied festival
sites rather than a fixed list of institutions):

1. **schema.org JSON-LD** (`<script type="application/ld+json">` with an
   `@type` ending in "Event", or the informal "Festival") — `startDate`,
   `endDate`, `location`, `offers.price`/`isAccessibleForFree`. Reliable
   when present (ticketing platforms, WordPress event plugins); handles
   being wrapped in a list or `@graph`.
2. **Open Graph tags** — `og:title` only, for the name. Not trusted for
   dates.
3. **Text heuristics** — regex scan for month-name + plausible-year
   snippets, parsed with `dateutil`, picking the earliest future-dated
   candidate. This is the same "best-effort, can be wrong, gets corrected or
   left blank next time" spirit as `parse_closing_date()` in
   `job-scraper/scrapers/base.py`.
4. **Cloudflare Workers AI** (optional, free-tier only) — only tried if 1-3
   all found no date; asks a free model to read the page's own text and
   extract the same fields as structured JSON.
5. Anything still missing is left absent from the returned dict. This is the
   expected, common outcome (see the Glastonbury test above) — not a bug to
   fix, and exactly what `scanner/alerts.py` exists to handle.

## The "can't find next date" alert (`scanner/alerts.py`)

For an event with a known `last_known_year` (say 2025) and a known month
(derived from its current `start_date`, since that field is never cleared
once set):

```
target_year = last_known_year + 1                     # 2026
alert_start = (that month, target_year) minus 3 months  # e.g. May 2026 for an August event
if today >= alert_start and no date found yet for target_year (or later):
    fire the alert once (dedupe via alert_sent_for_year)
```

Brand-new events that have never had *any* date found have no baseline year
to alert against — they just show "date pending" on the dashboard
indefinitely; no email fires for those. The rule as asked for is specifically
about a recurring event's next occurrence going quiet, not about a first-time
scrape still being in progress.

## Known limitations (MVP)

- **No JS-rendered site support**: `scanner/extractor.py` uses a plain
  `requests` fetch, not a headless browser — a festival site that only
  renders its dates via client-side JavaScript (no JSON-LD, no server-side
  HTML) won't be picked up by the text-heuristic pass, though the schema.org
  JSON-LD pass still works since that's embedded in the initial HTML
  regardless. `job-scraper` reaches for Playwright for its JS-rendered
  sources (`scrapers/nhm.py`); the same approach could be added here per
  event if a specific site needs it.
- **Text-heuristic date guesses aren't verified**: step 3 above picks the
  earliest plausible-looking date in the page text, which can occasionally
  pick up an unrelated date mentioned nearby. A wrong guess gets overwritten
  by the next correct scan, or can be fixed immediately via the dashboard's
  edit form.
- **One Worker, one user**: same as `meal-tracker` — a single shared
  `APP_SECRET`, no real multi-user auth. Fine for a personal app.
