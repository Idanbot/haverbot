# Haverbot · חברבוט 🔮

A modular Telegram group bot for Hebrew daily horoscopes from Israeli publishers.
Python **3.15.1**, **uv**, SQLite, and Docker. Designed for a Raspberry Pi 4 with
**64-bit Raspberry Pi OS**; CI also builds and smoke-tests an ARM64 image.

## Run on the Pi

Install Docker Engine and Docker Compose, then clone this repository. For a new
installation, copy `.env.example` to `.env` and set your BotFather token:

```dotenv
TELEGRAM_BOT_TOKEN=your_token_here
```

An existing `.env` is used as-is. Keep it local. Then:

```sh
docker compose pull
docker compose up -d
docker compose logs --tail=100 -f
```

No public port, webhook, domain, or inbound router configuration is needed: the bot
uses long polling. Run **one instance per bot token**. The named `haverbot-data`
volume persists the database across container replacements. `docker compose down`
preserves the volume; adding `--volumes` deletes its data.

Add the bot to a Telegram group and give it administrator status. This allows
reliable verification of group administrators. It only needs to send messages;
message deletion and member-management permissions are unnecessary. Telegram's
privacy mode can stay enabled: addressed slash commands and callback buttons work.
Daily posting is initially disabled.

In the group, a group administrator can run:

```text
/settings
/settings time 08:00
/settings provider israelhayom
/settings signs all
/settings enabled on
```

The settings panel has Hebrew buttons for providers, signs, subscription, fallback,
and on-demand access. Other members cannot modify settings, including through old
panels. Every action rechecks Telegram administrator status.

## Commands and settings

| Command | Behavior |
| --- | --- |
| `/help` or `/start` | Hebrew help, including in private chat |
| `/horoscope` | Bulletin for this group's selected signs |
| `/horoscope אריה` or `/horoscope leo` | Full forecast for one sign |
| `/sign אריה` | Alias for a single-sign request |
| `/settings` | Administrator settings panel |
| `/unsubscribe` | Administrator disables scheduled posts |
| `/forget` | Administrator deletes this group's settings and cooldowns |

Configuration commands, available to group administrators:

| Example | Default / constraints |
| --- | --- |
| `/settings enabled on` | Daily posting off until enabled |
| `/settings time 08:00` | `08:00`; strict `HH:MM`, Israel local time |
| `/settings provider israelhayom` | `israelhayom` or `mako` |
| `/settings fallback off` | On by default |
| `/settings signs אריה,טלה` | All signs by default; at least one required |
| `/settings signs all` | Select all 12 signs |
| `/settings on_demand off` | On by default; controls commands and expansion buttons |
| `/settings cooldown 15` | Bulletin cooldown; 1–1440 minutes |
| `/settings single_cooldown 1` | Single-sign cooldown; 1–1440 minutes |
| `/settings words 40` | Maximum preview words per sign; 5–100 |

Both cooldowns are shared across the **whole group**, but independent of each
other. Asking for Leo starts the single-sign cooldown for Aries and every other
single sign too. Expansion buttons use that same cooldown. Scheduled posts bypass
both cooldowns. Unavailable forecasts and failed sends before any part is sent do
not consume a cooldown. Group selection filters bulletins; explicit single-sign
commands may request any sign.

Bulletins display the actual provider, Gregorian and Hebrew forecast dates,
Hebrew sign names and zodiac symbols, previews, and a source link. A preview stops
at the configured word limit, or earlier to fit Telegram's character budget.
Truncated signs get a `התחזית המלאה` button. Full single-sign forecasts can span
several messages if necessary. HTML is escaped; link previews are disabled.

The Hebrew date describes the **forecast day**, rather than the religious date at
the instant of delivery after sunset. Text stays in Unicode logical order for
Telegram's native RTL rendering.

## Horoscope providers

| Provider | Public source |
| --- | --- |
| ישראל היום | https://www.israelhayom.co.il/horoscopes |
| mako | https://mobile.mako.co.il/spirituality-newage/horoscope?partner=ChannelHeader |

Mako is the default preference. With automatic fallback enabled, the bot tries
the preferred provider first, followed by the remaining providers in this order:
mako, Israel Hayom. New groups include all 12 signs by default. One bulletin
always uses **one provider**.
Each forecast must match the requested date. Missing content is never fabricated,
mixed across providers, or relabeled with today's date.

