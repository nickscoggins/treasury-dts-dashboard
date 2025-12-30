import sys
from pathlib import Path
from datetime import timedelta

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.lib.dts_loader import load_deposits_withdrawals, load_category_map, enrich_with_rollups


st.set_page_config(page_title="Drilldown (Cabinet → Agency → Program)", layout="wide")
st.title("Drilldown: Cabinet → Agency → Program")


@st.cache_data(show_spinner=True)
def get_enriched() -> pd.DataFrame:
    df = load_deposits_withdrawals()
    mapping = load_category_map()
    return enrich_with_rollups(df, mapping)


df = get_enriched()

# --- Controls
max_date = df["record_date"].max()
min_date = df["record_date"].min()
default_start = max_date - timedelta(days=365)

c1, c2, c3, c4 = st.columns([2, 1, 1, 1])

with c1:
    date_range = st.date_input(
        "Date range",
        value=(default_start.date(), max_date.date()),
        min_value=min_date.date(),
        max_value=max_date.date(),
    )

with c2:
    years = sorted(df["record_date"].dt.year.unique().tolist())
    year_choice = st.selectbox("Year (optional)", ["All"] + [str(y) for y in years], index=0)

with c3:
    txn_type = st.selectbox("Transaction type", ["Withdrawals", "Deposits"], index=0)

with c4:
    show_unmapped = st.checkbox("Show unmapped", value=True)

start_date, end_date = date_range
if year_choice != "All":
    y = int(year_choice)
    start_date = pd.Timestamp(year=y, month=1, day=1).date()
    end_date = pd.Timestamp(year=y, month=12, day=31).date()

mask = (df["record_date"].dt.date >= start_date) & (df["record_date"].dt.date <= end_date)
dff = df.loc[mask].copy()

if not show_unmapped:
    dff = dff[dff["cabinet_supercategory"] != "Unmapped"].copy()

# Pick default cabinet
cab_options = sorted(dff["cabinet_supercategory"].dropna().unique().tolist())
if not cab_options:
    st.error("No data available for the selected date range.")
    st.stop()

default_cab = st.session_state.get("selected_cabinet")
if default_cab not in cab_options:
    # fallback: choose the cabinet with largest withdrawals/deposits in this window
    tmp = (
        dff[dff["transaction_type"] == txn_type]
        .groupby("cabinet_supercategory", as_index=False)["transaction_today_amt"]
        .sum()
        .sort_values("transaction_today_amt", ascending=False)
    )
    default_cab = tmp.iloc[0]["cabinet_supercategory"] if len(tmp) else cab_options[0]

cabinet = st.selectbox("Cabinet", cab_options, index=cab_options.index(default_cab))

# Filter to cabinet + transaction type
x = dff[(dff["cabinet_supercategory"] == cabinet) & (dff["transaction_type"] == txn_type)].copy()

if x.empty:
    st.warning("No rows for that cabinet/transaction type in the selected range.")
    st.stop()

# --- Summary
total_amt = x["transaction_today_amt"].sum()
st.metric(f"{txn_type} total (sum)", f"${total_amt:,.0f}")

# --- Control chart density
st.subheader("Sankey: Cabinet → Agency → Program")
per_agency_top = st.slider("Programs per agency (top N)", min_value=5, max_value=50, value=20, step=5)

# Agency totals
agency_totals = (
    x.groupby("agency_rollup", as_index=False)["transaction_today_amt"]
    .sum()
    .rename(columns={"transaction_today_amt": "agency_total"})
    .sort_values("agency_total", ascending=False)
)

# Program totals per agency
prog = (
    x.groupby(["agency_rollup", "program_rollup"], as_index=False)["transaction_today_amt"]
    .sum()
    .rename(columns={"transaction_today_amt": "amt"})
)

# Keep top N programs per agency; bucket the rest into "Other"
prog = prog.sort_values(["agency_rollup", "amt"], ascending=[True, False])
prog["rank_within_agency"] = prog.groupby("agency_rollup")["amt"].rank(method="first", ascending=False)

top_prog = prog[prog["rank_within_agency"] <= per_agency_top].copy()
other_prog = prog[prog["rank_within_agency"] > per_agency_top].copy()

if not other_prog.empty:
    other_prog = (
        other_prog.groupby("agency_rollup", as_index=False)["amt"]
        .sum()
        .assign(program_rollup="Other (all remaining programs)")
    )
    prog2 = pd.concat([top_prog.drop(columns=["rank_within_agency"]), other_prog], ignore_index=True)
else:
    prog2 = top_prog.drop(columns=["rank_within_agency"])

# --- Build Sankey nodes
cab_node = f"{cabinet}"
agency_nodes = [f"Agency: {a}" for a in agency_totals["agency_rollup"].astype(str).tolist()]

# Programs can repeat across agencies, so prefix with agency to keep them unique
prog2["program_node"] = "Program: " + prog2["agency_rollup"].astype(str) + " → " + prog2["program_rollup"].astype(str)
program_nodes = prog2["program_node"].tolist()

nodes = [cab_node] + agency_nodes + program_nodes
node_index = {label: i for i, label in enumerate(nodes)}

sources = []
targets = []
values = []

# Links: Cabinet -> Agency
for _, r in agency_totals.iterrows():
    a = str(r["agency_rollup"])
    amt = float(r["agency_total"])
    sources.append(node_index[cab_node])
    targets.append(node_index[f"Agency: {a}"])
    values.append(amt)

# Links: Agency -> Program
for _, r in prog2.iterrows():
    a = str(r["agency_rollup"])
    pnode = r["program_node"]
    amt = float(r["amt"])
    sources.append(node_index[f"Agency: {a}"])
    targets.append(node_index[pnode])
    values.append(amt)

fig = go.Figure(
    data=[
        go.Sankey(
            arrangement="snap",
            node=dict(label=nodes, pad=15, thickness=15),
            link=dict(source=sources, target=targets, value=values),
        )
    ]
)
fig.update_layout(height=750, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, use_container_width=True)

# --- Tables
st.subheader("Top agencies")
st.dataframe(agency_totals, use_container_width=True, hide_index=True)

st.subheader("Top programs (within agencies)")
top_programs = (
    prog.sort_values("amt", ascending=False)[["agency_rollup", "program_rollup", "amt"]]
    .head(100)
)
st.dataframe(top_programs, use_container_width=True, hide_index=True)

# Unmapped hint
if show_unmapped:
    unm = x[(x["agency_rollup"] == "Unmapped") | (x["program_rollup"] == "Unmapped")]
    if len(unm) > 0:
        st.warning(
            f"This cabinet has {len(unm):,} rows with unmapped agency/program in the selected range. "
            "Update your mapping file to improve drilldown."
        )
