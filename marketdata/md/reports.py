"""Kaynak durumu ve fiyat tepkisiyle yapılandırılmış piyasa araştırması."""
import asyncio
from datetime import datetime, timezone

from . import analysis, config, fmt, heatmap, orderbook, whales

UTC = timezone.utc


def flow_totals(rows):
    buy = sum(row["buy_usd"] for row in rows)
    sell = sum(row["sell_usd"] for row in rows)
    return {"buy_usd": buy, "sell_usd": sell, "net_usd": buy - sell}


def nearest(clusters, side):
    return min((c for c in clusters if c["side"] == side), key=lambda c: abs(c["dist_pct"]), default=None)


async def generate_report(coin="BTC", focus="whale_liquidity", window="15m", min_price=None, max_price=None):
    results = await asyncio.gather(
        analysis.analysis(coin), heatmap.model(coin, "4h"), orderbook.latest(coin),
        whales.report(coin, window, min_price, max_price, limit=10), return_exceptions=True,
    )
    names = ("analysis", "heatmap", "orderbook", "whale_flow")
    sources = {name: "available" if isinstance(item, dict) else
               "error" if isinstance(item, Exception) else "missing" for name, item in zip(names, results)}
    a, hm, depth, flow = (item if isinstance(item, dict) else {} for item in results)
    f4 = a.get("flows", {}).get("4h", {})
    result = {
        "coin": coin, "focus": focus, "generated_utc": datetime.now(UTC).isoformat(),
        "sources": sources, "price": a.get("price"), "analysis_utc": a.get("generated_utc"),
        "summary": a.get("summary", []), "price_structure": a.get("price_structure"),
        "derivatives": a.get("derivatives"), "options": a.get("options"),
        "exchange_flows": {
            "cvd_spot": f4.get("cvd", {}).get("spot", {}).get("delta"),
            "cvd_perp": f4.get("cvd", {}).get("perp", {}).get("delta"),
            "whales_net_4h": f4.get("whales", {}).get("net_usd"),
            "liquidations_4h": f4.get("liquidations"), "coverage": f4.get("coverage"),
        },
        "risk_clusters": {
            "estimated": True, "nearest_long": nearest(hm.get("clusters", []), "long"),
            "nearest_short": nearest(hm.get("clusters", []), "short"),
            "venues_used": hm.get("venues_used"), "generated_utc": hm.get("generated_utc"),
        },
        "orderbook_imbalance": {**depth.get("bands", {}).get("1", {}), "ts": depth.get("ts"),
                                "venues": depth.get("sources")} if depth else None,
    }
    if flow:
        rows = flow["by_venue_market"]
        result["whale_liquidity"] = {**flow, **flow_totals(rows),
                                    "recent_trades": flow["trades"][:5],
                                    "by_market": {market: flow_totals([r for r in rows if r["market"] == market])
                                                  for market in ("spot", "perp")}}
    else:
        result["whale_liquidity"] = None
    return result


def format_report_text(report):
    lines = [f"<b>{fmt.escape(report['coin'])} piyasa araştırması</b>",
             f"Rapor: {report['generated_utc'][11:19]} UTC"]
    if report["price"] is not None:
        lines.append(f"Referans fiyat: ${report['price']:,.2f}")
    lines.extend("• " + fmt.escape(line) for line in report["summary"][:8])
    if report["whale_liquidity"]:
        lines.extend(("", fmt.whale_flow_text(report["whale_liquidity"] | {
            "trades": report["whale_liquidity"]["recent_trades"],
        })))
    missing = [name for name, status in report["sources"].items() if status != "available"]
    if missing:
        lines.append("Eksik bölümler: " + ", ".join(missing))
    return "\n".join(lines)
