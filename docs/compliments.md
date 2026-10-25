# Hebrew compliments

Compliments are available only in private messages. Send `/start` or `/compliment`
and the bot asks for a Hebrew name and preferred grammatical gender. The bundled
catalog currently contains **500 distinct female Hebrew compliments**. A male
profile can be saved, but will receive an availability message until male templates
are imported. The bot never changes feminine wording mechanically or sends it to
a profile configured for masculine wording.

The entered name, gender, subscription, delivery claims, and draw history stay in
SQLite on the Pi. Names are not inferred from Telegram profiles and are not logged.
`/forget` removes these records and the chat's horoscope settings. The catalog is
shared public content and remains available to other users.

## Use the bot

```text
/compliment
/compliments
/settings compliments
/settings name נועה
/settings gender נקבה
/settings compliment_time 09:00
/settings compliments on
/settings compliments off
```

The private `/settings` panel also links to the compliment settings. Buttons can
change the name, grammatical gender, delivery time, and daily subscription. Daily
compliments start **disabled**, with a default time of **09:00 Israel time**,
independent of the horoscope schedule. Changing gender disables the daily
subscription until it is explicitly enabled again. `/unsubscribe` disables both
personal daily features; `/settings compliments off` disables only compliments.

Each private chat draws randomly without replacement from the catalog for its
gender. After all available entries have been drawn, a new cycle begins and avoids
an immediate repeat of the previous entry when more than one entry is available.
History survives restarts. On-demand and daily requests share the same draw history.
Scheduled attempts occur only in their configured minute, skip missed times, and
use durable per-chat/day claims to prevent duplicate daily sends. A failed send can
consume a draw and a daily attempt. Full delivery guarantees are not assumed.

## JSONL catalog

The source file is
[`src/haverbot/compliments/resources/female.he.jsonl`](../src/haverbot/compliments/resources/female.he.jsonl).
It contains 500 UTF-8 JSON objects, one per line:

```json
{"id":1,"gender":"נקבה","text":"{name}, את מביאה איתך אור נעים גם לימים עמוסים."}
```

`id` is a stable positive integer identifying the compliment. The name is supplied
from the private profile and replaces the single `{name}` placeholder. Gender is
`נקבה` or `זכר`, with the complete text authored in that gender. IDs are unique
across all genders; use 501 and above for additional entries. Keep existing IDs
stable to preserve draw history. Text must be one line of Hebrew with punctuation,
15–600 characters long, with no other template fields or formatting instructions.
Actual recipient names or Telegram IDs never belong in the JSONL file.

The bot automatically imports missing bundled records when the container starts.
This is idempotent: existing IDs, including manual edits, are preserved. Normal
requests use SQLite only and require no remote API or content generation service.

## Import or update on the Pi

A normal image update seeds all 500 bundled entries automatically. No manual
insertion is needed. To import a custom file, validate and load it using:

```sh
uv run haverbot-import-compliments \
  src/haverbot/compliments/resources/female.he.jsonl \
  --database data/haverbot.sqlite3
```

By default, existing IDs are skipped. Add `--update` to replace text and gender for
existing IDs. The entire file is validated before any writes, and the database
import is transactional. The command never loads the Telegram token or contacts
Telegram.

For the checkout at `/opt/stacks/haverbot`, stream the file over SSH directly into
the running container; neither the file nor the database needs to be copied into
the container filesystem:

```sh
ssh rpi4.local \
  'cd /opt/stacks/haverbot && docker compose exec -T haverbot haverbot-import-compliments /dev/stdin --update' \
  < src/haverbot/compliments/resources/female.he.jsonl
```

The importer uses the same persistent SQLite volume as the bot. Importing template
text does not change stored names or subscriptions. Future container restarts keep
edits to existing IDs. Standard Docker image rollback restores the code; preserve
a private SQLite backup before changing the catalog or schema.
