from datetime import date as date_cls, datetime, timedelta, timezone

from icalendar import Calendar, Event


def build_calendar(events: dict) -> bytes:
    """One VEVENT per event that has a known start_date. UID is the stable
    calendar_uid stored on each record, so re-generating this file on every
    scan updates existing entries in Google Calendar rather than duplicating
    them on its next poll. Events with no date yet simply don't appear here -
    they still show on the dashboard, just marked "pending".
    """
    cal = Calendar()
    cal.add("prodid", "-//calendar-app//apineschi//")
    cal.add("version", "2.0")

    for record in events.values():
        start = record.get("start_date")
        if not start:
            continue

        vevent = Event()
        vevent.add("uid", record["calendar_uid"])
        vevent.add("summary", record.get("name") or "Untitled event")
        vevent.add("dtstart", date_cls.fromisoformat(start))
        # DTEND is exclusive per RFC 5545, so an inclusive last day needs +1.
        end_str = record.get("end_date") or start
        vevent.add("dtend", date_cls.fromisoformat(end_str) + timedelta(days=1))
        vevent.add("dtstamp", datetime.now(timezone.utc))

        if record.get("location"):
            vevent.add("location", record["location"])

        description_lines = []
        if record.get("is_free") is True:
            description_lines.append("Free")
        elif record.get("price_text"):
            description_lines.append(record["price_text"])
        if record.get("notes"):
            description_lines.append(record["notes"])
        if record.get("url"):
            description_lines.append(record["url"])
        if description_lines:
            vevent.add("description", "\n".join(description_lines))

        cal.add_component(vevent)

    return cal.to_ical()
