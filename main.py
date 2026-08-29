import json
import os
import sys
from datetime import date, datetime, timezone

from notify.email import format_alert_digest, format_alert_digest_html
from scanner.alerts import compute_alert
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
    alerts = []

    for record in events.values():
        try:
            found = scrape_event(record["url"])
        except Exception as e:
            print(f"[{record.get('id', record.get('url'))}] scrape FAILED: {e}", file=sys.stderr)
            found = {}

        for field in UPDATABLE_FIELDS:
            if found.get(field) is not None:
                record[field] = found[field]

        if record.get("start_date") and not record.get("last_known_year"):
            record["last_known_year"] = date.fromisoformat(record["start_date"]).year

        record["last_checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        updates, alert_fired = compute_alert(record, today)
        record.update(updates)
        if alert_fired:
            alerts.append(record)

    save_events(events)

    with open(ICS_PATH, "wb") as f:
        f.write(build_calendar(events))

    digest = format_alert_digest(alerts)
    print(digest)

    if alerts:
        with open(EMAIL_HTML_PATH, "w", encoding="utf-8") as f:
            f.write(format_alert_digest_html(alerts))


if __name__ == "__main__":
    main()
