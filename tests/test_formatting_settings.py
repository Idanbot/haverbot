import re
from dataclasses import replace
from datetime import date
from html import unescape

import pytest

from haverbot.domain import GroupSettings, Sign, validate_time
from haverbot.formatting import bulletin, full_sign, header, units
from haverbot.telegram_app import TelegramController


def test_group_defaults_to_mako_and_all_signs():
    settings = GroupSettings(-1)
    assert settings.provider == "mako"
    assert settings.signs == tuple(Sign)


def test_hebrew_header_uses_forecast_date_and_escapes_content(forecasts):
    message = bulletin([replace(forecasts[0], text="<b>שלום & עולם</b>")], GroupSettings(-1))
    assert "25/10/2026" in message.text
    assert "תשפ״ז" in message.text or "תשפ״ז".replace("״", '"') in message.text
    assert "&lt;b&gt;שלום &amp; עולם&lt;/b&gt;" in message.text
    assert "mako" in message.text
    assert not message.buttons
    assert "25/10/2026" in header("mako", date(2026, 10, 25))


def test_preview_words_buttons_and_telegram_budget(forecasts):
    message = bulletin(forecasts, GroupSettings(-1, max_words=40))
    assert len(message.buttons) == 12
    assert all(button.data.startswith("full:mako:2026-10-25:") for button in message.buttons)
    assert all(len(button.data.encode()) <= 64 for button in message.buttons)
    assert units(message.text) <= 4000
    for line in message.text.split("\n\n")[1:-1]:
        assert len(line.split(":</b> ", 1)[1].split()) <= 40


def test_huge_words_emoji_and_html_are_bounded(forecasts):
    huge = [replace(f, text=("<&😀" * 1500 + " ") * 10) for f in forecasts]
    message = bulletin(huge, GroupSettings(-1))
    assert units(message.text) <= 4000
    assert len(message.buttons) == 12


def test_full_sign_split_preserves_text_and_valid_entities(forecasts):
    text = ("שלום <עולם> & 🦁 " * 2000).strip()
    parts = full_sign(replace(forecasts[4], text=text))
    assert len(parts) > 1
    assert all(units(p.text) <= 4000 for p in parts)
    reconstructed = " ".join(
        unescape(re.sub(r"<[^>]+>", "", p.text.split("<b>אריה</b>\n", 1)[1].split("\n\n🔗")[0]))
        for p in parts
    )
    assert reconstructed == text


def test_mixed_provider_or_day_rejected(forecasts):
    with pytest.raises(ValueError, match="one provider"):
        bulletin([forecasts[0], replace(forecasts[1], provider="walla")], GroupSettings(-1))
    with pytest.raises(ValueError):
        full_sign(replace(forecasts[0], source_url="javascript:alert(1)"))


@pytest.mark.parametrize(
    "key,value,field,expected",
    [
        ("time", "23:59", "post_time", "23:59"),
        ("provider", "israelhayom", "provider", "israelhayom"),
        ("enabled", "on", "enabled", True),
        ("on_demand", "off", "on_demand", False),
        ("fallback", "toggle", "fallback", False),
        ("cooldown", "1440", "bulletin_minutes", 1440),
        ("single_cooldown", "1", "single_minutes", 1),
        ("words", "60", "max_words", 60),
        ("signs", "אריה,טלה", "signs", (Sign.ARIES, Sign.LEO)),
    ],
)
def test_admin_setting_values(key, value, field, expected):
    settings = TelegramController.apply_setting(GroupSettings(-1), key, value)
    assert getattr(settings, field) == expected


@pytest.mark.parametrize(
    "key,value",
    [
        ("time", "24:00"),
        ("time", "8:00"),
        ("cooldown", "0"),
        ("single_cooldown", "1441"),
        ("cooldown", "bad"),
        ("provider", "unknown"),
        ("provider", "walla"),
        ("words", "101"),
        ("signs", ""),
        ("enabled", "yes"),
        ("unknown", "on"),
    ],
)
def test_invalid_settings_rejected(key, value):
    with pytest.raises(ValueError):
        TelegramController.apply_setting(GroupSettings(-1), key, value)


def test_cannot_deselect_last_sign():
    with pytest.raises(ValueError):
        TelegramController.apply_setting(GroupSettings(-1, signs=(Sign.LEO,)), "sign", "leo")


@pytest.mark.parametrize("value", ["00:00", "02:30", "23:59"])
def test_valid_local_times(value):
    assert validate_time(value) == value
