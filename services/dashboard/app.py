"""Streamlit dashboard for IMPERION Advanced (with Deribit options)."""
import streamlit as st
import requests
import os
import pandas as pd
import plotly.express as px

API_BASE = os.environ.get("API_BASE", "http://localhost:8080")

st.set_page_config(page_title="IMPERION CORE X", layout="wide")
st.title("IMPERION Advanced Market Dashboard")

coin = st.selectbox("Coin", ["BTC", "ETH", "SOL"])

col1, col2 = st.columns(2)

with col1:
    st.subheader("Cascade Risk")
    try:
        risk = requests.get(f"{API_BASE}/api/cascade/{coin}", timeout=5).json()
        st.metric("Risk Score", f"{risk.get('cascade_risk', 0):.1f}", risk.get("level"))
        st.json(risk.get("components", {}))
    except Exception as e:
        st.error(str(e))

with col2:
    st.subheader("Heatmap Clusters")
    try:
        hm = requests.get(f"{API_BASE}/api/heatmap/{coin}", timeout=5).json()
        if hm and hm.get("clusters"):
            df = pd.DataFrame(hm["clusters"])
            fig = px.bar(df, x="price", y="usd", color="side", title=f"{coin} Estimated Liq Clusters")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No heatmap data yet")
    except Exception as e:
        st.error(str(e))

st.subheader("Deribit Options Flow")
col3, col4 = st.columns(2)

with col3:
    try:
        flow = requests.get(f"{API_BASE}/api/options/flow/{coin}", timeout=5).json()
        st.metric("Net Premium USD", f"{flow.get('net_premium_usd', 0):,.0f}")
        st.metric("Put/Call Ratio", flow.get("put_call_ratio", "—"))
        if flow.get("large_trades"):
            st.dataframe(pd.DataFrame(flow["large_trades"]).head(10))
    except Exception:
        st.info("No options flow data yet")

with col4:
    try:
        summ = requests.get(f"{API_BASE}/api/options/{coin}", timeout=5).json()
        st.metric("Max Pain", f"${summ.get('max_pain', 0):,.0f}")
        st.metric("Total OI", f"{summ.get('total_oi', 0):,.0f}")
        if summ.get("top_oi"):
            st.dataframe(pd.DataFrame(summ["top_oi"]).head(8))
    except Exception:
        st.info("No options summary yet")

st.caption("Models are estimates. Not investment advice. Options data from Deribit public API.")
