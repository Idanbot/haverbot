from datetime import datetime

import pytest

from haverbot.domain import ISRAEL, Forecast, Sign
from haverbot.storage import Store


@pytest.fixture
async def store():
    db = Store(":memory:")
    await db.open()
    yield db
    await db.close()


@pytest.fixture
def now():
    return datetime(2026, 10, 25, 12, 0, tzinfo=ISRAEL)


@pytest.fixture
def forecasts(now):
    return [
        Forecast(
            "mako",
            now.date(),
            sign,
            "שלום עולם תחזית יומית נעימה להיום " * 12,
            "https://mobile.mako.co.il/spirituality-newage/horoscope",
        )
        for sign in Sign
    ]
