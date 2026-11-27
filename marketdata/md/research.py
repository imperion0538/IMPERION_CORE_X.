"""Araştırma orkestratörü: 4 ajanın görevlerini mevcut modüllerle çalıştırır.
Çıktı: yapılandırılmış JSON rapor + özet. LLM entegrasyonu için hazır (opsiyonel)."""
import asyncio
from datetime import datetime, timezone
from . import analysis, advanced, macro, scoring, reports, ta

UTC = timezone.utc

async def market_structure_analyst(coin):
    a = await analysis.analysis(coin)
    structure = a.get("price_structure")
    # TA seviyeleri için basitleştirilmiş (gerçek mumlar gerekliyse genişlet)
    return {
        "agent": "Market Structure Analyst",
        "price": a.get("price"),
        "price_change_pct": a.get("price_change_pct"),
        "structure": structure,
        "orderbook_summary": a.get("orderbook"),
        "heatmap_clusters": a.get("heatmap_24h", {}).get("clusters", [])[:5],
        "status": "observed"
    }

async def flow_liquidity_analyst(coin):
    a = await analysis.analysis(coin)
    adv = await advanced.research(coin)
    return {
        "agent": "Flow & Liquidity Analyst",
        "flows_4h": a.get("flows", {}).get("4h"),
        "cascade": adv.get("cascade") if isinstance(adv, dict) else None,
        "anomalies": adv.get("anomalies") if isinstance(adv, dict) else [],
        "options_flow": adv.get("options_flow") if isinstance(adv, dict) else None,
        "status": "observed"
    }

async def macro_sentiment_analyst(coin):
    mac = await macro.latest_macro()
    return {
        "agent": "Macro & Sentiment Analyst",
        "macro": mac,
        "status": mac.get("status", "insufficient_data"),
        "note": "Sentiment (haber/X) henüz entegre değil; makro gözlenen veriler."
    }

async def synthesis_scenario_writer(coin, structure, flow, macro_data):
    a = await analysis.analysis(coin)
    scores = await scoring.compute_scores(coin)
    # Mevcut summarize + senaryo mantığını kullan
    summary = a.get("summary", [])
    return {
        "agent": "Synthesis & Scenario Writer",
        "summary": summary,
        "direction_bias": scores.get("direction_bias"),
        "scenarios_note": "Koşullu senaryolar TA modülünde; teyit gerektirir.",
        "status": "estimated"
    }

async def run_research(coin="BTC"):
    structure, flow, macro_data = await asyncio.gather(
        market_structure_analyst(coin),
        flow_liquidity_analyst(coin),
        macro_sentiment_analyst(coin),
        return_exceptions=True
    )
    synthesis = await synthesis_scenario_writer(
        coin,
        structure if isinstance(structure, dict) else {},
        flow if isinstance(flow, dict) else {},
        macro_data if isinstance(macro_data, dict) else {}
    )
    scores = await scoring.compute_scores(coin)
    return {
        "coin": coin,
        "generated_utc": datetime.now(UTC).isoformat(),
        "agents": {
            "structure": structure if isinstance(structure, dict) else {"status": "error"},
            "flow": flow if isinstance(flow, dict) else {"status": "error"},
            "macro": macro_data if isinstance(macro_data, dict) else {"status": "error"},
            "synthesis": synthesis
        },
        "scores": scores,
        "status": "observed",
        "disclaimer": "Araştırma amaçlıdır; yatırım tavsiyesi değildir. Veriler gözlenen veya tahmini olarak işaretlenmiştir."
    }

def format_research_text(report):
    lines = [f"<b>{report['coin']} İleri Düzey Piyasa Araştırması</b>", f"Zaman: {report['generated_utc'][11:19]} UTC", ""]
    synth = report["agents"]["synthesis"]
    for line in synth.get("summary", [])[:6]:
        lines.append("• " + line)
    bias = report["scores"].get("direction_bias", {})
    if bias.get("score") is not None:
        lines.append(f"\nYön bias skoru: {bias['score']:+.0f}/100 ({', '.join(bias.get('reasons', [])[:3])})")
    cascade = report["scores"].get("cascade", {})
    if cascade.get("score") is not None:
        lines.append(f"Cascade stress: {cascade['score']:.0f} ({cascade.get('level')})")
    mac = report["agents"]["macro"].get("macro", {})
    if mac.get("dxy"):
        lines.append(f"Makro: DXY {mac['dxy']:.2f} | 10Y {mac.get('us10y') or '-'} | SPX {mac.get('spx') or '-'}")
    lines.append("\n" + report["disclaimer"])
    return "\n".join(lines)
