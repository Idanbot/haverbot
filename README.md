# חברבוט · Haverbot 🔮

A friendly Telegram bot bringing Hebrew daily horoscopes to your groups.
Pick your signs, provider, and posting time—or ask whenever you like.

- 🇮🇱 Israel time, Hebrew dates, and Hebrew messages.
- 📰 Israel Hayom, Walla, and mako adapters with automatic fallback.
- ⚙️ Admin-only settings, shared cooldowns, and buttons for full forecasts.
- 💾 Seven days of cached forecasts in SQLite.
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

Compose uses `ghcr.io/idanbot/haverbot:latest`. After the first CI publication,
make the [GHCR package](https://github.com/users/Idanbot/packages/container/haverbot/settings)
**public** once so the Pi can pull without logging in.

Add the bot to your group as an admin, then run:

```text
/settings
/settings time 08:00
/settings enabled on
```

Anyone can use `/horoscope` or `/sign אריה`. Group admins control the provider,
selected signs, on-demand access, and cooldowns through `/settings`.
Scheduled posts start disabled; missed posting times are skipped.

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

**Source availability:** mako's 12 daily signs were verified. Israel Hayom blocked
this machine's automated requests; Walla returned no dated daily text. Fallback
handles unavailable sources.

See the [full guide](docs/usage.md) and [architecture](docs/architecture.md) for details.
