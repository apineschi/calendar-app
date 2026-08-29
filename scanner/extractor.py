import json
import os
import re
from datetime import datetime, timezone
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

# A date-shaped snippet: a month name plus a year within a plausible window -
# used to scan free-text prose once structured data isn't available (the
# common case - see ARCHITECTURE.md).
DATE_SNIPPET_RE = re.compile(
    r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+)?"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"[a-z]*\s*(?:\d{1,2}(?:st|nd|rd|th)?[-–—]?\s*)?(?:\d{1,2}(?:st|nd|rd|th)?)?,?\s*(20\d{2})\b",
    re.IGNORECASE,
)

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


def extract_date_from_text(soup: BeautifulSoup) -> dict:
    text = soup.get_text(" ", strip=True)
    now_year = datetime.now(timezone.utc).year
    candidates = []
    for match in DATE_SNIPPET_RE.finditer(text):
        year = int(match.group(1))
        if now_year <= year <= now_year + 3:
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


def scrape_event(url: str) -> dict:
    """Best-effort extraction of {name, start_date, end_date, location, is_free,
    price_text} from an event page. Any field it can't determine is simply
    absent from the returned dict - callers should never overwrite an
    existing stored value with a missing one. Returning {} (nothing found) is
    a normal, expected outcome for many real event sites, not an error - see
    ARCHITECTURE.md's note on the Glastonbury test case.
    """
    try:
        html = fetch_html(url)
    except requests.RequestException:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    jsonld = extract_from_jsonld(soup)
    og = extract_from_opengraph(soup)
    text = {} if jsonld.get("start_date") else extract_date_from_text(soup)

    result = {}
    for field in FIELDS:
        value = _first_present(field, jsonld, text, og)
        if value is not None:
            result[field] = value

    if not result.get("start_date"):
        ai_result = extract_with_workers_ai(soup)
        for field in FIELDS:
            if field not in result and ai_result.get(field) is not None:
                result[field] = ai_result[field]

    return result
