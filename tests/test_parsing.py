"""
Tests for the parts of scrape.py that don't need the network or the API:
the HTML reduction and the post-extraction validation.

    python -m pytest tests/ -q      (or: python tests/test_parsing.py)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrape import extract_bonus_text, validate  # noqa: E402

FIXTURE = """
<html><body>
<nav><a href="/casino-bonuses">Casino Bonuses</a><a href="/latest">Latest</a></nav>
<header>THIS WEEK'S HOTTEST CASINOS</header>
<script>var tracking = {"wagering":"999x"};</script>
<main>
  <h1>100% up to &euro;500 + 100 Free Spins at Rokubet Casino, 1st Deposit</h1>
  <div>Minimum Deposit &euro;15</div>
  <div>Wagering Requirements 40xb</div>
  <div>Bonus Percentage 100%</div>
  <div>Maximum Bonus Amount &euro;500</div>
  <h2>General Information</h2>
  <ul>
    <li>You can withdraw your winnings only after the bonus has been wagered 40 times.</li>
    <li>Deposit at least &euro;15 to qualify for the offer.</li>
  </ul>
</main>
<footer>&copy; 2026 AskGamblers.com</footer>
</body></html>
"""


def test_extracts_headline():
    headline, _ = extract_bonus_text(FIXTURE)
    assert "Rokubet Casino" in headline
    assert "100%" in headline


def test_drops_chrome_and_scripts():
    _, text = extract_bonus_text(FIXTURE)
    assert "999x" not in text, "script contents leaked into the model input"
    assert "HOTTEST CASINOS" not in text
    assert "AskGamblers.com" not in text


def test_keeps_the_fields_that_matter():
    _, text = extract_bonus_text(FIXTURE)
    for expected in ("Minimum Deposit", "40xb", "wagered 40 times"):
        assert expected in text


def test_validate_accepts_a_good_record():
    record = {
        "casino_name": "Rokubet Casino",
        "welcome_bonus": "100% up to 500 EUR, + 100 free spins",
        "wagering_multiplier": 40,
        "wagering_basis": "bonus",
        "min_deposit_amount": 15,
    }
    url = "https://www.askgamblers.com/casino-bonuses/latest/rokubet-casino-bonus-100percent-up-to-500-eur-100-free-spins-2026-10-05"
    assert validate(record, url) == []


def test_validate_catches_the_wrong_bonus_on_the_page():
    """The real failure mode: the model reads a neighbouring offer card."""
    record = {
        "casino_name": "Rokubet Casino",
        "welcome_bonus": "110% up to 750 EUR",  # this is the 2nd-deposit offer
        "wagering_multiplier": 40,
        "wagering_basis": "bonus",
        "min_deposit_amount": 15,
    }
    url = "https://www.askgamblers.com/casino-bonuses/latest/rokubet-casino-bonus-100percent-up-to-500-eur-100-free-spins-2026-10-05"
    problems = validate(record, url)
    assert any("cross-contamination" in p for p in problems)


def test_validate_catches_implausible_numbers():
    record = {
        "casino_name": "X",
        "welcome_bonus": "",
        "wagering_multiplier": 4000,
        "wagering_basis": "sideways",
        "min_deposit_amount": 0,
    }
    problems = validate(record, "https://example.com/x")
    assert len(problems) == 3


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    print("all passed" if not failures else f"{failures} failed")
    sys.exit(1 if failures else 0)
