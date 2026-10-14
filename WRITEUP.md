# Taking this to production: 200 casinos, updated daily

## Where it runs

One Hetzner CPX21 (3 vCPU / 4 GB / 80 GB, Falkenstein), Ubuntu 24.04, everything in Docker
Compose: the scraper, Postgres, Caddy. 200 pages a day is roughly four minutes of work —
this is a small job wearing a big word, and a single box I can SSH into beats a Lambda fan-out
I have to reconstruct from CloudWatch when a selector breaks. The role owns the full stack, so
the fewer moving parts between me and a broken scrape, the better. Scale-out point: if the
crawl grows past a few thousand pages or needs residential egress, the fetch step moves to a
queue (Redis + RQ) with workers I can add, while Postgres and the site stay where they are.

## Database and backups

Postgres 17. Three tables: `casinos`, `bonuses` (current state, one row per casino), and
`bonus_snapshots` (append-only, one row per scrape per casino). The snapshot table is the
point — it makes "this casino quietly moved from 35× to 45×" a query rather than a lost fact,
and it gives you a diff to show affiliates.

Backups: nightly `pg_dump` piped into [restic](https://restic.net) to Backblaze B2, 30 daily
and 6 monthly snapshots, plus WAL archiving to the same bucket for point-in-time recovery.
A weekly cron restores the newest backup into a throwaway database and asserts the row count —
an untested backup is a rumour.

## Domain, DNS, HTTPS

Domain registered at Cloudflare Registrar (at-cost, no renewal gouging), DNS on Cloudflare with
the record proxied — free CDN in front of a static page, and the origin IP stays off public DNS.
Caddy on the box terminates TLS with automatic Let's Encrypt certs and renewal; HSTS on. The
public site is static HTML built from the database, so Cloudflare serves nearly all of it from
cache and the origin only gets hit on a purge after each build.

## Scheduling and knowing it failed

A systemd timer, not cron: `OnCalendar=*-*-* 04:17`, `RandomizedDelaySec=900`, logs in journald,
and an `OnFailure=` unit that fires an alert if the service exits non-zero. Cron silently emails
a mailbox nobody reads.

Silence is the enemy, so the alerting is layered:

- **A dead-man's switch.** The job pings a [Healthchecks.io](https://healthchecks.io) URL on
  success. If the run never starts — box down, timer disabled, disk full — the *absence* of
  the ping alerts. A job that fails to run is the failure mode cron monitoring misses.
- **Per-casino status rows.** Each casino gets a row in a `scrape_runs` table with status and
  error. The run exits non-zero if more than 10% failed.
- **Change-rate alarms, which matter more than errors.** A layout change usually doesn't throw;
  it returns confident nonsense. So: alert if more than 15% of casinos changed their wagering
  multiplier in one night, if any field flips to null that was populated yesterday, or if a
  record hasn't refreshed in 72 hours. Stale rows get flagged on the page rather than shown as
  current.
- **Sentry** for unhandled exceptions, alerts to a Slack webhook, Healthchecks escalating to
  email and SMS.

Cost control worth doing on day one: hash the extracted page text and only call the model when
the hash changes. Bonus terms move maybe weekly, so steady-state LLM spend drops by an order
of magnitude, and the diff tells you which pages to look at.

## Rough monthly cost

| Item | € / month |
| --- | --- |
| Hetzner CPX21 | 8 |
| Domain (amortised) | 1 |
| Backblaze B2 (backups, a few GB) | 1 |
| Claude API — 200 pages/day, Haiku, with hash-skip caching | 3–6 |
| Cloudflare, Healthchecks, Sentry (free tiers) | 0 |
| **Total** | **~€13–16** |

Without the caching the API line is closer to €25–30, which is still not the thing to optimise
first. The real cost at this scale is the hour a week someone spends on broken selectors — which
is the argument for leaning on the model for interpretation and keeping the parser dumb.

---

# Five lines on AI

1. Built with Claude (Claude Opus 5 in Claude Code); the scraper's extraction step calls
   `claude-haiku-5-5` through the Anthropic API.
2. It was confidently wrong about the source: it proposed casino.guru on reputation and started
   writing a parser for it before anything had been fetched.
3. I caught it by actually fetching the page before trusting the plan — casino.guru geo-gates,
   and from this IP the wagering and minimum-deposit fields render as "No bonuses available for
   players from Ohio" and are absent from the HTML entirely.
4. Same check killed lcb.org, whose `robots.txt` disallows the bot outright — worth reading
   rather than assuming.
5. Second correction: on the AskGamblers pages the model will happily read a neighbouring offer
   card (a 2nd-deposit bonus, or another casino in the sidebar), so the prompt names that trap
   and `validate()` cross-checks the returned percentage against the source URL slug.

**Time taken:** _<fill in>_
