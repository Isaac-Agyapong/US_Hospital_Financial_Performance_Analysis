"""
Load Data/raw/ into PostgreSQL (database hospital_finance) and build the star schema.

    1. SQL/01a_schema_raw.sql       raw schema                      (skipped with --skip-raw)
    2. raw.*                        bulk-load source files with COPY (skipped with --skip-raw)
    3. SQL/01b_schema_core.sql      core + analytics schemas
    4. SQL/02_transform.sql         raw -> core: type, clean, pick one report per hospital-year, derive KPIs
    5. SQL/04_analytics_views.sql   one view per business question (notebook, Power BI and the ML project read these)

    python Python/02_load_postgres.py              full rebuild (~1 min)
    python Python/02_load_postgres.py --skip-raw   rebuild core/analytics from the loaded raw layer

Connection settings come from the standard PG* environment variables (default postgres@localhost:5432);
the password is read by libpq from pgpass.conf (never stored here).
"""
import io
import os
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "Data" / "raw"
SQL = ROOT / "SQL"
DB = os.getenv("PGDATABASE", "hospital_finance")

# source column -> raw column (cost report). Street Address is deliberately not loaded.
COST_COLS = {
    "rpt_rec_num": "rpt_rec_num", "Provider CCN": "provider_ccn", "Hospital Name": "hospital_name", "City": "city",
    "State Code": "state_code", "Zip Code": "zip_code", "County": "county", "Medicare CBSA Number": "cbsa",
    "Rural Versus Urban": "rural_urban", "CCN Facility Type": "facility_type", "Provider Type": "provider_type",
    "Type of Control": "type_of_control", "Fiscal Year Begin Date": "fy_begin", "Fiscal Year End Date": "fy_end",
    "FTE - Employees on Payroll": "fte_employees", "Number of Interns and Residents (FTE)": "fte_residents",
    "Total Days Title XVIII": "days_medicare", "Total Days Title XIX": "days_medicaid",
    "Total Days (V + XVIII + XIX + Unknown)": "days_total", "Number of Beds": "beds",
    "Total Bed Days Available": "bed_days_available", "Total Discharges Title XVIII": "discharges_medicare",
    "Total Discharges Title XIX": "discharges_medicaid", "Total Discharges (V + XVIII + XIX + Unknown)": "discharges_total",
    "Cost of Charity Care": "cost_charity_care", "Total Bad Debt Expense": "bad_debt_expense",
    "Cost of Uncompensated Care": "cost_uncompensated_care", "Total Salaries From Worksheet A": "total_salaries",
    "Total Costs": "total_costs", "Inpatient Total Charges": "inpatient_charges",
    "Outpatient Total Charges": "outpatient_charges", "Combined Outpatient + Inpatient Total Charges": "total_charges",
    "Contract Labor: Direct Patient Care": "contract_labor", "Cash on Hand and in Banks": "cash",
    "Temporary Investments": "temporary_investments", "Accounts Receivable": "accounts_receivable",
    "Total Current Assets": "total_current_assets", "Total Fixed Assets": "total_fixed_assets",
    "Total Assets": "total_assets", "Total Current Liabilities": "total_current_liabilities",
    "Total Long Term Liabilities": "total_long_term_liabilities", "Total Liabilities": "total_liabilities",
    "Total Fund Balances": "total_fund_balances", "Disproportionate Share Adjustment": "dsh_adjustment",
    "Total IME Payment": "ime_payment", "Total Patient Revenue": "total_patient_revenue",
    "Less Contractual Allowance and Discounts on Patients' Accounts": "contractual_allowances",
    "Net Patient Revenue": "net_patient_revenue", "Less Total Operating Expense": "total_operating_expense",
    "Net Income from Service to Patients": "net_income_patients", "Total Other Income": "total_other_income",
    "Total Income": "total_income", "Total Other Expenses": "total_other_expenses", "Net Income": "net_income",
    "Cost To Charge Ratio": "cost_to_charge_ratio", "Net Revenue from Medicaid": "net_revenue_medicaid",
    "Medicaid Charges": "medicaid_charges",
}
INPATIENT_COLS = {
    "Rndrng_Prvdr_CCN": "ccn", "Rndrng_Prvdr_Org_Name": "org_name", "Rndrng_Prvdr_State_Abrvtn": "state",
    "Rndrng_Prvdr_RUCA": "ruca", "Tot_Benes": "total_benes", "Tot_Submtd_Cvrd_Chrg": "submitted_charges",
    "Tot_Pymt_Amt": "total_payment", "Tot_Mdcr_Pymt_Amt": "medicare_payment", "Tot_Dschrgs": "discharges",
    "Tot_Cvrd_Days": "covered_days", "Bene_Avg_Age": "bene_avg_age", "Bene_Dual_Cnt": "bene_dual_cnt",
    "Bene_Avg_Risk_Scre": "bene_avg_risk_score",
}


