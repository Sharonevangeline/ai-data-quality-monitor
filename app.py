import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os

from checker import run_all_checks, get_db_tables
from llm_report import generate_report

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="AI Data Quality Monitor",
    page_icon=None,
    layout="wide",
)

st.title("AI-Powered Data Quality Monitor")
st.caption("Upload a CSV or connect to a SQLite database — automated checks + AI report generation")

# ─────────────────────────────────────────────
# SIDEBAR — Data Source Selection
# ─────────────────────────────────────────────
source_path = None
table_name = None
ready_to_run = False

st.sidebar.header("Data Source")
source_type = st.sidebar.radio(
    "Select source type",
    ["CSV File", "SQLite Database"],
    help="Mode 1: CSV upload | Mode 2: SQLite database"
)

# ── Mode 1: CSV ──────────────────────────
if source_type == "CSV File":
    st.sidebar.markdown("**Upload your CSV file:**")
    uploaded_file = st.sidebar.file_uploader(
        "Choose a CSV file",
        type=["csv"],
        label_visibility="collapsed"
    )
    if uploaded_file is not None:
        save_path = "temp_upload.csv"
        with open(save_path, "wb") as f:
            f.write(uploaded_file.read())
        source_path = save_path
        st.sidebar.success(f"Ready: {uploaded_file.name}")
        ready_to_run = True

# ── Mode 2: SQLite ───────────────────────
elif source_type == "SQLite Database":
    st.sidebar.markdown("**Database file path:**")
    db_input = st.sidebar.text_input(
        "Database path",
        placeholder="e.g. sample_data.db",
        label_visibility="collapsed"
    )
    if db_input and os.path.exists(db_input):
        tables = get_db_tables(db_input)
        if tables:
            table_name = st.sidebar.selectbox("Select table", tables)
            source_path = db_input
            ready_to_run = True
            st.sidebar.success(f"Connected: {len(tables)} table(s) found")
        else:
            st.sidebar.error("No tables found in this database.")
    elif db_input:
        st.sidebar.error("File not found. Check the path.")

st.sidebar.divider()

# ── Optional: expected schema ────────────
st.sidebar.subheader("Schema Validation (optional)")
use_schema = st.sidebar.checkbox("Enable schema drift detection")
expected_schema = None
if use_schema:
    st.sidebar.caption("Enter column:dtype pairs, one per line. E.g.  age:float64")
    schema_text = st.sidebar.text_area("Expected schema", height=120)
    if schema_text:
        try:
            expected_schema = dict(
                line.strip().split(":", 1)
                for line in schema_text.strip().splitlines()
                if ":" in line
            )
            st.sidebar.success(f"{len(expected_schema)} columns defined")
        except Exception:
            st.sidebar.warning("Could not parse schema — check formatting")

st.sidebar.divider()
run_btn = st.sidebar.button(
    "Run Quality Checks",
    disabled=not ready_to_run,
    width="stretch",
    type="primary",
)

# ─────────────────────────────────────────────
# MAIN AREA
# ─────────────────────────────────────────────

if not ready_to_run:
    st.info("Select a data source in the sidebar to get started.")
    with st.expander("Quick start — try sample files"):
        st.markdown("""
        **Mode 1 (CSV):** Upload the `sample_data.csv` file included in this repo.

        **Mode 2 (SQLite):** Enter `sample_data.db` in the database path field,
        then select the `customers` or `orders` table.
        """)
    st.stop()

# ── Run checks when button is clicked ────────
if run_btn:
    with st.spinner("Running checks..."):
        try:
            results, df = run_all_checks(
                source=source_path,
                table_name=table_name,
                expected_schema=expected_schema,
            )
            st.session_state["results"] = results
            st.session_state["df"] = df
        except Exception as e:
            st.error(f"Error loading data: {e}")
            st.stop()

# Retrieve from session state so results persist across reruns
if "results" not in st.session_state:
    st.info("Click **Run Quality Checks** in the sidebar to begin.")
    st.stop()

results = st.session_state["results"]
df = st.session_state["df"]
profile = results["dataset_profile"]
checks = results["checks"]
overall = results["overall_status"]

# ─────────────────────────────────────────────
# METRICS ROW
# ─────────────────────────────────────────────
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Rows", f"{profile['row_count']:,}")
m2.metric("Columns", profile["column_count"])
m3.metric("Checks Run", len(checks))
m4.metric("Source", "CSV" if source_type == "CSV File" else "SQLite")

status_map = {"PASS": "🟢 PASS", "WARN": "🟡 WARN", "FAIL": "🔴 FAIL"}
m5.metric("Overall Status", status_map.get(overall, overall))

st.divider()

# ─────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["Dashboard", "Check Details", "Data Preview", "AI Report"])

