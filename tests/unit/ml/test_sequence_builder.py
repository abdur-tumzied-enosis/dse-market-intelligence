import numpy as np
import pandas as pd

from ml.features.sequence_builder import (
    advance_on_calendar,
    date_boundary,
    purged_val_start,
    trading_calendar,
)


def test_trading_calendar_is_sorted_unique_union():
    times = np.array(
        ["2024-01-03", "2024-01-01", "2024-01-03", "2024-01-02"],
        dtype="datetime64[ns]",
    )
    cal = trading_calendar(times)
    assert list(cal) == list(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))


def test_date_boundary_splits_by_unique_date_quantile():
    # 10 unique dates; frac=0.8 -> boundary is the 9th (index 8)
    dates = pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 11)])
    times = np.array(list(dates) + list(dates))  # each date appears twice
    b = date_boundary(times, frac=0.8)
    assert b == dates[8]


def test_advance_on_calendar_moves_n_trading_bars_not_calendar_days():
    # Mon-Fri only (skips weekend) -> advancing 3 bars from Fri lands on Wed
    cal = trading_calendar(
        pd.to_datetime(
            ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
             "2024-01-08", "2024-01-09", "2024-01-10"]  # note: weekend 6-7 absent
        ).to_numpy()
    )
    out = advance_on_calendar(cal, pd.Timestamp("2024-01-05"), 3)
    assert out == pd.Timestamp("2024-01-10")


def test_advance_on_calendar_clamps_past_end():
    cal = trading_calendar(pd.to_datetime(["2024-01-01", "2024-01-02"]).to_numpy())
    assert advance_on_calendar(cal, pd.Timestamp("2024-01-02"), 5) == pd.Timestamp("2024-01-02")


def test_purged_val_start_advances_from_last_train_date():
    cal = trading_calendar(
        pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 21)]).to_numpy()
    )
    boundary = pd.Timestamp("2024-01-15")  # train = dates < 15, last train = 14
    # gap of 3 bars from 2024-01-14 -> 2024-01-17
    assert purged_val_start(cal, boundary, gap_bars=3) == pd.Timestamp("2024-01-17")
