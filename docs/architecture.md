# Architecture

The bot is one long-polling process. It needs no web server, Redis, separate task
queue, external calendar service, or LLM. SQLite and Telegram are the only stateful
services. A 15-second maintenance loop coordinates daily fetches and scheduling.

```text
Telegram commands / buttons
         │
         ▼
TelegramController ───────► formatting (HTML + pure message/button models)
         │
         ▼
HoroscopeService ◄──────── Scheduler
    │          │
    ▼          ▼
  Store     Provider protocol
 (SQLite)       │
                ├── IsraelHayomProvider
                └── MakoProvider
                         │
                         ▼
                     HttpFetcher
```

## Module boundaries

* `domain.py`: immutable forecasts and group settings, sign identities, validation,
  Hebrew labels, and the Israel timezone.
* `storage.py`: asynchronous persistence. Forecast keys are immutable; conditional
  SQLite UPSERTs atomically claim cooldowns even across connections. Delivery
  primary keys prevent repeated daily attempts. `user_version=4` includes durable provider/day ingestion claims and migration
  of the previous default word cap to automatic sizing.
* `providers/`: public HTML/JSON extraction. Each provider returns only dated daily
  forecasts it can verify. HTTP timeouts, response bounds, pacing, and safe failures
  live in the shared fetcher. Parsers accept HTML strings, enabling fixture tests.
* `service.py`: one daily ingestion task per provider, durable attempt claims,
  provider fallback, retention, and the two on-demand cooldown buckets. Time is
  injected for deterministic tests. Chat settings, cooldowns, and ingestion claims survive restarts. Commands only
  read cached forecasts; failures wait for the next local day.
* `formatting.py`: Hebrew date conversion, safe HTML, preview word/character
  budgets, exact expansion identifiers, and long single-sign splitting.
* `scheduler.py`: current-minute delivery and durable per-group/day attempt claims.
  The schedule is an Israel wall-clock time; timestamps are used for elapsed-time
  cooldowns, so the autumn repeated hour cannot extend or reset a cooldown.
* `telegram_app.py`: transport and permission checks. Commands are processed
  sequentially, while fetch tasks run concurrently in the event loop. In groups, each settings mutation checks live Telegram administrator membership.
  Private chat users control their own configuration without a membership lookup.
* `main.py`: dependency composition, lifecycle, commands, and polling. No service
  reads the token; it is confined to configuration and Telegram construction.
* `compliments/`: gender-specific Hebrew templates, strict JSONL import, private
  profile/onboarding persistence, random draws without replacement, and daily
  delivery through the scheduler extension. The bundled catalog is a package
  resource; startup imports missing IDs without overwriting manual edits.
* `check_providers.py`: optional live source diagnostics; never loads bot credentials.

## Add a provider

1. Implement `Provider` (`id` and `async fetch(day, missing) -> list[Forecast]`).
2. Use the injected `Fetcher` rather than creating an independent HTTP client.
3. Keep extraction separate from I/O. Require evidence of the **forecast date**,
   which may differ from the article publication timestamp. Never substitute the
   fetch date for missing source date metadata.
4. Return partial successes. Raise `ProviderError` for transport/access failures;
   supply `retry_after` for throttling metadata. A complete forecast never needs refetching
   for the same day/sign key.
5. Register its ID/name in `PROVIDER_NAMES`, wire the adapter in `main.py` and the
   diagnostic, and add dated synthetic HTML fixtures plus mocked HTTP tests.
6. Update admin options/help and live-verification documentation.

`Forecast` contains provider ID, date, sign, full normalized text, and an HTTP(S)
source URL. The service handles caching and fallback. Providers do not format
Telegram messages, inspect group settings, send messages, or access the token.

## Add a bot feature

Create a separate domain/service module and Telegram handlers, then register it at
the composition root. Keep authorization in the transport boundary and persistence
behind storage methods. Extend the schema through a versioned migration if needed.
Horoscope parsing, selection, and formatting should not acquire feature-specific
conditionals for unrelated functionality.

## Consistency choices

* All signs in a bulletin come from one provider/day. A group's chosen subset can
  be served when that subset is present in the daily ingestion result.
* A single-sign command resolves the provider according to current settings. An
  expansion callback resolves an exact immutable provider/day/sign key instead.
* Missing data does not start a cooldown. A successful reservation prevents
  simultaneous requests from flooding a group; a failure before the first send
  releases it. A partly sent multi-message sign counts as delivered.
* Scheduled delivery claims are never released. This yields at-most-once attempts,
  with the accepted possibility of skipping a post during a crash/network failure.
* Old scheduled posts and queued Telegram commands are not replayed on restart.
* Credentials and group identifiers never belong in fixtures, source, or logs.
  Synthetic fixtures reflect parser contracts; they are not proof of live source
  availability. Keep explicit live checks separate from deterministic CI.
