"""VOO/BND $100/month DCA: Total Return (dividends reinvested) vs Price Return.

Runs in a throwaway container on the server (nothing is installed on the host):
  sudo docker run --rm -v "$PWD/scripts:/s:ro" python:3.12-slim \
    sh -c "pip -q install yfinance >/dev/null 2>&1 && python /s/total_return.py"

Source: Yahoo Finance monthly bars. 'Adj Close' includes reinvested distributions (total return);
'Close' is price only. Same method as the price-only figures shown to the owner (monthly buy at
month-end close, January rebalance), so the two can be compared directly.
"""
import json

import yfinance as yf

START, END = "2016-10-01", "2026-10-01"


def monthly(sym: str, adjusted: bool) -> dict:
    df = yf.download(sym, start=START, end=END, interval="1mo", auto_adjust=False, progress=False)
    col = "Adj Close" if adjusted else "Close"
    s = df[col]
    if hasattr(s, "columns"):
        s = s.iloc[:, 0]
    return {d.strftime("%Y-%m"): float(v) for d, v in s.dropna().items()}


def dca(voo: dict, bnd: dict, w: float) -> dict:
    months = sorted(m for m in voo if m in bnd and "2016-10" <= m <= "2026-09")
    sv = sb = inv = 0.0
    peak, mdd = 0.0, 0.0
    for i, m in enumerate(months):
        sv += 100 * w / voo[m]
        sb += 100 * (1 - w) / bnd[m]
        inv += 100
        val = sv * voo[m] + sb * bnd[m]
        if m.endswith("-01") and i > 0:
            sv, sb = val * w / voo[m], val * (1 - w) / bnd[m]
        r = val / inv
        peak = max(peak, r)
        mdd = min(mdd, r / peak - 1)
    return {"months": len(months), "first": months[0], "last": months[-1], "invested": inv,
            "final": round(val), "gain_pct": round((val / inv - 1) * 100, 1), "max_drawdown_pct": round(mdd * 100, 1)}


out = {"source": "Yahoo Finance via yfinance", "period": [START, END], "results": {}}
for label, adjusted in (("total_return", True), ("price_only", False)):
    voo, bnd = monthly("VOO", adjusted), monthly("BND", adjusted)
    out["results"][label] = {name: dca(voo, bnd, w) for name, w in (("100/0", 1.0), ("80/20", 0.8), ("60/40", 0.6))}
print(json.dumps(out, indent=1))
