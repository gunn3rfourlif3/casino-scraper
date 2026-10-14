#!/usr/bin/env python3
"""
Casino bonus scraper.

Pulls three welcome bonuses from AskGamblers (one public source), uses Claude to
turn the free-text bonus terms into structured fields, and writes data/bonuses.json.
Then regenerates index.html from that JSON.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python scrape.py

Options:
    --no-build     write the JSON but skip regenerating index.html
    --html-dir DIR parse saved .html fixtures instead of hitting the network
                   (used by tests; filenames must match the slugs in TARGETS)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# --- configuration -----------------------------------------------------------

SOURCE = "https://www.askgamblers.com"

# Three casinos, one source. These are AskGamblers' own bonus-detail pages.
TARGETS = [
    "/casino-bonuses/latest/rokubet-casino-bonus-100percent-up-to-500-eur-100-free-spins-2026-10-05",
    "/casino-bonuses/latest/betbeast-casino-bonus-250percent-up-to-1000-eur-250-free-spins-2026-10-13",
    "/casino-bonuses/latest/spinlander-casino-bonus-120percent-up-to-888-eur-2026-10-13",
]

# Identify the bot honestly and leave a contact. Swap the email before real use.
USER_AGENT = "locare-screening-scraper/1.0 (+contact: you@example.com)"

REQUEST_DELAY_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 30
MODEL = os.environ.get("MODEL", "claude-haiku-5-5")

OUT_JSON = Path("data/bonuses.json")

# How much page text to hand the model. Enough to cover the detail block and the
# "General Information" bullets, short enough to stay cheap.
TEXT_BUDGET = 3500


# --- fetching ----------------------------------------------------------------


def robots_allows(url: str) -> bool:
    """Check robots.txt for our user agent before fetching anything."""
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except Exception as exc:
        # Unreadable robots.txt: refuse rather than assume permission.
        print(f"  ! could not read {robots_url}: {exc}", file=sys.stderr)
        return False
    return parser.can_fetch(USER_AGENT, url)


def fetch(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code in (401, 403, 429) or response.status_code >= 500:
        # A block, a rate limit or an outage. Do not retry harder, do not work
        # around it: stop and report so a human can pick a different source.
        raise RuntimeError(
            f"{url} returned HTTP {response.status_code} - treating as a block, not retrying"
        )
    response.raise_for_status()
    return response.text


# --- parsing -----------------------------------------------------------------


def extract_bonus_text(html: str) -> tuple[str, str]:
    """
    Reduce a bonus page to (headline, free-text block).

    Deliberately selector-light: strip the chrome, take the visible text, and
    start from the H1. Class names on a site like this churn; the shape of the
    content does not. The model does the interpreting.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg"]):
        tag.decompose()

    h1 = soup.find("h1")
    headline = h1.get_text(" ", strip=True) if h1 else ""

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)

    if headline:
        # Start at the second occurrence if the H1 also appears in a breadcrumb.
        start = text.find(headline)
        if start > -1:
            text = text[start:]

    return headline, text[:TEXT_BUDGET]


# --- the LLM step ------------------------------------------------------------

EXTRACTION_PROMPT = """\
Below is the visible text of one bonus page from a casino comparison site.

Return ONLY a JSON object, no prose and no code fence, with exactly these keys:

  "casino_name"          string  - the casino, e.g. "Rokubet Casino"
  "welcome_bonus"        string  - the headline offer in the form
                                   "<percent>% up to <amount> <CURRENCY>" plus
                                   ", + N free spins" when free spins are included.
                                   Use the ISO currency code (EUR, USD, CAD), not the symbol.
  "wagering_multiplier"  integer - just the number from the wagering requirement.
                                   "40xb" -> 40. "25x(d+b)" -> 25. Null if none is stated.
  "wagering_basis"       string  - what the multiplier applies to, one of
                                   "bonus", "deposit+bonus", or "unknown".
                                   "xb" means bonus. "x(d+b)" means deposit+bonus.
  "min_deposit_amount"   number  - the minimum qualifying deposit, number only.
  "min_deposit_currency" string  - ISO code for that deposit, e.g. "EUR".
  "max_bonus_amount"     number  - the cap on the bonus, number only. Null if not stated.
  "free_spins"           integer - free spins included, 0 if none.

Rules:
- Use only what the page states. If a field is genuinely absent, use null.
- Do not infer a wagering requirement from a different bonus on the page. The page
  lists other offers from the same casino and similar offers from other casinos;
  ignore all of them and read only the bonus named in the headline.

Headline: {headline}

Page text:
{text}
"""


