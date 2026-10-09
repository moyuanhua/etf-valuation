"""Export a compact history series for the website chart → docs/history.json.

Only the last CHART_ROWS points per index (约半年交易日) are exported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

try:
    from .common import DATA_ROOT, PROJECT_ROOT, load_indices
except ImportError:  # pragma: no cover
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from scripts.common import DATA_ROOT, PROJECT_ROOT, load_indices  # type: ignore

HIST_DIR = DATA_ROOT / "history"
VAL_FILE = HIST_DIR / "valuation.csv"
PRICE_FILE = HIST_DIR / "price.csv"
OUT = PROJECT_ROOT / "docs" / "history.json"
CHART_ROWS = 180  # 约半年交易日


def _round(v, n=4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, n)


def main() -> None:
    val = pd.read_csv(VAL_FILE, dtype={"index_code": str}) if VAL_FILE.exists() else pd.DataFrame()
    prc = pd.read_csv(PRICE_FILE, dtype={"index_code": str}) if PRICE_FILE.exists() else pd.DataFrame()

    out = {"generated": pd.Timestamp.now().strftime("%Y-%m-%d"), "indices": []}
    for cfg in load_indices():
        code = cfg["code"]
        v = val[val["index_code"] == code].tail(CHART_ROWS) if not val.empty else pd.DataFrame()
        p = prc[prc["index_code"] == code].tail(CHART_ROWS) if not prc.empty else pd.DataFrame()
        val_series = []
        for _, r in v.iterrows():
            pct = _round(r.get("pe_pct"), 4)
            if pct is not None and pct <= 1:
                pct = round(pct * 100, 1)
            val_series.append([str(r["date"])[:10], pct])
        px_series = [[str(r["date"])[:10], _round(r.get("close"))] for _, r in p.iterrows()]
        out["indices"].append({
            "code": code, "name": cfg["name"], "watch": bool(cfg.get("watch")),
            "val": val_series, "px": px_series,
        })

    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size = OUT.stat().st_size / 1024
    print(f"曲线数据已写入 {OUT} ({len(out['indices'])} 指数, {size:.0f} KB)")


if __name__ == "__main__":
    main()
