# חברבוט · Haverbot 🔮

A friendly Telegram bot bringing Hebrew daily horoscopes to groups and private chats,
plus personal Hebrew compliments in DMs.
Pick your signs, provider, and posting time—or ask whenever you like.

- 🇮🇱 Israel time, Hebrew dates, and Hebrew messages.
- 📰 Israel Hayom and mako adapters with automatic fallback.
- ⚙️ Personal DM settings, admin-controlled groups, and full-forecast buttons.
- 💝 500 Hebrew compliments, personal name/gender settings, and optional daily delivery.
- 💾 One daily ingestion, seven days of SQLite cache, and previews sized to fit.
- 🐳 Python 3.15.1 + uv; Docker images for ARM64 and AMD64.

## Run on your Raspberry Pi 4

Use **64-bit Raspberry Pi OS**, with Docker and Docker Compose installed.

```sh
git clone https://github.com/Idanbot/haverbot.git
cd haverbot
cp .env.example .env
nano .env  # Set TELEGRAM_BOT_TOKEN from BotFather.
docker compose pull
docker compose up -d
```

Compose pulls the public `ghcr.io/idanbot/haverbot:latest` image automatically for
your architecture. No registry login or on-device build is needed. Watchtower checks for bot image
updates every five minutes and automatically replaces the bot when an update is available.

Add the bot to your group as an admin, then run:

```text
/settings
/settings time 08:00
/settings enabled on
```

You can also open a private chat and run `/start`. It asks for a Hebrew name and
grammatical gender for compliments. Use `/compliment` for a random compliment, or
`/compliments` to enable daily compliments. The catalog is female-only for now;
names and profiles stay in the private SQLite volume.
Anyone can use `/horoscope` or `/sign אריה`. Group admins control the provider,
selected signs, on-demand access, and cooldowns through `/settings`.
Scheduled posts start disabled; missed posting times are skipped. In DMs, each
user controls their own settings. Use `/settings words auto` for automatic previews.

Both sources are ingested once per Israel-local day at **06:00**, configurable
with `HAVERBOT_INGEST_TIME` in `.env`. Starting after that time runs an unattempted
day immediately. Commands read the cache; failed daily ingestion waits until the next day.

Your settings live in a Docker volume and survive updates. Keep `.env` private;
Git and Docker exclude it, credentials, databases, and local backups.

## Update

```sh
git pull --ff-only
docker compose pull
docker compose up -d
```

## Develop

```sh
uv sync --locked
uv run pytest
uv run haverbot
```

CI checks tests, types, lint, and both Docker architectures before publishing to
GHCR on `main` and `v*.*.*` tags. It uses GitHub's built-in token; your Telegram
secret stays on the Pi.

See the [full guide](docs/usage.md) and [architecture](docs/architecture.md) for details.

See the [compliments guide](docs/compliments.md) for settings, JSONL format, and
SSH import commands for the Pi. The 500 bundled records load automatically.
