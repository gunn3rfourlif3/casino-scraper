#!/usr/bin/env python3
"""
Generate index.html from data/bonuses.json.

Run on its own with `python build_page.py`, or let scrape.py call it at the end
of a run. The page is a single static file with no dependencies, so it works on
GitHub Pages, Netlify, Vercel or straight off disk.
"""

import html
import json
from pathlib import Path

IN_JSON = Path("data/bonuses.json")
OUT_HTML = Path("index.html")

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Casino welcome bonuses</title>
<style>
  :root {{
    --bg: #ffffff;
    --fg: #16181d;
    --muted: #5c6370;
    --line: #e3e5ea;
    --accent: #1f5fd8;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14161a;
      --fg: #e8eaee;
      --muted: #9aa1ad;
      --line: #2b2f37;
      --accent: #78a6ff;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 24px 16px 48px;
    background: var(--bg);
    color: var(--fg);
    font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  main {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
  a {{ color: var(--accent); }}

  /* Desktop: a real table. */
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 12px 10px; border-bottom: 1px solid var(--line); }}
  th {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
  .casino {{ font-weight: 600; }}
  .basis {{ color: var(--muted); font-size: 0.85rem; }}

  /* Phone: collapse each row into a stacked card with its own labels. */
  @media (max-width: 680px) {{
    thead {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }}
    tr {{ display: block; border: 1px solid var(--line); border-radius: 10px; padding: 4px 12px; margin-bottom: 14px; }}
    td {{ display: flex; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--line); padding: 10px 0; }}
    tr td:last-child {{ border-bottom: 0; }}
    td::before {{ content: attr(data-label); color: var(--muted); font-size: 0.8rem; flex: 0 0 auto; }}
    td > span {{ text-align: right; }}
    .casino {{ font-size: 1.05rem; }}
  }}
  footer {{ margin-top: 32px; color: var(--muted); font-size: 0.8rem; }}
</style>
</head>
<body>
<main>
  <h1>Casino welcome bonuses</h1>
  <p class="meta">
    {count} casinos, scraped from <a href="{source}">{source_label}</a> on {scraped_at}.
    Figures extracted with {model}.
  </p>

  <table>
    <thead>
      <tr>
        <th>Casino</th>
        <th>Welcome bonus</th>
        <th>Wagering</th>
        <th>Min deposit</th>
        <th>Source</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>

  <footer>
    Generated from <code>data/bonuses.json</code> by <code>build_page.py</code>.
    Bonus terms change often &mdash; always check the source page before relying on a figure.
    18+. Please gamble responsibly.
  </footer>
</main>
</body>
</html>
"""

ROW = """      <tr>
        <td class="casino" data-label="Casino"><span>{casino}</span></td>
        <td data-label="Welcome bonus"><span>{bonus}</span></td>
        <td data-label="Wagering"><span>{wagering}</span></td>
        <td data-label="Min deposit"><span>{deposit}</span></td>
        <td data-label="Source"><span><a href="{url}" rel="nofollow noopener">View terms</a></span></td>
      </tr>"""


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else "&mdash;"


def format_wagering(record: dict) -> str:
    multiplier = record.get("wagering_multiplier")
    if multiplier is None:
        return "&mdash;"
    basis = record.get("wagering_basis")
    label = {"bonus": "on bonus", "deposit+bonus": "on deposit + bonus"}.get(basis)
    if label:
        return f'{multiplier}&times; <span class="basis">{label}</span>'
    return f"{multiplier}&times;"


def format_deposit(record: dict) -> str:
    amount = record.get("min_deposit")
    if amount is None:
        return "&mdash;"
    amount = int(amount) if float(amount).is_integer() else amount
    currency = record.get("min_deposit_currency") or ""
    return esc(f"{amount} {currency}".strip())


def build() -> None:
    data = json.loads(IN_JSON.read_text(encoding="utf-8"))
    casinos = data.get("casinos", [])

    rows = "\n".join(
        ROW.format(
            casino=esc(record.get("casino")),
            bonus=esc(record.get("welcome_bonus")),
            wagering=format_wagering(record),
            deposit=format_deposit(record),
            url=esc(record.get("source_url")),
        )
        for record in casinos
    )

    source = data.get("source", "")
    page = TEMPLATE.format(
        count=len(casinos),
        source=esc(source),
        source_label=esc(source.replace("https://", "")),
        scraped_at=esc(data.get("scraped_at", "").replace("T", " ").replace("+00:00", " UTC")),
        model=esc(data.get("model", "a language model")),
        rows=rows,
    )

    OUT_HTML.write_text(page, encoding="utf-8")
    print(f"wrote {OUT_HTML} ({len(casinos)} rows)")


if __name__ == "__main__":
    build()
