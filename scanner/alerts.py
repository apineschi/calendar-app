from datetime import date


def _months_before(d: date, months: int) -> date:
    year = d.year
    month = d.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def compute_alert(record: dict, today: date) -> tuple[dict, bool]:
    """Decide whether this event's "can't find next year's date" alert should
    fire (or clear) this run. Returns (updates, alert_fired) - `updates` is a
    dict of fields to merge into the record ({} if nothing changes); the
    record itself is never mutated here.

    An event only has a `last_known_year` once a date has actually been
    found for it at least once - `start_date` is never cleared once set (see
    main.py's merge policy), so deriving the recurring month from the current
    `start_date` is always safe whenever `last_known_year` is present.
    """
    last_known_year = record.get("last_known_year")
    start_date = record.get("start_date")
    if not last_known_year or not start_date:
        return {}, False

    current = date.fromisoformat(start_date)

    if current.year > last_known_year:
        updates = {"last_known_year": current.year}
        if record.get("alert_sent_for_year"):
            updates["alert_sent_for_year"] = None
        return updates, False

    target_year = last_known_year + 1
    alert_start = _months_before(date(target_year, current.month, 1), 3)
    already_sent = record.get("alert_sent_for_year") == target_year

    if today >= alert_start and not already_sent:
        return {"alert_sent_for_year": target_year}, True

    return {}, False
