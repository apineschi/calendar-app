from datetime import date, timedelta

# How long to wait after an event concludes before giving up on finding next
# year's date and clearing it back to "no date set" - gives the organizer a
# reasonable window to actually publish the new date before this app treats
# the old one as gone.
STALE_GRACE_DAYS = 30


def _months_before(d: date, months: int) -> date:
    year = d.year
    month = d.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def check_staleness(record: dict, today: date) -> tuple[dict, bool]:
    """An event whose end_date is more than STALE_GRACE_DAYS in the past,
    with nothing newer found by then, is cleared back to start_date/end_date
    = null - moving it into the dashboard's "No date set" section instead of
    continuing to display a date that's already happened. Fires the
    "went stale" notification once per transition (deduped via
    went_stale_notified_for_year on the record; main.py clears that dedupe
    flag again once a genuinely new date is eventually found, so the next
    time this same event goes stale it can notify again).

    Returns (updates, notify) - `updates` merges into the record ({} if nothing
    changes); the record itself is never mutated here.
    """
    start = record.get("start_date")
    if not start:
        return {}, False

    end = record.get("end_date") or start
    if today < date.fromisoformat(end) + timedelta(days=STALE_GRACE_DAYS):
        return {}, False

    year = record.get("last_known_year")
    already_notified = record.get("went_stale_notified_for_year") == year
    updates = {"start_date": None, "end_date": None}
    if not already_notified:
        updates["went_stale_notified_for_year"] = year
    return updates, not already_notified


def compute_alert(record: dict, today: date) -> tuple[dict, bool]:
    """Decide whether the "still no date, and we're getting close" alert
    should fire this run - a second, later checkpoint than check_staleness()
    above for the same underlying "waiting on next year's date" situation.
    Only relevant while the event is actually dateless (start_date is null,
    whether it never had one or check_staleness() just cleared it) - relies
    on last_known_year/last_known_month, which main.py keeps up to date
    independently of start_date so this keeps working even after a date is
    cleared.
    """
    last_known_year = record.get("last_known_year")
    last_known_month = record.get("last_known_month")
    if not last_known_year or not last_known_month or record.get("start_date"):
        return {}, False

    target_year = last_known_year + 1
    alert_start = _months_before(date(target_year, last_known_month, 1), 3)
    already_sent = record.get("alert_sent_for_year") == target_year

    if today >= alert_start and not already_sent:
        return {"alert_sent_for_year": target_year}, True

    return {}, False
