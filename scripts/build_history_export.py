"""Export a compact history series for the website chart → docs/history.json.

- PE 分位：由蛋卷 PE 历史（周频，2016 起）自算「扩张分位」= 当日 PE 在截至当日历史中的排名；
  最后一点若当日有蛋卷官方分位则以官方为准（与评分口径一致）。
- 价格：ETF 代理收盘。
- 两者统一截取最近 WINDOW_DAYS 个自然日（约半年）。
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
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
WINDOW_DAYS = 180


def _f(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _pe_percentile_series(val: pd.DataFrame) -> list:
    """扩张分位：每点 = (#{pe_j <= pe_i, j<=i}) / i。"""
    rows = [(str(r["date"])[:10], _f(r.get("pe")), _f(r.get("pe_pct"))) for _, r in val.iterrows()]
    rows = [(d, pe, pct) for d, pe, pct in rows if pe is not None]
    rows.sort(key=lambda x: x[0])
    if len(rows) < 8:      # 点太少不画（避免单点显示成 100）
        return []
    seen = []
    out = []
    for d, pe, stored_pct in rows:
        seen.append(pe)
        rank = sum(1 for x in seen if x <= pe)
        pct = rank / len(seen) * 100.0
        out.append([d, round(pct, 1)])
    # 最后一点用官方分位（若有）
    if out and rows:
        last_stored = rows[-1][2]
        if last_stored is not None:
            out[-1][1] = round(last_stored * 100, 1) if last_stored <= 1 else round(last_stored, 1)
    return out


def main() -> None:
    val = pd.read_csv(VAL_FILE, dtype={"index_code": str}) if VAL_FILE.exists() else pd.DataFrame()
    prc = pd.read_csv(PRICE_FILE, dtype={"index_code": str}) if PRICE_FILE.exists() else pd.DataFrame()

    # 统一时间窗
    all_dates = []
    if not prc.empty:
        all_dates.append(pd.to_datetime(prc["date"]).max())
    if not val.empty:
        all_dates.append(pd.to_datetime(val["date"]).max())
    cutoff = (max(all_dates) - timedelta(days=WINDOW_DAYS)).date().isoformat() if all_dates else None

    out = {"generated": pd.Timestamp.now().strftime("%Y-%m-%d"), "window_days": WINDOW_DAYS, "indices": []}
    for cfg in load_indices():
        code = cfg["code"]
        v = val[val["index_code"] == code].copy() if not val.empty else pd.DataFrame()
        p = prc[prc["index_code"] == code].copy() if not prc.empty else pd.DataFrame()
        val_series = _pe_percentile_series(v) if not v.empty else []
        if cutoff:
            val_series = [x for x in val_series if x[0] >= cutoff]
        px = []
        if not p.empty:
            p["date"] = pd.to_datetime(p["date"]).dt.date.astype(str)
            px = [[r["date"], _f(r.get("close"))] for _, r in p.iterrows() if _f(r.get("close")) is not None]
            if cutoff:
                px = [x for x in px if x[0] >= cutoff]
        out["indices"].append({"code": code, "name": cfg["name"], "watch": bool(cfg.get("watch")),
                               "val": val_series, "px": px})

    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = sum(1 for i in out["indices"] if i["val"])
    print(f"曲线数据已写入 {OUT}（{len(out['indices'])} 指数，{n} 个有 PE 分位，窗口 {WINDOW_DAYS} 天，{OUT.stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    main()
