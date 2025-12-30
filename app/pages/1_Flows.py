import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import timedelta

from app.lib.dts_loader import load_deposits_withdrawals, load_category_map, enrich_with_rollups


st.set_page_config(page_title="Flows (Sankey)", layout="wide")

st.title("Flows through the Treasury General Account (TGA)")
st.caption("MVP: Sum of daily amounts (transaction_today_amt) over the selected date range.")


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
    view_choice = st.selectbox("View", ["Gross flows (Sankey)", "Net by cabinet (table)"], index=0)

with c4:
    show_unmapped = st.checkbox("Show unmapped", value=True)

# Apply year shortcut if chosen
start_date, end_date = date_range
if year_choice != "All":
    y = int(year_choice)
    start_date = pd.Timestamp(year=y, month=1, day=1).date()
    end_date = pd.Timestamp(year=y, month=12, day=31).date()

mask = (df["record_date"].dt.date >= start_date) & (df["record_date"].dt.date <= end_date)
dff = df.loc[mask].copy()

if not show_unmapped:
    dff = dff[dff["cabinet_supercategory"] != "Unmapped"].copy()

# --- Summary metrics
total_deposits = dff.loc[dff["transaction_type"] == "Deposits", "transaction_today_amt"].sum()
total_withdrawals = dff.loc[dff["transaction_type"] == "Withdrawals", "transaction_today_amt"].sum()
net = total_deposits - total_withdrawals

m1, m2, m3 = st.columns(3)
m1.metric("Deposits (sum)", f"${total_deposits:,.0f}")
m2.metric("Withdrawals (sum)", f"${total_withdrawals:,.0f}")
m3.metric("Net (Deposits − Withdrawals)", f"${net:,.0f}")

st.divider()

# --- Net table option
if view_choice.startswith("Net"):
    dep = (
        dff[dff["transaction_type"] == "Deposits"]
        .groupby("cabinet_supercategory", as_index=False)["transaction_today_amt"]
        .sum()
        .rename(columns={"transaction_today_amt": "deposits"})
    )
    wdr = (
        dff[dff["transaction_type"] == "Withdrawals"]
        .groupby("cabinet_supercategory", as_index=False)["transaction_today_amt"]
        .sum()
        .rename(columns={"transaction_today_amt": "withdrawals"})
    )

    net_tbl = dep.merge(wdr, on="cabinet_supercategory", how="outer").fillna(0.0)
    net_tbl["net"] = net_tbl["deposits"] - net_tbl["withdrawals"]
    net_tbl = net_tbl.sort_values("withdrawals", ascending=False)

    st.subheader("Net by cabinet")
    st.dataframe(
        net_tbl.style.format({"deposits": "${:,.0f}", "withdrawals": "${:,.0f}", "net": "${:,.0f}"}),
        use_container_width=True,
    )
    st.stop()

# --- Build Sankey (gross)
dep_by_cab = (
    dff[dff["transaction_type"] == "Deposits"]
    .groupby("cabinet_supercategory", as_index=False)["transaction_today_amt"]
    .sum()
)
wdr_by_cab = (
    dff[dff["transaction_type"] == "Withdrawals"]
    .groupby("cabinet_supercategory", as_index=False)["transaction_today_amt"]
    .sum()
)

# Prefix labels so left/right cabinets stay visually distinct
dep_by_cab["node"] = "Deposits: " + dep_by_cab["cabinet_supercategory"].astype(str)
wdr_by_cab["node"] = "Withdrawals: " + wdr_by_cab["cabinet_supercategory"].astype(str)
tga_node = "Treasury General Account (TGA)"

nodes = dep_by_cab["node"].tolist() + [tga_node] + wdr_by_cab["node"].tolist()
node_index = {label: i for i, label in enumerate(nodes)}

# Links: Deposits cabinets -> TGA
sources = [node_index[x] for x in dep_by_cab["node"]]
targets = [node_index[tga_node]] * len(dep_by_cab)
values = dep_by_cab["transaction_today_amt"].tolist()

# Links: TGA -> Withdrawals cabinets
sources += [node_index[tga_node]] * len(wdr_by_cab)
targets += [node_index[x] for x in wdr_by_cab["node"]]
values += wdr_by_cab["transaction_today_amt"].tolist()

fig = go.Figure(
    data=[
        go.Sankey(
            arrangement="snap",
            textfont=dict(size=14, color="black"),
            node=dict(
                label=nodes,
                pad=15,
                thickness=15,
                line=dict(color="rgba(0,0,0,0.35)", width=1),
            ),
            link=dict(source=sources, target=targets, value=values),
        )
    ]
)
fig.update_layout(
    height=650,
    margin=dict(l=10, r=10, t=10, b=10),
    paper_bgcolor="white",
    plot_bgcolor="white",
    font=dict(color="black"),
)

st.subheader("Cabinet-level gross flows")
st.plotly_chart(fig, use_container_width=True)

st.divider()
st.subheader("Click a cabinet to drill down")

# Cabinet summary table for clicking
dep_tbl = (
    dff[dff["transaction_type"] == "Deposits"]
    .groupby("cabinet_supercategory", as_index=False)["transaction_today_amt"]
    .sum()
    .rename(columns={"transaction_today_amt": "deposits"})
)
wdr_tbl = (
    dff[dff["transaction_type"] == "Withdrawals"]
    .groupby("cabinet_supercategory", as_index=False)["transaction_today_amt"]
    .sum()
    .rename(columns={"transaction_today_amt": "withdrawals"})
)

cab_tbl = dep_tbl.merge(wdr_tbl, on="cabinet_supercategory", how="outer").fillna(0.0)
cab_tbl["net"] = cab_tbl["deposits"] - cab_tbl["withdrawals"]
cab_tbl = cab_tbl.sort_values("withdrawals", ascending=False)

# Streamlit supports "row click selection" on st.dataframe in recent versions
event = st.dataframe(
    cab_tbl.style.format({"deposits": "${:,.0f}", "withdrawals": "${:,.0f}", "net": "${:,.0f}"}),
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

# Persist selection for the Drilldown page
try:
    sel_rows = event.selection.get("rows", [])
    if sel_rows:
        selected_idx = sel_rows[0]
        selected_cab = cab_tbl.iloc[selected_idx]["cabinet_supercategory"]
        st.session_state["selected_cabinet"] = str(selected_cab)
        st.success(f"Selected cabinet: **{selected_cab}**. Now open the **Drilldown** page in the sidebar.")
except Exception:
    st.info("If row selection isn't available, use the Drilldown page dropdown to choose a cabinet.")


# Unmapped diagnostics (super helpful for tightening the mapping)
unmapped = dff[dff["cabinet_supercategory"] == "Unmapped"].copy()
if len(unmapped) > 0:
    st.warning(f"Unmapped rows in this range: {len(unmapped):,}. Fix by adding them to your mapping file.")
    top_unmapped = (
        unmapped.groupby(["transaction_type", "transaction_catg", "transaction_catg_desc"], as_index=False)[
            "transaction_today_amt"
        ]
        .sum()
        .sort_values("transaction_today_amt", ascending=False)
        .head(50)
    )
    st.dataframe(
        top_unmapped.style.format({"transaction_today_amt": "${:,.0f}"}),
        use_container_width=True,
    )
else:
    st.success("No unmapped categories in the selected range.")
