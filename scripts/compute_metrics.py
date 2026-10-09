"""Aggregate the committed history store into daily dashboard metrics.

Reads data/history/valuation.csv and data/history/price.csv (long format,
maintained incrementally by update_history.py). No network access — so this
also runs fine in CI from committed data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    from .common import DATA_ROOT, ensure_data_dir, load_indices
except ImportError:  # pragma: no cover - direct execution fallback
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from scripts.common import DATA_ROOT, ensure_data_dir, load_indices  # type: ignore


HIST_DIR = DATA_ROOT / "history"
VAL_FILE = HIST_DIR / "valuation.csv"
PRICE_FILE = HIST_DIR / "price.csv"
PROCESSED_DIR = ensure_data_dir("processed")
METRICS_FILE = PROCESSED_DIR / "metrics.csv"


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"index_code": str})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _percentile(series: pd.Series) -> Optional[float]:
    series = _num(series).dropna()
    # 只用足够长的历史自算分位（≥约 8 年），避免用短窗口给出误导性百分位
    if len(series) < 2000:
        return None
    series = series.sort_index()
    current = series.iloc[-1]
    return float(np.clip((series <= current).sum() / len(series) * 100.0, 0.0, 100.0))


def _current(series: pd.Series) -> Optional[float]:
    series = _num(series).dropna()
    return float(series.iloc[-1]) if not series.empty else None


def _drawdown(price: pd.Series) -> Optional[float]:
    price = _num(price).dropna()
    if price.empty:
        return None
    rolling_max = price.cummax()
    dd = 1.0 - price / rolling_max
    return float(np.clip(dd.iloc[-1], 0.0, 1.0))


def main() -> None:
    indices = load_indices()
    val_all = _read(VAL_FILE)
    price_all = _read(PRICE_FILE)
    records = []

    for cfg in indices:
        code = cfg["code"]
        val = val_all[val_all["index_code"] == code].sort_values("date") if not val_all.empty else pd.DataFrame()
        prc = price_all[price_all["index_code"] == code].sort_values("date") if not price_all.empty else pd.DataFrame()

        if val.empty:
            print(f"[metrics] 缺少估值数据: {code}")
        if prc.empty:
            print(f"[metrics] 缺少行情数据: {code}")

        pe_pct = None
        if "pe_pct" in val.columns:
            v = _current(val["pe_pct"])
            if v is not None:
                pe_pct = float(v) * 100.0 if v <= 1 else float(v)
        if pe_pct is None:
            pe_pct = _percentile(val.get("pe", pd.Series(dtype=float)))

        pb_pct = None
        if "pb_pct" in val.columns:
            v = _current(val["pb_pct"])
            if v is not None:
                pb_pct = float(v) * 100.0 if v <= 1 else float(v)
        if pb_pct is None:
            pb_pct = _percentile(val.get("pb", pd.Series(dtype=float)))

        close = prc.get("close", pd.Series(dtype=float))
        drawdown = _drawdown(close)
        price_len = int(_num(close).dropna().shape[0])
        if price_len > 1 and not prc.empty:
            span_years = (prc["date"].max() - prc["date"].min()).days / 365.0
        else:
            span_years = 0.0
        dd_window = f"{min(span_years, 10.0):.0f}y" if span_years > 0 else ""

        coverage = "full" if (pe_pct is not None and pb_pct is not None) else "partial"
        last_val = val.iloc[-1] if not val.empty else {}

        records.append({
            "index_code": code,
            "pe_pct": pe_pct,
            "pb_pct": pb_pct,
            "drawdown": drawdown,
            "pe_current": _current(val.get("pe", pd.Series(dtype=float))),
            "pb_current": _current(val.get("pb", pd.Series(dtype=float))),
            "dividend_current": _current(val.get("dividend", pd.Series(dtype=float))),
            "roe_current": _current(val.get("roe", pd.Series(dtype=float))),
            "eva_type": last_val.get("eva_type") if not val.empty else None,
            "eva_type_int": None,
            "bond_yield": None,
            "coverage": coverage,
            "dd_window": dd_window,
            "watch": bool(cfg.get("watch", False)),
        })

    metrics = pd.DataFrame(records)
    metrics.to_csv(METRICS_FILE, index=False)
    print(f"指标文件已生成: {METRICS_FILE} ({len(metrics)} 条记录)")


if __name__ == "__main__":
    main()
