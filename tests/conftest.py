import pytest

from agent.config import load_config


@pytest.fixture
def cfg():
    return load_config()


from pathlib import Path

import pandas as pd

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def zigzag():
    return pd.read_csv(FIXTURES / "zigzag_4h.csv")


@pytest.fixture
def wilder():
    return pd.read_csv(FIXTURES / "wilder_rsi.csv", comment="#")


def candles(rows):
    """[(open, high, low, close[, volume])] -> OHLCV frame."""
    df = pd.DataFrame([r if len(r) == 5 else (*r, 1.0) for r in rows],
                      columns=["open", "high", "low", "close", "volume"])
    df.insert(0, "ts", range(len(df)))
    return df
