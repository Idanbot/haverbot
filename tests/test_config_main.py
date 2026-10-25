import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock

import pytest
from telegram.ext import Application, ExtBot

from haverbot.config import Config
from haverbot.main import main


def test_config_hides_token_and_requires_it(monkeypatch):
    monkeypatch.setattr("haverbot.config.load_dotenv", lambda: None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        Config.from_env()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "example-private-value")
    monkeypatch.setenv("HAVERBOT_DATABASE", "example.db")
    config = Config.from_env()
    assert "example-private-value" not in repr(config)
    assert config.database == "example.db"


def test_composition_lifecycle_without_telegram_network(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config, "from_env", lambda: Config("123456:TEST_TOKEN", str(tmp_path / "state.db"))
    )
    commands = AsyncMock()
    monkeypatch.setattr(ExtBot, "set_my_commands", commands)
    called = []

    def fake_polling(application, **kwargs):
        assert kwargs["drop_pending_updates"]
        assert len(application.handlers[0]) >= 9

        async def lifecycle():
            await application.post_init(application)
            with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
                assert connection.execute("SELECT count(*) FROM compliments").fetchone()[0] == 500
            await asyncio.sleep(0)
            await application.post_stop(application)
            await application.post_shutdown(application)

        asyncio.run(lifecycle())
        called.append(True)

    monkeypatch.setattr(Application, "run_polling", fake_polling)
    main()
    assert called == [True]
    commands.assert_awaited_once()
    assert {"start", "compliment", "compliments"} <= {c.command for c in commands.call_args.args[0]}
