import html as html_lib


def format_alert_digest_html(records: list) -> str:
    """HTML digest of events whose next date couldn't be found in time, for
    the email action's html_body. Mirrors job-scraper/notify/email.py's
    format_digest_html() structure.
    """
    if not records:
        return "<p>No alerts.</p>"

    def esc(value) -> str:
        return html_lib.escape(str(value))

    cards = []
    for record in records:
        target_year = (record.get("last_known_year") or 0) + 1
        cards.append(f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="background:#fdf1e0; border-radius:8px; margin-bottom:12px;">
          <tr><td style="padding:14px 18px; font-family:Arial,Helvetica,sans-serif; color:#222;">
            <div style="font-size:16px; font-weight:bold; margin:0 0 8px;">
              <a href="{esc(record.get('url', ''))}" style="color:#111; text-decoration:none;">{esc(record.get('name', 'Unknown event'))}</a>
            </div>
            <div style="font-size:13px; line-height:1.5;">
              Last known year: {esc(record.get('last_known_year'))}<br>
              Still no date found for {esc(target_year)} as of {esc(record.get('last_checked', ''))}.
            </div>
          </td></tr>
        </table>""")

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;">
      <p style="font-size:14px;"><strong>{len(records)} event{'s' if len(records) != 1 else ''} need{'s' if len(records) == 1 else ''} attention</strong></p>
      {"".join(cards)}
    </div>"""


def format_alert_digest(records: list) -> str:
    """Plain-text digest, used for the console/log output the GitHub Actions
    workflow greps for "FOUND" to decide whether to send an email at all.
    """
    if not records:
        return "No alerts this run."

    lines = [f"--- FOUND {len(records)} EVENT(S) NEEDING ATTENTION ---", ""]
    for record in records:
        target_year = (record.get("last_known_year") or 0) + 1
        lines.append(f"EVENT: {record.get('name', 'Unknown event')}")
        lines.append(f"LAST KNOWN YEAR: {record.get('last_known_year')}")
        lines.append(f"STILL NO DATE FOR: {target_year}")
        lines.append(f"LINK: {record.get('url', '')}")
        lines.append("-" * 30)
    return "\n".join(lines)