# ── TAB 1: Dashboard ─────────────────────────
with tab1:
    col_left, col_right = st.columns(2)

    with col_left:
        # Check status summary chart
        check_names = [c["check"].replace("_", " ").title() for c in checks]
        check_statuses = [c["status"] for c in checks]
        color_map = {"PASS": "#22c55e", "WARN": "#f59e0b", "FAIL": "#ef4444"}
        colors = [color_map.get(s, "#888") for s in check_statuses]

        fig_status = go.Figure(go.Bar(
            x=check_names,
            y=[1] * len(checks),
            marker_color=colors,
            text=check_statuses,
            textposition="inside",
        ))
        fig_status.update_layout(
            title="Check Results Overview",
            showlegend=False,
            yaxis=dict(visible=False),
            height=300,
            margin=dict(t=40, b=10, l=10, r=10),
        )
        st.plotly_chart(fig_status, use_container_width=True)

    with col_right:
        # Null values bar chart
        null_check = next((c for c in checks if c["check"] == "null_values"), None)
        if null_check and null_check["affected_columns"]:
            null_df = pd.DataFrame([
                {"Column": col, "Null %": v["null_percent"], "Count": v["null_count"]}
                for col, v in null_check["affected_columns"].items()
            ])
            fig_null = px.bar(
                null_df,
                x="Column",
                y="Null %",
                text="Count",
                title="Null Values by Column (%)",
                color="Null %",
                color_continuous_scale="Reds",
            )
            fig_null.update_layout(height=300, margin=dict(t=40, b=10, l=10, r=10))
            st.plotly_chart(fig_null, use_container_width=True)
        else:
            st.success("No null values found.")

    # Numeric distributions
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if numeric_cols:
        st.subheader("Numeric Column Distributions")
        selected_col = st.selectbox("Select column", numeric_cols)
        fig_hist = px.histogram(
            df,
            x=selected_col,
            nbins=30,
            title=f"Distribution of '{selected_col}'",
            marginal="box",
        )
        fig_hist.update_layout(height=320, margin=dict(t=40, b=10))
        st.plotly_chart(fig_hist, use_container_width=True)


# ── TAB 2: Check Details ─────────────────────
with tab2:
    st.subheader("Detailed Check Results")
    for check in checks:
        status = check["status"]
        icon = "✅" if status == "PASS" else "⚠️" if status == "WARN" else "❌"
        label = check["check"].replace("_", " ").title()

        with st.expander(f"{icon} {label} — {status}"):
            # Pretty-print key fields
            if check["check"] == "null_values":
                if check["affected_columns"]:
                    st.dataframe(
                        pd.DataFrame(check["affected_columns"]).T.reset_index()
                        .rename(columns={"index": "column"}),
                        width="stretch",
                    )
                else:
                    st.write("No nulls detected.")

            elif check["check"] == "duplicates":
                st.write(f"**Duplicate rows:** {check['duplicate_row_count']}")
                st.write(f"**% of data:** {check['percent_of_data']}%")
                if check["sample_duplicates"]:
                    st.dataframe(pd.DataFrame(check["sample_duplicates"]), width="stretch")

            elif check["check"] == "outliers":
                if check["affected_columns"]:
                    for col, info in check["affected_columns"].items():
                        st.write(f"**{col}** — {info['outlier_count']} outlier(s), expected range: {info['expected_range']}")
                        st.write(f"Outlier values: `{info['outlier_values']}`")
                else:
                    st.write("No outliers detected.")

            elif check["check"] == "schema_drift":
                if check["drifted_columns"]:
                    st.dataframe(
                        pd.DataFrame(check["drifted_columns"]).T.reset_index()
                        .rename(columns={"index": "column"}),
                        width="stretch",
                    )
                else:
                    st.write("No schema drift detected.")
                    if check.get("current_schema"):
                        st.write("**Current schema:**")
                        st.json(check["current_schema"])

            elif check["check"] == "date_validity":
                if check["affected_columns"]:
                    for col, info in check["affected_columns"].items():
                        st.write(f"**{col}** — {info['invalid_count']} invalid date(s)")
                        st.write(f"Sample invalid values: `{info['sample_invalid_values']}`")
                else:
                    st.write("All date columns parsed successfully.")

            # Raw JSON toggle
            if st.toggle("Show raw JSON", key=check["check"]):
                st.json(check)


# ── TAB 3: Data Preview ──────────────────────
with tab3:
    st.subheader("Data Preview")
    st.caption(f"Showing {min(100, len(df))} of {len(df)} rows from: `{profile['source']}`")

    # Highlight nulls
    def highlight_nulls(val):
        return "background-color: #fee2e2" if pd.isnull(val) else ""

    st.dataframe(
        df.head(100).style.map(highlight_nulls),
        width="stretch",
        height=400,
    )

    st.subheader("Column Data Types")
    dtype_df = pd.DataFrame(
        [(col, str(df[col].dtype), df[col].nunique(), df[col].isnull().sum())
         for col in df.columns],
        columns=["Column", "Type", "Unique Values", "Null Count"]
    )
    st.dataframe(dtype_df, width="stretch")


# ── TAB 4: AI Report ─────────────────────────
with tab4:
    st.subheader("AI-Generated Quality Report")
    st.caption("Uses LLM to turn raw check results into a plain-English stakeholder report.")

    if st.button("Generate Report with LLM", type="primary"):
        with st.spinner("LM is analyzing your data..."):
            report = generate_report(results)
        st.session_state["report"] = report

    if "report" in st.session_state:
        st.markdown(st.session_state["report"])
        st.divider()

        dl_col1, dl_col2 = st.columns(2)
        with dl_col1:
            st.download_button(
                label="Download Report (.txt)",
                data=st.session_state["report"],
                file_name="quality_report.txt",
                mime="text/plain",
                width="stretch",
            )
        with dl_col2:
            import json as _json
            st.download_button(
                label="Download Raw Results (.json)",
                data=_json.dumps(results, indent=2, default=str),
                file_name="results.json",
                mime="application/json",
                width="stretch",
            )
    else:
        st.info("Click the button above to generate your AI report.")
