"""Streamlit dashboard for IMPERION Advanced."""
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

st.caption("Models are estimates. Not investment advice.")
