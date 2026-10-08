"""Fetch theme-index data not covered by djeva.

Valuation: 中证官网指数估值静态表 (akshare stock_zh_index_value_csindex),
latest snapshot only (~20 rows, no long history -> no percentile).
Prices: 国信 queryPastHQInfo via env GS_API_KEY (<=500 trading days ~ 2y).

Requires env: GS_API_KEY. Without it the script exits 0 with a warning
(so CI without the secret keeps old data instead of failing the run).
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlencode

import akshare as ak
import pandas as pd

try:
    from .common import ensure_data_dir, load_indices
except ImportError:  # pragma: no cover - direct execution fallback
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from scripts.common import ensure_data_dir, load_indices  # type: ignore


GS_API_KEY = os.environ.get("GS_API_KEY", "")
GS_BASE = "https://dgzt.guosen.com.cn/skills"
GS_PAST_HQ = f"{GS_BASE}/gsnews/market/agentbot/queryPastHQInfo/1.0"
WANT_NUMS = 500

THEME_VAL_DIR = ensure_data_dir("raw", "theme")
PRICE_DIR = ensure_data_dir("raw", "cn_theme")


def _fetch_valuation(cfg: dict) -> None:
    code = cfg["code"]
    symbol = str(cfg.get("csindex_symbol") or code)
    df = ak.stock_zh_index_value_csindex(symbol=symbol)
    if df.empty:
        raise RuntimeError("中证估值返回空")
    df["日期"] = pd.to_datetime(df["日期"])
    last = df.sort_values("日期").iloc[-1]
    # 注意：中证股息率为百分制（如 2.35 即 2.35%），除以 100 与蛋卷口径（小数）对齐
    rec = pd.DataFrame([{
        "date": pd.to_datetime(last["日期"]).date().isoformat(),
        # 市盈率1/股息率1 为中证 headline 口径
        "pe": float(last["市盈率1"]),
        "pb": "",
        "dividend_yield": float(last["股息率1"]) / 100.0,
        "roe": "",
        "source": "csindex",
    }])
    path = THEME_VAL_DIR / f"{code}_valuation.csv"
    if path.exists():
        old = pd.read_csv(path)
        rec = pd.concat([old, rec], ignore_index=True)
        rec = rec.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    rec.to_csv(path, index=False)
    print(f"[theme-val] {code} -> PE={rec.iloc[-1]['pe']} 股息={rec.iloc[-1]['dividend_yield']} (累计 {len(rec)} 条)")


def _legacy_ssl_context():
    """兼容国信旧服务器 TLS renegotiation（同 gs-stock-market-query skill 逻辑）。"""
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
        ["curl", "-s", "-k", url],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="ignore",
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"curl 失败: {result.stderr[:200]}")
    return json.loads(result.stdout)


def _fetch_price(cfg: dict) -> None:
    code = cfg["code"]
    params = {
        "code": str(cfg["gs_code"]),
        "setCode": str(cfg["gs_setcode"]),
        "wantNums": str(WANT_NUMS),
        "target": 0,
        "softName": "goldsun_skills",
        "skillName": "gs-stock-market-query",
        "apiKey": GS_API_KEY,
    }
    url = f"{GS_PAST_HQ}?{urlencode(params)}"
    payload = None
    last_err = ""
    # 部分标的对 wantNums 上限更低（如 159326 仅支持 300），逐级降级重试
    for want in (WANT_NUMS, 300, 200):
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
        result = (payload or {}).get("result", {})
        if result.get("code") == 0 and payload.get("object", {}).get("dailyHQList"):
            break
        last_err = str(result)
        payload = None
    if payload is None:
        raise RuntimeError(f"国信返回异常: {last_err}")
    rows = payload.get("object", {}).get("dailyHQList", [])
    if not rows:
        raise RuntimeError("国信返回空行情")
    frame = pd.DataFrame([{"date": r["date"], "close": float(r["close"])} for r in rows])
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d")
    frame = frame.sort_values("date")
    path = PRICE_DIR / f"{code}_price.csv"
    frame.to_csv(path, index=False)
    print(f"[theme-price] {code} -> 行情 {len(frame)} 条 ({frame['date'].min().date()}~{frame['date'].max().date()})")


def main() -> None:
    indices = [cfg for cfg in load_indices() if cfg.get("class") == "CN_THEME"]
    if not indices:
        raise SystemExit("config/indices.yaml 未配置任何 CN_THEME 指数")
    if not GS_API_KEY:
        print("[theme] 未设置 GS_API_KEY，跳过主题指数抓取（保留旧数据）")
        return
    ok, fail = 0, 0
    for cfg in indices:
        code = cfg["code"]
        try:
            _fetch_valuation(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[theme-val] {code} 失败: {exc}")
            fail += 1
            continue
        try:
            _fetch_price(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[theme-price] {code} 失败: {exc}")
            fail += 1
            continue
        ok += 1
    print(f"[theme] 完成: 成功 {ok} / 失败 {fail}")
    if ok == 0:
        raise SystemExit("主题指数全部抓取失败")


if __name__ == "__main__":
    main()
