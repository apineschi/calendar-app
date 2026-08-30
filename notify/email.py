import html as html_lib


def _esc(value) -> str:
    return html_lib.escape(str(value))


def _card_html(record: dict, headline: str) -> str:
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:#fdf1e0; border-radius:8px; margin-bottom:12px;">
      <tr><td style="padding:14px 18px; font-family:Arial,Helvetica,sans-serif; color:#222;">
        <div style="font-size:16px; font-weight:bold; margin:0 0 8px;">
          <a href="{_esc(record.get('url', ''))}" style="color:#111; text-decoration:none;">{_esc(record.get('name', 'Unknown event'))}</a>
        </div>
        <div style="font-size:13px; line-height:1.5;">{headline}</div>
      </td></tr>
    </table>"""


def format_alert_digest_html(stale_records: list, upcoming_records: list) -> str:
    """HTML digest covering both notification points in an event's
    "waiting on next year's date" lifecycle (see scanner/alerts.py):
    stale_records just had their date cleared back to "no date set" because
    the event concluded over a month ago with nothing newer found;
    upcoming_records are still dateless and now within 3 months of when the
    event is expected. Mirrors job-scraper/notify/email.py's structure.
    """
    if not stale_records and not upcoming_records:
        return "<p>No alerts.</p>"

    sections = []
    if stale_records:
        cards = [
            _card_html(r, f"Ended over a month ago and no {(r.get('last_known_year') or 0) + 1} date has been found yet - moved to \"No date set\".")
            for r in stale_records
        ]
        sections.append(f"""
        <p style="font-size:14px;"><strong>{len(stale_records)} event{'s' if len(stale_records) != 1 else ''} just went stale</strong></p>
        {"".join(cards)}""")
    if upcoming_records:
        cards = [
            _card_html(r, f"Last known year: {_esc(r.get('last_known_year'))}. Still no date found for {(r.get('last_known_year') or 0) + 1} as of {_esc(r.get('last_checked', ''))}.")
            for r in upcoming_records
        ]
        sections.append(f"""
        <p style="font-size:14px;"><strong>{len(upcoming_records)} event{'s' if len(upcoming_records) != 1 else ''} still need{'s' if len(upcoming_records) == 1 else ''} attention</strong></p>
        {"".join(cards)}""")

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;">{"".join(sections)}</div>"""


def format_alert_digest(stale_records: list, upcoming_records: list) -> str:
    """Plain-text digest, used for the console/log output the GitHub Actions
    workflow greps for "FOUND" to decide whether to send an email at all.
    """
    total = len(stale_records) + len(upcoming_records)
    if not total:
        return "No alerts this run."

    lines = [f"--- FOUND {total} EVENT(S) NEEDING ATTENTION ---", ""]
    for record in stale_records:
        target_year = (record.get("last_known_year") or 0) + 1
        lines.append(f"EVENT: {record.get('name', 'Unknown event')}")
        lines.append("STATUS: Just went stale - moved to No date set")
        lines.append(f"STILL NO DATE FOR: {target_year}")
        lines.append(f"LINK: {record.get('url', '')}")
        lines.append("-" * 30)
    for record in upcoming_records:
        target_year = (record.get("last_known_year") or 0) + 1
        lines.append(f"EVENT: {record.get('name', 'Unknown event')}")
        lines.append("STATUS: Still no date, getting close to expected month")
        lines.append(f"LAST KNOWN YEAR: {record.get('last_known_year')}")
        lines.append(f"STILL NO DATE FOR: {target_year}")
        lines.append(f"LINK: {record.get('url', '')}")
        lines.append("-" * 30)
    return "\n".join(lines)
