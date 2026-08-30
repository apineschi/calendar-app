import json
import os
import re
from datetime import date as date_cls, datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# schema.org's Event subtypes (MusicEvent, SportsEvent, ...) all end in
# "Event"; "Festival" isn't an official schema.org type but shows up on real
# sites anyway, so it's matched too.
EVENT_TYPE_MARKERS = ("Event", "Festival")

# Month names AND common abbreviations - a real test against
# profounddecisions.co.uk ("Autumn Equinox - 11th Sep to 13th Sep 2026")
# found genuinely-present dates going completely undetected because the site
# uses 3-letter abbreviations, which none of the regexes below originally
# matched at all (they only recognized full month names). Matching is done
# by capturing any word-like token and resolving it through this lookup,
# rather than baking every spelling into each regex's alternation.
MONTH_LOOKUP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _parse_month(word: str) -> Optional[int]:
    return MONTH_LOOKUP.get(word.strip(". ").lower())


# A single day + ordinal suffix, tolerant of a stray space between them
# (e.g. "11 th") - the profounddecisions.co.uk page renders ordinal suffixes
# in a <sup> tag, which BeautifulSoup's get_text(" ", ...) then separates
# from its digit with a space, breaking a naive "\d{1,2}(?:st|nd|rd|th)?"
# pattern that assumes no gap.
_DAY_RE = r"\d{1,2}\s*(?:st|nd|rd|th)?"
_MONTH_WORD_RE = r"[A-Za-z]{3,9}\.?"

# A date-shaped snippet: a month name/abbreviation plus a year within a
# plausible window - used to scan free-text prose once structured data isn't
# available (the common case - see ARCHITECTURE.md).
DATE_SNIPPET_RE = re.compile(
    rf"\b(?:{_DAY_RE}\s+)?({_MONTH_WORD_RE})\s*"
    rf"(?:{_DAY_RE}[-–—]?\s*)?(?:{_DAY_RE})?,?\s*(20\d{{2}})\b"
)

DATE_CONTEXT_WINDOW = 80
TICKET_CONTEXT_RE = re.compile(
    r"ticket|early\s*bird|on\s*sale|sold\s*out|deadline|book\s*by|last\s*chance|closing\s*date|expires",
    re.IGNORECASE,
)

# Two distinct multi-day range phrasings turned up from real sites, each
# missed entirely by the other: "2-4 July 2027" (lovesupremefestival.com,
# one shared month) versus "11th Sep to 13th Sep 2026"
# (profounddecisions.co.uk, the month repeated with each day). Both are
# tried; RANGE_DIFF_MONTH_RE also tolerates the two days naming different
# months (a range spanning a month boundary), so long as they land in the
# same year - see extract_date_range_from_text for that guard.
RANGE_SAME_MONTH_RE = re.compile(
    rf"\b({_DAY_RE})\s*(?:-|–|—|to)\s*({_DAY_RE})\s+({_MONTH_WORD_RE})\s+(20\d{{2}})\b"
)
RANGE_DIFF_MONTH_RE = re.compile(
    rf"\b({_DAY_RE})\s+({_MONTH_WORD_RE})\s+to\s+({_DAY_RE})\s+({_MONTH_WORD_RE})\s+(20\d{{2}})\b"
)


def _leading_int(text: str) -> Optional[int]:
    match = re.match(r"\d{1,2}", text)
    return int(match.group(0)) if match else None

WORKERS_AI_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

WORKERS_AI_SCHEMA = {
    "type": "object",
    "properties": {
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "is_free": {"type": ["boolean", "null"]},
        "price_text": {"type": ["string", "null"]},
    },
    "required": ["start_date", "end_date", "location", "is_free", "price_text"],
}


