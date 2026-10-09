"""增量历史更新器：每日只抓「今天」的估值与收盘价，追加到历史库并裁剪。

历史库（长表，提交进仓库）：
  data/history/valuation.csv  date,index_code,pe,pe_pct,pb,pb_pct,dividend,roe,eva_type
  data/history/price.csv      date,index_code,close

价格尺度（关键：同一指数全程必须同一尺度，否则回撤会错）：
  - CN_CSI    ：Yahoo 指数符号（000300.SS / 399006.SZ…，指数点位）
  - CN_THEME  ：国信 ETF 代理（约 2 年，ETF 价）
  - HK_HSI / US_INDEX：Yahoo 指数符号
首次（历史不足）一次性回补（yfinance period=10y / 国信 500），之后只取最近 REQUEST_RECENT 天。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import akshare as ak
import pandas as pd
import requests
import yfinance as yf

try:
    from .common import DATA_ROOT, load_indices
    from . import fetch_cn_csindex as fc
    from . import fetch_djeva as fd
except ImportError:  # pragma: no cover
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from scripts.common import DATA_ROOT, load_indices  # type: ignore
    from scripts import fetch_cn_csindex as fc  # type: ignore
    from scripts import fetch_djeva as fd  # type: ignore

HIST_DIR = DATA_ROOT / "history"
HIST_DIR.mkdir(parents=True, exist_ok=True)
VAL_FILE = HIST_DIR / "valuation.csv"
PRICE_FILE = HIST_DIR / "price.csv"

KEEP_ROWS = 3000
BACKFILL_MIN = 60
REQUEST_RECENT = 12

VAL_COLS = ["date", "index_code", "pe", "pe_pct", "pb", "pb_pct", "dividend", "roe", "eva_type"]
PRICE_COLS = ["date", "index_code", "close"]


def _load(path: Path, cols) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype={"index_code": str})
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]


def _append_trim(existing: pd.DataFrame, new: pd.DataFrame, cols) -> pd.DataFrame:
    df = pd.concat([existing, new], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    df = df.drop_duplicates(subset=["date", "index_code"], keep="last")
    df = df.sort_values(["index_code", "date"])
    df = df.groupby("index_code", group_keys=False).tail(KEEP_ROWS)
    return df[cols]


PE_HIST_URL = "https://danjuanapp.com/djapi/index_eva/pe_history/{code}?day=all"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def _pe_history(djeva_code: str) -> list:
    """蛋卷 PE 历史（周频，2016 起），用于给曲线回补 PE 分位。"""
    r = requests.get(PE_HIST_URL.format(code=djeva_code),
                     headers={"User-Agent": UA, "Referer": "https://danjuanfunds.com/"}, timeout=30)
    data = r.json().get("data", {}) or {}
    out = []
    for it in data.get("index_eva_pe_growths", []):
        ts, pe = it.get("ts"), it.get("pe")
        if ts and pe is not None:
            out.append({"date": dt.datetime.utcfromtimestamp(float(ts) / 1000).date().isoformat(),
                        "pe": float(pe)})
    return out


def _djeva_snapshot() -> dict:
    items = fd._fetch_snapshot()
    mapping = fd._build_code_map()
    out = {}
    for it in items:
        mapped = mapping.get(str(it.get("index_code", "")).upper())
        if mapped:
            out[mapped] = fd._normalise_item(it)
    return out


def _csindex_valuation(symbol: str) -> dict:
    df = ak.stock_zh_index_value_csindex(symbol=symbol)
    if df.empty:
        raise RuntimeError("中证估值返回空")
    last = df.sort_values("日期").iloc[-1]
    return {
        "date": pd.to_datetime(last["日期"]).date().isoformat(),
        "pe": float(last["市盈率1"]), "pe_pct": None, "pb": None, "pb_pct": None,
        "dividend": float(last["股息率1"]) / 100.0, "roe": None, "eva_type": None,
    }


def _price_yf(symbols, want: int, period: str) -> pd.DataFrame:
    for sym in symbols:
        if not sym:
            continue
        try:
            df = yf.download(sym, period=period, progress=False, auto_adjust=False)
            if df.empty:
                continue
            frame = df.reset_index()[["Date", "Close"]]
            frame.columns = ["date", "close"]
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame.dropna().sort_values("date")
            if len(frame) >= 2:
                return frame.tail(want)
        except Exception:
            continue
    raise RuntimeError("yfinance 失败")


def _price_guosen(cfg, want: int) -> pd.DataFrame:
    if cfg.get("gs_code"):
        target = (str(cfg["gs_code"]), int(cfg.get("gs_setcode", 0)))
    else:
        target = fc._guosen_target([p for p in cfg.get("etf_proxies", []) if isinstance(p, str)])
    if not target:
        raise RuntimeError("无国信标的")
    return fc._fetch_via_guosen(*target).tail(want)


def _price_recent(cfg, existing_rows: int) -> pd.DataFrame:
    backfill = existing_rows < BACKFILL_MIN
    want = KEEP_ROWS if backfill else REQUEST_RECENT
    period = "10y" if backfill else "1mo"
    cls = cfg.get("class")

    if cls == "CN_THEME":
        proxies = [p for p in cfg.get("etf_proxies", []) if isinstance(p, str)]
        try:
            return _price_yf(proxies, want, period)
        except Exception:
            return _price_guosen(cfg, want)
    if cls == "CN_CSI":
        # 统一用 ETF 代理（与国信同尺度；Yahoo 的中证指数符号只有 1 条不可用）
        proxies = [p for p in cfg.get("etf_proxies", []) if isinstance(p, str)]
        try:
            return _price_yf(proxies, want, period)
        except Exception:
            return _price_guosen(cfg, want)
    # HK / US
    return _price_yf([str(cfg.get("price_symbol") or cfg["code"])], want, period)


def main() -> None:
    indices = load_indices()
    val_hist = _load(VAL_FILE, VAL_COLS)
    price_hist = _load(PRICE_FILE, PRICE_COLS)

    dj = {}
    try:
        dj = _djeva_snapshot()
        print(f"[估值] 蛋卷获取 {len(dj)} 条")
    except Exception as exc:  # noqa: BLE001
        print(f"[估值] 蛋卷失败: {str(exc)[:90]}")

    new_val, new_price = [], []
    for cfg in indices:
        code = cfg["code"]
        row = None
        if code in dj:
            src = dj[code]
            row = {"date": src.get("date"), "pe": src.get("pe"), "pe_pct": src.get("pe_percentile"),
                   "pb": src.get("pb"), "pb_pct": src.get("pb_percentile"),
                   "dividend": src.get("dividend_yield"), "roe": src.get("roe"), "eva_type": src.get("eva_type")}
        elif cfg.get("csindex_symbol"):
            try:
                row = _csindex_valuation(str(cfg["csindex_symbol"]))
            except Exception as exc:  # noqa: BLE001
                print(f"  中证估值失败 {code}: {str(exc)[:90]}")

        # 一次性回补 PE 历史（供曲线），仅蛋卷指数；放在快照之前，同日以快照为准
        if cfg.get("djeva_code"):
            have_pe = int(val_hist[(val_hist["index_code"] == code) & (pd.to_numeric(val_hist["pe"], errors="coerce").notna())].shape[0])
            if have_pe < 100:
                try:
                    for rec in _pe_history(str(cfg["djeva_code"])):
                        new_val.append({"index_code": code, "date": rec["date"], "pe": rec["pe"],
                                        "pe_pct": None, "pb": None, "pb_pct": None,
                                        "dividend": None, "roe": None, "eva_type": None})
                except Exception as exc:  # noqa: BLE001
                    print(f"  PE 回补失败 {code}: {str(exc)[:80]}")

        if row:
            new_val.append({"index_code": code, **{k: row.get(k) for k in VAL_COLS if k != "index_code"}})

        try:
            have = int((price_hist["index_code"] == code).sum())
            pdf = _price_recent(cfg, have)
            for _, r in pdf.iterrows():
                new_price.append({"index_code": code,
                                  "date": pd.to_datetime(r["date"]).date().isoformat(),
                                  "close": float(r["close"])})
        except Exception as exc:  # noqa: BLE001
            print(f"  价格失败 {code}: {str(exc)[:90]}")

    nv = pd.DataFrame(new_val, columns=VAL_COLS)
    np_ = pd.DataFrame(new_price, columns=PRICE_COLS)
    val_hist = _append_trim(val_hist, nv, VAL_COLS)
    price_hist = _append_trim(price_hist, np_, PRICE_COLS)
    val_hist.to_csv(VAL_FILE, index=False)
    price_hist.to_csv(PRICE_FILE, index=False)
    print(f"[历史] valuation {len(val_hist)} 行 / price {len(price_hist)} 行 → {HIST_DIR}")


if __name__ == "__main__":
    main()
