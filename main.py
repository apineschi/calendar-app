import json
import os
import sys
from datetime import date, datetime, timezone

from notify.email import format_alert_digest, format_alert_digest_html
from scanner.alerts import check_staleness, compute_alert
from scanner.extractor import scrape_event
from scanner.ics import build_calendar

ROOT = os.path.dirname(os.path.abspath(__file__))
EVENTS_PATH = os.path.join(ROOT, "docs", "events.json")
ICS_PATH = os.path.join(ROOT, "docs", "calendar.ics")
EMAIL_HTML_PATH = os.path.join(ROOT, "email_digest.html")

# Fields a fresh scrape is allowed to write. Deliberately excludes "name" -
# that's user-controlled (set at creation or edited later), and letting a
# monthly scan overwrite it with whatever the page's og:title/JSON-LD name
# happens to be (often noisy, e.g. "Home - Glastonbury Festivals") would
# clobber a name you specifically chose. A missing/null field from
# scrape_event() never overwrites what's already stored either way -
# "couldn't find it this time" is not the same as "it's now unknown".
UPDATABLE_FIELDS = ("start_date", "end_date", "location", "is_free", "price_text")


def load_events() -> dict:
    if not os.path.exists(EVENTS_PATH):
        return {}
    with open(EVENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_events(events: dict) -> None:
    with open(EVENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)


def main():
    events = load_events()
    today = datetime.now(timezone.utc).date()
    stale_alerts = []
    upcoming_alerts = []

    for record in events.values():
        try:
            found = scrape_event(record["url"])
        except Exception as e:
            print(f"[{record.get('id', record.get('url'))}] scrape FAILED: {e}", file=sys.stderr)
            found = {}

        for field in UPDATABLE_FIELDS:
            if found.get(field) is not None:
                record[field] = found[field]

        # last_known_year/month are the persistent memory of "when this
        # event last happened" - unlike start_date/end_date, they're never
        # cleared by check_staleness() below, so compute_alert() can keep
        # working out the right alert window even once the actual date is
        # gone. A rollover to a genuinely new year clears both notification
        # dedupe flags, so the next cycle's staleness/alert can fire again.
        if record.get("start_date"):
            found_year = date.fromisoformat(record["start_date"]).year
            if not record.get("last_known_year") or found_year > record["last_known_year"]:
                record["last_known_year"] = found_year
                record["last_known_month"] = date.fromisoformat(record["start_date"]).month
                record["alert_sent_for_year"] = None
                record["went_stale_notified_for_year"] = None

        record["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        stale_updates, stale_notify = check_staleness(record, today)
        record.update(stale_updates)
        if stale_notify:
            stale_alerts.append(record)

        alert_updates, alert_fired = compute_alert(record, today)
        record.update(alert_updates)
        if alert_fired:
            upcoming_alerts.append(record)

    save_events(events)

    with open(ICS_PATH, "wb") as f:
        f.write(build_calendar(events))

    digest = format_alert_digest(stale_alerts, upcoming_alerts)
    print(digest)

    if stale_alerts or upcoming_alerts:
        with open(EMAIL_HTML_PATH, "w", encoding="utf-8") as f:
            f.write(format_alert_digest_html(stale_alerts, upcoming_alerts))


if __name__ == "__main__":
    main()