Run an explicit live diagnostic without loading `.env`, contacting Telegram,
storing group data, or printing source text:

```sh
uv run haverbot-check-providers mako
uv run haverbot-check-providers israelhayom mako
# Check a specific forecast date without caching it:
uv run haverbot-check-providers --date 2026-10-25 mako
# In the built image:
docker compose run --rm --no-deps --entrypoint haverbot-check-providers haverbot mako
```

The diagnostic exits successfully if at least one requested provider supplies all
12 signs. It exits nonzero if none do. Live diagnostics are intentionally separate
from CI; tests use local synthetic fixtures and mocked HTTP.

## Cache, retries, and scheduling

* SQLite stores each **Israel-local forecast day + provider + sign** separately.
  Full text is immutable once cached for that key, keeping expansion buttons
  consistent with the original bulletin.
* Retention is **today plus six previous calendar days**. Buttons include the
  original provider/day/sign; expired buttons explain in Hebrew that the cache no
  longer contains the forecast. They never switch to today's text or another source.
* After a group configures settings or requests content, background maintenance
  builds all signs for its providers. Complete days are reused without fetching
  again. Partial successes remain cached and only missing signs are requested.
* Concurrent requests share fetching work. Per-host HTTP requests are separated
  by at least two seconds, have timeouts and size limits, and use bounded connection
  pools. Incomplete results retry after 1, 2, 4, 8, 16, then 30 minutes, capped at
  30 minutes. HTTP 429 honors `Retry-After`; access-denied/challenge responses wait
  at least one hour. No CAPTCHA solving or browser automation is included.
* A demand for unavailable content gets a Hebrew availability message immediately
  while fetching continues in the background. Request it again later; there is no
  unsolicited delayed delivery.
* Scheduled delivery is considered only during the configured minute, checked
  every 15 seconds. Startup skips the current minute, missed times aren't replayed,
  and delivery work expires at the end of its minute. If content is unavailable,
  that scheduled attempt sends the Hebrew availability message instead.
* `Asia/Jerusalem` handles DST. A missing spring hour is skipped; a repeated autumn
  hour sends at most once per group/day. A durable claim is written **before** the
  attempt. A crash or delivery failure can therefore skip a post; avoiding duplicate
  or late scheduled messages takes precedence over retrying delivery.

## Development and checks

```sh
uv sync --locked
uv run haverbot
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest --cov=haverbot --cov-report=term-missing
uv run python scripts/check_public_files.py
```

`uv.lock` pins dependencies for local development, CI, and Docker. The runtime
image has no uv or development tools and runs as UID/GID 10001. Compose uses a
read-only root filesystem with a persistent writable data volume. Host timezone
settings are not required because scheduling explicitly uses Israel's zone.

GitHub Actions runs unit tests, linting, formatting, type checks, and a basic
publishable-file secret check. Native AMD64 and ARM64 jobs build the image and
check imports, non-root execution, and volume persistence. After checks pass, pushes to main and version tags publish AMD64/ARM64 images to
`ghcr.io/idanbot/haverbot` using the built-in `GITHUB_TOKEN`. Pull requests never
publish. No Telegram token is needed in CI.

The tests exercise provider date/body extraction, blocked responses, cache
retention, partial fetches and retries, fallback, administrator checks, separate
cooldowns, concurrent SQLite connections, message escaping/budgets, old expansion
buttons, startup/shutdown, missed deliveries, and both DST transitions.

See [architecture](architecture.md) to add a provider or another bot feature.

## Private runtime data

`.env`, databases, volumes, caches, and build artifacts are excluded from Git and
Docker build contexts. The Dockerfile copies only dependency manifests and source
code. CI never needs the real token.

SQLite stores group chat IDs because subscriptions require a destination. They
stay in the runtime volume; usernames, member profiles, birthdays, and message
history are not stored. `/forget` deletes group settings, cooldowns, and delivery
claims; removal of the bot from a group also clears those records. The forecast
cache is shared public content, so forgetting a group does not delete it.
Application logs contain provider/error types and aggregate counts, rather than
chat IDs or message bodies. Third-party HTTP/Telegram logging is suppressed to
avoid token-bearing URLs. Back up the SQLite volume while the bot is stopped;
keep backups private as well.
