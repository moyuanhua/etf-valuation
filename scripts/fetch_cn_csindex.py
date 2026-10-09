"""Fetch CSI index daily prices.

三级兜底（境外 runner 抓不到东财时依次降级）：
1. akshare stock_zh_index_daily_em（约 15 年，本地/中国 IP 最稳）
2. yfinance 指数符号 / ETF 代理
3. 国信 queryPastHQInfo（约 2 年，需 GS_API_KEY；CI 里配了 Secret 即可用）

单只失败不拖垮整体；仅当全部失败才报错。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib import request as urllib_request
from urllib.parse import urlencode

import akshare as ak
import pandas as pd
import yfinance as yf

try:
    from .common import ensure_data_dir, load_indices
except ImportError:  # pragma: no cover - direct execution fallback
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from scripts.common import ensure_data_dir, load_indices  # type: ignore


RAW_DIR = ensure_data_dir("raw", "cn_csi")
PRICE_FILENAME = "{code}_price.csv"

GS_API_KEY = os.environ.get("GS_API_KEY", "")
GS_PAST_HQ = "https://dgzt.guosen.com.cn/skills/gsnews/market/agentbot/queryPastHQInfo/1.0"


def _akshare_symbol(symbol: str) -> str:
    return symbol.lower()


def _fetch_via_akshare(symbol: str, start: dt.date) -> pd.DataFrame:
    last_err = None
    for attempt in range(3):
        try:
            df = ak.stock_zh_index_daily_em(symbol=_akshare_symbol(symbol))
            if df.empty:
                raise RuntimeError("akshare 返回空结果")
            df = df.rename(columns={"date": "date", "close": "close"})
            df["date"] = pd.to_datetime(df["date"])
            df = df.loc[df["date"] >= pd.Timestamp(start)].sort_values("date")
            return df[["date", "close"]]
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"akshare 重试 3 次失败: {last_err}")


def _yf_candidates(price_symbol: str, proxies: Iterable[str]) -> List[str]:
    """指数代码 + 由 sh/sz 前缀派生的 Yahoo 符号 + ETF 代理。"""
    candidates: List[str] = []
    symbol = str(price_symbol)
    if symbol.startswith("sh") and len(symbol) >= 8:
        candidates.append(symbol[2:8] + ".SS")
    elif symbol.startswith("sz") and len(symbol) >= 8:
        candidates.append(symbol[2:8] + ".SZ")
    candidates.append(symbol)
    candidates.extend([p for p in proxies if isinstance(p, str) and p])
    seen = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def _fetch_via_yfinance(symbols: Iterable[str], start: dt.date) -> pd.DataFrame:
    for symbol in symbols:
        if not symbol:
            continue
        try:
            df = yf.download(symbol, start=start.isoformat(), progress=False, auto_adjust=False)
            if df.empty:
                continue
            frame = df.reset_index()[["Date", "Close"]]
            frame.columns = ["date", "close"]
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame.loc[frame["date"] >= pd.Timestamp(start)].dropna(subset=["close"])
            if not frame.empty:
                return frame.sort_values("date")
        except Exception:
            continue
    raise RuntimeError("yfinance 兜底失败")


def _guosen_target(proxies: Iterable[str]) -> Optional[Tuple[str, int]]:
    """取第一个 A 股 ETF 代理作为国信标的（.SS→沪=1, .SZ→深=0）。"""
    for proxy in proxies:
        if not isinstance(proxy, str) or len(proxy) < 9:
            continue
        code, _, suffix = proxy.partition(".")
        if code.isdigit() and len(code) == 6:
            if suffix.upper() == "SS":
                return code, 1
            if suffix.upper() == "SZ":
                return code, 0
    return None


def _legacy_ssl_context():
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers("ALL:@SECLEVEL=0")
            ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        except Exception:
            pass
        return ctx
    except Exception:
        return None


def _curl_get(url: str) -> dict:
    result = subprocess.run(
        ["curl", "-s", "-k", url], capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="ignore",
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"curl 失败: {result.stderr[:150]}")
    return json.loads(result.stdout)


def _fetch_via_guosen(code: str, set_code: int) -> pd.DataFrame:
    params = {
        "code": code, "setCode": str(set_code), "target": 0,
        "softName": "goldsun_skills", "skillName": "gs-stock-market-query",
        "apiKey": GS_API_KEY,
    }
    payload = None
    last_err = ""
    for want in (500, 300, 200):
        params["wantNums"] = str(want)
        url = f"{GS_PAST_HQ}?{urlencode(params)}"
        try:
            req = urllib_request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib_request.urlopen(req, context=_legacy_ssl_context(), timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:150]
            try:
                payload = _curl_get(url)
            except Exception as exc2:  # noqa: BLE001
                last_err = str(exc2)[:150]
                payload = None
                continue
        if not isinstance(payload, dict):
            last_err = f"非预期响应类型: {type(payload).__name__}"
            payload = None
            continue
        result = payload.get("result", {})
        if isinstance(result, list):
            ok = any(isinstance(x, dict) and x.get("code") == 0 for x in result)
            code_msg = result[0] if result and isinstance(result[0], dict) else result
        else:
            ok = isinstance(result, dict) and result.get("code") == 0
            code_msg = result
        if ok and payload.get("object", {}).get("dailyHQList"):
            break
        last_err = str(code_msg)
        payload = None
    if payload is None:
        raise RuntimeError(f"国信返回异常: {last_err}")
    rows = payload["object"]["dailyHQList"]
    frame = pd.DataFrame([{"date": r["date"], "close": float(r["close"])} for r in rows])
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
    return frame.sort_values("date")


def _acceptable(df: Optional[pd.DataFrame], min_rows: int = 30) -> bool:
    """行情至少要 min_rows 条才接受，避免 yfinance 只回 1 条导致回撤=0。"""
    return df is not None and not df.empty and len(df) >= min_rows


def main() -> None:
    indices = [cfg for cfg in load_indices() if cfg.get("class") == "CN_CSI"]
    if not indices:
        raise SystemExit("config/indices.yaml 未配置任何 CN_CSI 指数")

    start_date = dt.date.today() - dt.timedelta(days=365 * 15)

    ok, fail = 0, 0
    for cfg in indices:
        code = cfg["code"]
        price_symbol = str(cfg.get("price_symbol") or code)
        proxies = [p for p in cfg.get("etf_proxies", []) if isinstance(p, str) and p]

        print(f"[CN_CSI] {code} -> {price_symbol}")
        price_df, source = None, None

        # 1) akshare（历史最长，东财健康时最佳）
        try:
            df = _fetch_via_akshare(price_symbol, start_date)
            if _acceptable(df):
                price_df, source = df, "akshare"
        except Exception as exc:  # noqa: BLE001
            print(f"  akshare 失败: {str(exc)[:110]}")

        # 2) yfinance（指数/ETF 代理）
        if price_df is None:
            try:
                df = _fetch_via_yfinance(_yf_candidates(price_symbol, proxies), start_date)
                if _acceptable(df):
                    price_df, source = df, "yfinance"
            except Exception as exc:  # noqa: BLE001
                print(f"  yfinance 失败: {str(exc)[:110]}")

        # 3) 国信（约 2 年，最稳；CI/东财被封时的可靠兜底）
        if price_df is None:
            target = _guosen_target(proxies)
            if target and GS_API_KEY:
                try:
                    df = _fetch_via_guosen(*target)
                    if _acceptable(df, 20):
                        price_df, source = df, f"guosen({target[0]})"
                except Exception as exc:  # noqa: BLE001
                    print(f"  国信兜底失败: {str(exc)[:110]}")

        if price_df is None:
            print(f"  {code} 行情获取失败，跳过（保留旧数据）")
            fail += 1
            continue

        path = RAW_DIR / PRICE_FILENAME.format(code=code)
        price_df.to_csv(path, index=False)
        ok += 1
        print(f"  行情 {len(price_df)} 条，来源 {source}")

    print(f"[CN_CSI] 完成: 成功 {ok} / 失败 {fail}")
    if ok == 0:
        raise SystemExit("CN_CSI 全部失败")


if __name__ == "__main__":
    main()