def conninfo(db=DB):
    return " ".join([f"host={os.getenv('PGHOST', 'localhost')}", f"port={os.getenv('PGPORT', '5432')}",
                     f"user={os.getenv('PGUSER', 'postgres')}", f"dbname={db}"])


CONNINFO = conninfo()


def copy_frame(cur, table, df):
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with cur.copy(f"COPY {table} ({', '.join(df.columns)}) FROM STDIN WITH (FORMAT csv)") as cp:
        while data := buf.read(1 << 20):
            cp.write(data)


def run_sql_file(conn, name):
    t = time.time()
    conn.execute((SQL / name).read_text(encoding="utf-8-sig"))
    conn.commit()
    print(f"  ran {name} ({time.time() - t:.0f}s)")


def ensure_database():
    with psycopg.connect(conninfo("postgres"), autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB,)).fetchone():
            return
        print("creating database", DB)
        for stmt in (SQL / "00_create_database.sql").read_text(encoding="utf-8-sig").split(";"):
            body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
            if body:
                admin.execute(body)
        admin.execute("ALTER SYSTEM SET max_wal_size = '256MB'")
        admin.execute("SELECT pg_reload_conf()")


def read(path, cols):
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="latin-1")
    missing = set(cols) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns {sorted(missing)}")
    return df[list(cols)].rename(columns=cols).apply(lambda s: s.str.strip())


def load_raw(conn):
    with conn.cursor() as cur:
        for f in sorted(RAW.glob("cost_report_*.csv")):
            df = read(f, COST_COLS)
            df.insert(0, "file_year", f.stem[-4:])
            copy_frame(cur, "raw.cost_report", df)
            print(f"  raw.cost_report {f.stem[-4:]}: {len(df):>6,} rows")
        for f in sorted(RAW.glob("inpatient_*.csv")):
            df = read(f, INPATIENT_COLS)
            df.insert(0, "data_year", f.stem[-4:])
            copy_frame(cur, "raw.inpatient", df)
            print(f"  raw.inpatient   {f.stem[-4:]}: {len(df):>6,} rows")
        kff = pd.read_csv(RAW / "kff_expansion_status.csv", dtype=str, keep_default_na=False)
        copy_frame(cur, "raw.kff_expansion", kff)
        conn.commit()


def main():
    start = time.time()
    ensure_database()
    with psycopg.connect(CONNINFO) as conn:
        if "--skip-raw" not in sys.argv:
            print("raw layer")
            run_sql_file(conn, "01a_schema_raw.sql")
            load_raw(conn)
        if "--raw-only" in sys.argv:
            return
        print("core layer")
        run_sql_file(conn, "01b_schema_core.sql")
        run_sql_file(conn, "02_transform.sql")
        print("analytics layer")
        run_sql_file(conn, "04_analytics_views.sql")
        for t in ["core.dim_state", "core.dim_hospital", "core.fact_hospital_year", "core.fact_inpatient_year"]:
            print(f"  {t:<28} {conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>8,} rows")
    print(f"done in {(time.time() - start) / 60:.1f} min")


if __name__ == "__main__":
    main()