def extract_fields(headline: str, text: str) -> dict:
    """Hand the free text to Claude and get structured fields back."""
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("anthropic package missing - run: pip install -r requirements.txt")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set - the extraction step needs it")

    client = Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[
            {
                "role": "user",
                "content": EXTRACTION_PROMPT.format(headline=headline, text=text),
            }
        ],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"model did not return valid JSON: {exc}\n{raw[:400]}") from exc


# --- validation --------------------------------------------------------------


def validate(record: dict, url: str) -> list[str]:
    """
    Sanity-check what came back. An LLM that misreads a page usually fails loudly
    here rather than quietly writing a wrong number into the JSON.
    """
    problems = []

    if not record.get("casino_name"):
        problems.append("casino_name is empty")

    multiplier = record.get("wagering_multiplier")
    if multiplier is not None and not (0 <= multiplier <= 150):
        problems.append(f"wagering_multiplier {multiplier} outside plausible range 0-150")

    deposit = record.get("min_deposit_amount")
    if deposit is not None and not (0 < deposit <= 100_000):
        problems.append(f"min_deposit_amount {deposit} outside plausible range")

    if record.get("wagering_basis") not in ("bonus", "deposit+bonus", "unknown", None):
        problems.append(f"unexpected wagering_basis {record.get('wagering_basis')!r}")

    # The headline percentage should appear in the source URL slug. Cheap check
    # that the model read the right bonus and not a neighbouring card.
    bonus = record.get("welcome_bonus") or ""
    match = re.match(r"(\d+)%", bonus)
    if match and f"{match.group(1)}percent" not in url:
        problems.append(
            f"welcome_bonus starts {match.group(1)}% which is not in the source URL - "
            "possible cross-contamination from another offer on the page"
        )

    return problems


# --- main --------------------------------------------------------------------


def scrape_one(path: str, html_dir: Path | None) -> dict:
    url = SOURCE + path
    print(f"- {url}")

    if html_dir:
        slug = path.rsplit("/", 1)[-1]
        html = (html_dir / f"{slug}.html").read_text(encoding="utf-8")
    else:
        if not robots_allows(url):
            raise RuntimeError(f"robots.txt disallows {url} for {USER_AGENT}")
        html = fetch(url)
        time.sleep(REQUEST_DELAY_SECONDS)

    headline, text = extract_bonus_text(html)
    if not headline:
        raise RuntimeError(f"no H1 found on {url} - page layout may have changed")

    fields = extract_fields(headline, text)

    problems = validate(fields, url)
    if problems:
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)

    return {
        "casino": fields.get("casino_name"),
        "welcome_bonus": fields.get("welcome_bonus"),
        "wagering_multiplier": fields.get("wagering_multiplier"),
        "wagering_basis": fields.get("wagering_basis"),
        "min_deposit": fields.get("min_deposit_amount"),
        "min_deposit_currency": fields.get("min_deposit_currency"),
        "max_bonus": fields.get("max_bonus_amount"),
        "free_spins": fields.get("free_spins"),
        "source_url": url,
        "validation_warnings": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--html-dir", type=Path)
    args = parser.parse_args()

    print(f"Scraping {len(TARGETS)} bonuses from {SOURCE}")

    records, failures = [], []
    for path in TARGETS:
        try:
            records.append(scrape_one(path, args.html_dir))
        except Exception as exc:
            print(f"  ! FAILED: {exc}", file=sys.stderr)
            failures.append({"path": path, "error": str(exc)})

    if not records:
        print("nothing scraped, leaving existing JSON alone", file=sys.stderr)
        return 1

    payload = {
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": MODEL,
        "casinos": records,
        "failures": failures,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT_JSON} ({len(records)} casinos, {len(failures)} failed)")

    if not args.no_build:
        import build_page

        build_page.build()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