def fetch_html(url: str, timeout: int = 20) -> str:
    resp = requests.get(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_html_with_browser(url: str, timeout: int = 30) -> Optional[str]:
    """Fallback for pages a plain `requests` call can't get: sites that only
    render their content client-side (nothing useful in the raw HTML), and -
    to a point - sites that block non-browser requests outright. Tried only
    after fetch_html() itself fails, mirroring job-scraper's approach of
    reaching for Playwright specifically for JS-rendered sources.

    Real-world limit found testing this against womad.co.uk: it returns the
    identical 403 Forbidden page to headless Chromium as it does to a plain
    `requests` call - and to a bare `robots.txt` request too, which needs no
    browser or JS execution at all to serve. That rules out fingerprinting
    as the trigger; it's a wholesale block on the IP range this runs from
    (GitHub Actions' and this sandbox's are both in cloud/datacenter
    ranges), which no client-side change here can fix. See
    fetch_html_from_archive() for the fallback that actually helps with
    that case, by fetching from a different IP entirely.
    Returns None (never raises) on any failure, including Playwright/its
    browser binaries not being installed, since this is already the last
    resort before giving up on the page.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
                page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                return page.content()
            finally:
                browser.close()
    except Exception:
        return None


def fetch_html_from_archive(url: str, timeout: int = 20) -> Optional[str]:
    """Last-resort alternate source: the Wayback Machine's own most recent
    crawl of the page, fetched from archive.org's infrastructure rather than
    ours. This is the fallback that actually helps with a wholesale
    IP-range block like womad.co.uk's (see fetch_html_with_browser()) -
    archive.org already has a copy from a fetch that succeeded on its own
    IPs, regardless of what's blocking this app's. The tradeoff is
    freshness: whatever comes back is only as recent as archive.org's last
    crawl of that page, which could be old or (for an obscure site) may not
    exist at all. Tried only after the live page and a real browser have
    both failed to find a date. Returns None (never raises) on any failure.
    """
    try:
        avail = requests.get(
            "https://archive.org/wayback/available",
            params={"url": url},
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=timeout,
        )
        avail.raise_for_status()
        snapshot = avail.json().get("archived_snapshots", {}).get("closest")
        if not snapshot or not snapshot.get("available") or not snapshot.get("url"):
            return None
        # The plain snapshot URL replays the page with a Wayback Machine
        # toolbar injected on top, whose "captured on ..." banner text could
        # itself be misread as the event's date by the text heuristics.
        # Inserting "id_" after the timestamp fetches the raw capture with
        # no banner, per archive.org's own convention for this.
        snapshot_url = re.sub(r"(/web/\d+)/", r"\1id_/", snapshot["url"], count=1)
    except (requests.RequestException, ValueError, KeyError):
        return None

    try:
        return fetch_html(snapshot_url, timeout=timeout)
    except requests.RequestException:
        return None


def _iter_jsonld_nodes(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        nodes = data.get("@graph") if isinstance(data, dict) and "@graph" in data else data
        if isinstance(nodes, dict):
            nodes = [nodes]
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if isinstance(node, dict):
                yield node


def _is_event_node(node: dict) -> bool:
    node_type = node.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    return any(isinstance(t, str) and any(marker in t for marker in EVENT_TYPE_MARKERS) for t in types)


def _location_text(node: dict) -> Optional[str]:
    loc = node.get("location")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, str):
        return loc
    if not isinstance(loc, dict):
        return None
    name = loc.get("name") if isinstance(loc.get("name"), str) else None
    address = loc.get("address")
    if isinstance(address, dict):
        addr_bits = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
        addr_text = ", ".join(b for b in addr_bits if b)
        if name and addr_text:
            return f"{name}, {addr_text}"
        return name or addr_text or None
    if isinstance(address, str):
        return f"{name}, {address}" if name else address
    return name


def _price_fields(node: dict) -> dict:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    result = {}
    if "isAccessibleForFree" in node:
        result["is_free"] = bool(node["isAccessibleForFree"])
    if not isinstance(offers, dict):
        return result
    price = offers.get("price")
    currency = offers.get("priceCurrency", "")
    if price is not None:
        result["price_text"] = f"{currency} {price}".strip()
        result.setdefault("is_free", str(price) in ("0", "0.0", "0.00"))
    return result


def _parse_iso_date(value) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dateutil_parser.isoparse(value).date().isoformat()
    except (ValueError, OverflowError):
        try:
            return dateutil_parser.parse(value, fuzzy=True).date().isoformat()
        except (ValueError, OverflowError, TypeError):
            return None


def extract_from_jsonld(soup: BeautifulSoup) -> dict:
    for node in _iter_jsonld_nodes(soup):
        if not _is_event_node(node):
            continue
        result = {
            "name": node.get("name") if isinstance(node.get("name"), str) else None,
            "start_date": _parse_iso_date(node.get("startDate")),
            "end_date": _parse_iso_date(node.get("endDate")),
            "location": _location_text(node),
        }
        result.update(_price_fields(node))
        if result.get("start_date"):
            return result
    return {}


def extract_from_opengraph(soup: BeautifulSoup) -> dict:
    tag = soup.find("meta", property="og:title")
    title = tag.get("content") if tag else None
    return {"name": title} if title else {}


LOCATION_STOP_WORDS_RE = re.compile(
    r"\b(previous|line-?up|tickets?|book|home|about|news|menu|faq|shop|"
    r"gallery|contact|sign\s*up|subscribe|wellness|families|restaurant|"
    r"skip\s*to(\s*main)?(\s*content)?)\b",
    re.IGNORECASE,
)


def _guess_location_after(text: str, end_pos: int) -> Optional[str]:
    """Best-effort only: on many event pages the venue name sits right after
    the date (as on lovesupremefestival.com: "2-4 July 2027 Glynde Place,
    East Sussex"), with no punctuation separating them from the next section
    of the page once get_text() collapses all whitespace to single spaces.
    Cuts at the first digit or word that looks like unrelated site chrome
    (nav links, section headings) rather than part of a place name - a wrong
    guess just gets left as-is or corrected via the dashboard's edit form,
    same as any other best-effort field here.
    """
    snippet = text[end_pos:end_pos + 80]
    stop_match = LOCATION_STOP_WORDS_RE.search(snippet)
    if stop_match:
        snippet = snippet[:stop_match.start()]
    digit_match = re.search(r"\d", snippet)
    if digit_match:
        snippet = snippet[:digit_match.start()]
    # readingfestival.com puts a "|" separator between the date and the
    # venue in the flattened text ("...2026 | Richfield Avenue, Reading
    # Skip to main content"), which the stop-word/digit cuts above don't
    # touch since it comes before the venue name, not after it.
    snippet = snippet.strip(" ,.-|")
    return snippet if 3 <= len(snippet) <= 80 else None


def _in_range_year(year: int) -> bool:
    now_year = datetime.now(timezone.utc).year
    return now_year <= year <= now_year + 3


def _ticket_context(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - DATE_CONTEXT_WINDOW):end]
    return bool(TICKET_CONTEXT_RE.search(context))


def _range_candidates_same_month(text: str):
    for match in RANGE_SAME_MONTH_RE.finditer(text):
        day1_text, day2_text, month_word, year_str = match.groups()
        year = int(year_str)
        month = _parse_month(month_word)
        if month is None or not _in_range_year(year) or _ticket_context(text, match.start(), match.end()):
            continue
        day1, day2 = _leading_int(day1_text), _leading_int(day2_text)
        try:
            start = date_cls(year, month, day1)
            end = date_cls(year, month, day2)
        except (ValueError, TypeError):
            continue
        if end < start:
            continue
        yield start, end, match.end()


def _range_candidates_diff_month(text: str):
    for match in RANGE_DIFF_MONTH_RE.finditer(text):
        day1_text, month1_word, day2_text, month2_word, year_str = match.groups()
        year = int(year_str)
        month1, month2 = _parse_month(month1_word), _parse_month(month2_word)
        if None in (month1, month2) or not _in_range_year(year) or _ticket_context(text, match.start(), match.end()):
            continue
        day1, day2 = _leading_int(day1_text), _leading_int(day2_text)
        try:
            start = date_cls(year, month1, day1)
            end = date_cls(year, month2, day2)
        except (ValueError, TypeError):
            continue
        if end < start:
            continue
        yield start, end, match.end()


def extract_date_range_from_text(soup: BeautifulSoup) -> dict:
    text = soup.get_text(" ", strip=True)
    candidates = list(_range_candidates_same_month(text)) + list(_range_candidates_diff_month(text))

    if not candidates:
        return {}
    candidates.sort(key=lambda c: (c[0], c[1]))
    start, end, match_end = candidates[0]
    result = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    location = _guess_location_after(text, match_end)
    if location:
        result["location"] = location
    return result


def extract_date_from_text(soup: BeautifulSoup) -> dict:
    """A real test against lovesupremefestival.com found this heuristic
    confidently picking up "Early Bird Tickets Only until 7th August 2026" -
    a ticket-sale deadline, not the festival's actual date. Nearby ticket/
    sale language is common on event sites and easy to mistake for the event
    date itself, so any date-shaped snippet close to one of those words is
    skipped rather than trusted.

    The same real page also demonstrated why a day *range* ("2-4 July 2027")
    needs its own pass first (extract_date_range_from_text): the plain
    single-day pattern below would only ever catch the trailing day of a
    range, silently dropping the real start date. A second real site
    (profounddecisions.co.uk, "11th Sep to 13th Sep 2026") showed a second
    range phrasing exists too (month repeated per day, not shared) - both are
    tried in extract_date_range_from_text before falling back to this
    single-day scan.
    """
    range_result = extract_date_range_from_text(soup)
    if range_result:
        return range_result

    text = soup.get_text(" ", strip=True)
    candidates = []
    for match in DATE_SNIPPET_RE.finditer(text):
        month_word, year_str = match.groups()
        year = int(year_str)
        if _parse_month(month_word) is None or not _in_range_year(year):
            continue
        if _ticket_context(text, match.start(), match.end()):
            continue
        try:
            parsed = dateutil_parser.parse(match.group(0), fuzzy=True, dayfirst=True).date()
            candidates.append(parsed)
        except (ValueError, OverflowError, TypeError):
            continue
    if not candidates:
        return {}
    candidates.sort()
    return {"start_date": candidates[0].isoformat()}


def extract_with_workers_ai(soup: BeautifulSoup) -> dict:
    """Last-resort fallback when structured data and text heuristics both find
    nothing: ask a free Cloudflare Workers AI model to read the page's own
    text. No-ops unless CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN are set -
    deliberately Workers AI, not the Anthropic API, to keep this at $0 (see
    ARCHITECTURE.md's "zero paid infrastructure" section).
    """
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not account_id or not api_token:
        return {}

    page_text = soup.get_text(" ", strip=True)[:6000]
    today = datetime.now(timezone.utc).date().isoformat()
    prompt = (
        f"Today's date is {today}. Extract this event's next occurrence from the "
        "page text below. If a field genuinely isn't stated, use null - never guess.\n\n"
        f"PAGE TEXT:\n{page_text}"
    )

    try:
        resp = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{WORKERS_AI_MODEL}",
            headers={"Authorization": f"Bearer {api_token}"},
            json={
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema", "json_schema": WORKERS_AI_SCHEMA},
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["result"]["response"]
        if isinstance(data, str):
            data = json.loads(data)
    except (requests.RequestException, KeyError, ValueError):
        return {}

    return {k: v for k, v in data.items() if v is not None}


FIELDS = ("name", "start_date", "end_date", "location", "is_free", "price_text")


def _first_present(key: str, *dicts: dict):
    for d in dicts:
        if key in d and d[key] is not None:
            return d[key]
    return None


def _extract_deterministic(soup: BeautifulSoup) -> dict:
    jsonld = extract_from_jsonld(soup)
    og = extract_from_opengraph(soup)
    text = {} if jsonld.get("start_date") else extract_date_from_text(soup)

    result = {}
    for field in FIELDS:
        value = _first_present(field, jsonld, text, og)
        if value is not None:
            result[field] = value
    return result


def _merge_from_html(html: Optional[str], result: dict) -> Optional[BeautifulSoup]:
    """If html is present, run the deterministic extraction pass against it
    and merge any fields `result` is still missing into it in place (never
    overwriting a field another source already found). Returns the parsed
    soup - kept around so a later Workers AI pass can use the richest page
    fetched so far - or None if there was no html to parse.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    found = _extract_deterministic(soup)
    for field in FIELDS:
        if field not in result and found.get(field) is not None:
            result[field] = found[field]
    return soup


def scrape_event(url: str) -> dict:
    """Best-effort extraction of {name, start_date, end_date, location, is_free,
    price_text} from an event page. Any field it can't determine is simply
    absent from the returned dict - callers should never overwrite an
    existing stored value with a missing one. Returning {} (nothing found) is
    a normal, expected outcome for many real event sites, not an error - see
    ARCHITECTURE.md's note on the Glastonbury test case.

    Three fetch attempts are tried in order, each only if the previous ones
    haven't found a date yet: the plain page, a real headless browser (helps
    with JS-rendered pages), then archive.org's own last crawl of the page
    (helps with sites that wholesale block this app's IP range, which the
    browser retry can't - see fetch_html_with_browser()'s docstring). All
    three run the same deterministic checks; the final Workers AI pass (if
    configured) uses whichever of them returned a page at all, preferring
    the richest one fetched.
    """
    result = {}
    best_soup = None

    try:
        html = fetch_html(url)
    except requests.RequestException:
        html = None
    soup = _merge_from_html(html, result)
    if soup is not None:
        best_soup = soup

    if not result.get("start_date"):
        soup = _merge_from_html(fetch_html_with_browser(url), result)
        if soup is not None:
            best_soup = soup

    if not result.get("start_date"):
        soup = _merge_from_html(fetch_html_from_archive(url), result)
        if soup is not None:
            best_soup = soup

    if not result.get("start_date") and best_soup is not None:
        ai_result = extract_with_workers_ai(best_soup)
        for field in FIELDS:
            if field not in result and ai_result.get(field) is not None:
                result[field] = ai_result[field]

    return result
