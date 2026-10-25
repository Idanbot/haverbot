# Haverbot · חברבוט 🔮

A modular Telegram bot for groups and private chats for Hebrew daily horoscopes from Israeli publishers.
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
and on-demand access. Other group members cannot modify settings, including through old panels.
Group changes recheck Telegram administrator status. In a private chat, the user
controls their own settings and can enable their own daily subscription.

## Commands and settings

| Command | Behavior |
| --- | --- |
| `/help` or `/start` | Hebrew help, including in private chat |
| `/horoscope` | Bulletin for this chat's selected signs |
| `/horoscope אריה` or `/horoscope leo` | Full forecast for one sign |
| `/sign אריה` | Alias for a single-sign request |
| `/compliment` | Private personalized Hebrew compliment; first use asks name/gender |
| `/compliments` | Private compliment settings and daily subscription |
| `/settings` | Personal settings in DMs; administrator settings in groups |
| `/unsubscribe` | Disable scheduled posts; group admins only in groups |
| `/forget` | Delete this chat's settings and cooldowns; group admins only in groups |

Configuration commands are available to each DM user and to group administrators:

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
| `/settings words auto` | Default: allocate the available character budget across signs |
| `/settings words 60` | Optional word cap per sign; 5–100, within the character budget |

Both cooldowns are shared across the **whole chat**, but independent of each
other. Asking for Leo starts the single-sign cooldown for Aries and every other
single sign too. Expansion buttons use that same cooldown. Scheduled posts bypass
both cooldowns. Unavailable forecasts and failed sends before any part is sent do
not consume a cooldown. Chat selection filters bulletins; explicit single-sign
commands may request any sign.

Bulletins display the actual provider, Gregorian and Hebrew forecast dates,
Hebrew sign names and zodiac symbols, previews, and a source link. A preview stops
at Telegram's character budget. Short signs donate unused space to longer signs.
An optional manual word cap can further shorten previews.
Truncated signs get a `התחזית המלאה` button. Full single-sign forecasts can span
several messages if necessary. HTML is escaped; link previews are disabled.

The Hebrew date describes the **forecast day**, rather than the religious date at
the instant of delivery after sunset. Text stays in Unicode logical order for
Telegram's native RTL rendering.

Compliment profile and schedule settings are described in the [compliments guide](compliments.md).
Daily compliments are independent of daily horoscopes and begin disabled.

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

## Cache and scheduling

* Each enabled provider is ingested once per Israel-local calendar day, independently
  of how many chats use the bot. Set `HAVERBOT_INGEST_TIME=06:00` in `.env` to choose
  the daily ingestion time. An unattempted day runs on startup after that time.
* A SQLite claim is saved before network access. Partial results remain cached;
  failures and interrupted attempts wait until the next day. Container updates,
  restarts, chat commands, and settings changes do not repeat a daily ingestion.
  Provider adapters may need multiple HTTP requests to collect their signs during
  the single ingestion job. Per-host requests remain spaced by at least two seconds.
* Commands, expansion buttons, and scheduled posts read only SQLite. Missing
  forecasts produce a Hebrew availability message. There is no command-triggered
  scraping or delayed reply. Choose a posting time after the ingestion time.
* Cache keys are Israel-local day + provider + sign. Full text is immutable for
  that key. Retention is today plus six previous days. Full-forecast buttons keep
  the original provider/date/sign and explain when their cached forecast expires.
* Scheduled delivery is checked every 15 seconds, during its configured minute.
  Startup skips the current minute; missed posts are not replayed. Work expires at
  the end of its minute. Scheduled posts bypass on-demand cooldowns.
* `Asia/Jerusalem` handles DST: a missing spring hour is skipped and a repeated
  autumn hour sends at most once per chat/day. Delivery claims are durable.

## Automatic container updates

Compose includes Watchtower, which checks GHCR every 300 seconds and updates only
Haverbot containers carrying this stack's enable/scope labels. The SQLite volume
survives replacement. Watchtower uses the host Docker socket to replace containers;
the bot container does not have access to that socket. `DOCKER_API_VERSION` defaults
to 1.44; override `WATCHTOWER_DOCKER_API_VERSION` in `.env` if your Docker host needs
another supported API version. The Watchtower image supports ARM64 and AMD64.

To apply Compose or `.env` changes, run `git pull --ff-only` and
`docker compose up -d`. Watchtower updates the bot image; Git/Compose configuration
changes still need that manual step. Pin `HAVERBOT_IMAGE` to an immutable SHA tag
if you want to stay on a specific bot release.

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
retention, daily ingestion claims and cache-only requests, fallback, administrator checks, separate
cooldowns, concurrent SQLite connections, message escaping/budgets, old expansion
buttons, startup/shutdown, missed deliveries, and both DST transitions.

See [architecture](architecture.md) to add a provider or another bot feature.

## Private runtime data

`.env`, databases, volumes, caches, and build artifacts are excluded from Git and
Docker build contexts. The Dockerfile copies only dependency manifests and source
code. CI never needs the real token.

SQLite stores group or private chat IDs because subscriptions require a destination. They
stay in the runtime volume; Telegram usernames, member profiles, birthdays, and message history are not stored.
Compliment users supply their own Hebrew name and gender; these stay only in SQLite. `/forget` deletes chat settings, compliment names/profiles/history, cooldowns, and delivery
claims; removal of the bot from a group also clears those records. The forecast
cache is shared public content, so forgetting a group does not delete it.
Application logs contain provider/error types and aggregate counts, rather than
chat IDs or message bodies. Third-party HTTP/Telegram logging is suppressed to
avoid token-bearing URLs. Back up the SQLite volume while the bot is stopped;
keep backups private as well.
