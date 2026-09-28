"""Export the tables the Power BI report imports to dashboard/data/*.csv.

The report reads small CSV extracts instead of connecting to PostgreSQL, so anyone who clones the repository
can open the dashboard without a database. Every number comes from the analytics views; nothing is recalculated
here beyond reshaping and ranking.

    hospital_year     one row per study hospital per year (the grain most visuals use, so clicks cross-filter)
    hospital_profile  one row per hospital: latest year, loss streak and rank, for the "Look up a hospital" page
    states            50 states + DC with Medicaid status and tile-map position
    unpaid_care       uncompensated care share by year, expansion vs non-expansion states
    cost_trend        cost per stay, agency staff share and price markup by year
    billed_state      what hospitals bill Medicare per dollar paid, by state (2023) and nationally by year
"""
import sys
from importlib import import_module
from pathlib import Path

import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dashboard" / "data"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "Python"))
CONNINFO = import_module("02_load_postgres").CONNINFO
STATE_GRID = import_module("viz_style").STATE_GRID


def q(conn, sql):
    cur = conn.execute(sql)
    df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    for c in df.columns:
        if df[c].dtype == object and df[c].map(lambda v: v.__class__.__name__ == "Decimal").any():
            df[c] = df[c].astype(float)
    return df


def save(df, name):
    df.to_csv(OUT / f"{name}.csv", index=False)
    print(f"  {name:<18} {len(df):>7,} rows")


def main():
    with psycopg.connect(CONNINFO) as conn:
        # stressed = lost money this year and last year (same rule as analytics.v_financial_stress)
        hy = q(conn, """
            WITH h AS (
                SELECT v.*, lag(net_income)  OVER (PARTITION BY ccn ORDER BY fiscal_year) AS prev_net_income,
                            lag(fiscal_year) OVER (PARTITION BY ccn ORDER BY fiscal_year) AS prev_year
                FROM analytics.v_hospital_year v
            )
            SELECT ccn, fiscal_year, state_abbrev,
                   CASE hospital_type WHEN 'Critical access' THEN 'Critical access (small rural)'
                                      ELSE 'General hospital' END                          AS hospital_type,
                   ownership, coalesce(rural_urban, 'Unknown') AS rural_urban, medicaid_status,
                   round(total_revenue) AS total_revenue, round(net_income) AS net_income,
                   round(total_margin, 4) AS total_margin,
                   (net_income < 0)::int AS loss,
                   CASE WHEN prev_year = fiscal_year - 1 AND rural_urban IS NOT NULL THEN 1 ELSE 0 END AS has_prior,
                   CASE WHEN prev_year = fiscal_year - 1 AND rural_urban IS NOT NULL
                        THEN (net_income < 0 AND prev_net_income < 0)::int ELSE 0 END      AS stressed,
                   round(medicaid_day_share, 4) AS medicaid_day_share
            FROM h ORDER BY ccn, fiscal_year""")
        hy["group_label"] = (hy.rural_urban + " " + hy.ownership.str.lower() + " " +
                             hy.hospital_type.map({"General hospital": "general hospitals",
                                                   "Critical access (small rural)": "critical access"}))
        hy.loc[hy.rural_urban == "Unknown", "group_label"] = "Unknown"
        # years in a row with a loss, up to and including this year (a missing year breaks the streak)
        streak, prev = [], (None, None, 0)
        for ccn, yr, loss in hy[["ccn", "fiscal_year", "loss"]].itertuples(index=False):
            run = (prev[2] + 1 if prev[0] == ccn and prev[1] == yr - 1 else 1) if loss else 0
            streak.append(run)
            prev = (ccn, yr, run)
        hy["loss_streak"] = streak
        # Medicaid share fifths within each year (1 = fewest Medicaid patients)
        ok = hy.medicaid_day_share.between(0, 1)
        hy.loc[ok, "medicaid_fifth"] = hy[ok].groupby("fiscal_year").medicaid_day_share.transform(
            lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) + 1)
        hy["medicaid_fifth"] = hy.medicaid_fifth.astype("Int64")
        hy["medicaid_fifth_label"] = hy.medicaid_fifth.map({1: "Fewest Medicaid", 2: "2nd fifth", 3: "Middle",
                                                            4: "4th fifth", 5: "Most Medicaid"})
        save(hy.drop(columns="medicaid_day_share"), "hospital_year")

        st = q(conn, "SELECT state_abbrev, state_name, medicaid_status FROM core.dim_state ORDER BY 1")
        st["tile_x"] = st.state_abbrev.map(lambda a: STATE_GRID[a][0])
        st["tile_y"] = st.state_abbrev.map(lambda a: STATE_GRID[a][1])
        save(st, "states")

        prof = q(conn, """
            WITH last AS (
                SELECT DISTINCT ON (ccn) ccn, fiscal_year AS last_year, total_margin, net_income, total_revenue, beds
                FROM analytics.v_hospital_year ORDER BY ccn, fiscal_year DESC
            ), streak AS (          -- consecutive loss years ending in the hospital's latest year (as v_loss_streaks)
                SELECT f.ccn, count(*) AS loss_years
                FROM (SELECT ccn, fiscal_year, net_income < 0 AS loss,
                             fiscal_year - row_number() OVER (PARTITION BY ccn, net_income < 0 ORDER BY fiscal_year) AS grp
                      FROM analytics.v_hospital_year) f
                JOIN last l USING (ccn)
                WHERE f.loss
                GROUP BY f.ccn, f.grp
                HAVING max(f.fiscal_year) = max(l.last_year)
            )
            SELECT h.ccn, h.hospital_name, h.city, h.state_abbrev, s.state_name,
                   CASE h.hospital_type WHEN 'Critical access' THEN 'Critical access hospital (small rural)'
                                        ELSE 'General hospital' END AS hospital_type,
                   h.ownership, h.rural_urban, l.beds, l.last_year, round(l.total_margin, 4) AS last_margin,
                   round(l.net_income) AS last_net_income, round(l.total_revenue) AS last_revenue,
                   coalesce(k.loss_years, 0) AS loss_years
            FROM core.dim_hospital h
            JOIN last l USING (ccn)
            JOIN core.dim_state s USING (state_abbrev)
            LEFT JOIN streak k USING (ccn)
            WHERE h.in_study AND l.last_year >= 2021""")
        bands = [(0, "Made money", 0), (1, "1 year", 1), (2, "2 years", 2), (4, "3-4 years", 3), (99, "5+ years", 4)]
        prof["loss_band"] = prof.loss_years.map(lambda n: next(b for top, b, _ in bands if n <= top))
        prof["loss_band_order"] = prof.loss_years.map(lambda n: next(o for top, _, o in bands if n <= top))
        prof["hospital_label"] = prof.hospital_name + " (" + prof.city.fillna("") + ", " + prof.state_abbrev + ")"
        dup = prof.hospital_label.duplicated(keep=False)
        prof.loc[dup, "hospital_label"] += " #" + prof.loc[dup, "ccn"]
        # rank on profit margin among hospitals in the same state with the same latest year (1 = highest margin)
        grp = prof.groupby(["state_abbrev", "last_year"]).last_margin
        prof["state_rank"] = grp.rank(ascending=False, method="min").astype(int)
        prof["state_peers"] = grp.transform("size")
        us = hy[hy.fiscal_year == hy.fiscal_year.max()].total_margin.median()
        state_med = hy[hy.fiscal_year == hy.fiscal_year.max()].groupby("state_abbrev").total_margin.median()
        prof["state_median_margin"] = prof.state_abbrev.map(state_med).round(4)
        prof["us_median_margin"] = round(us, 4)
        save(prof, "hospital_profile")

        save(q(conn, """SELECT fiscal_year, medicaid_status, round(uncompensated_care_pct, 4) AS unpaid_care_share
                        FROM analytics.v_medicaid_uncompensated ORDER BY 1, 2"""), "unpaid_care")
        cost = q(conn, """SELECT fiscal_year, round(cost_per_discharge_median) AS cost_per_stay,
                                 round(contract_labor_pct, 4) AS agency_staff_share, round(charge_to_cost, 2) AS charge_to_cost
                          FROM analytics.v_cost_trend ORDER BY 1""")
        bs = q(conn, """SELECT c.state_abbrev, s.state_name, c.hospitals, round(c.billed_per_dollar_paid, 2) AS billed_per_dollar
                        FROM analytics.v_charges_vs_payments c JOIN core.dim_state s USING (state_abbrev)
                        WHERE c.data_year = 2023""")
        us_bill = q(conn, """SELECT data_year, round(sum(submitted_charges) / sum(total_payment), 2) AS us_billed_per_dollar
                             FROM core.fact_inpatient_year GROUP BY 1 ORDER BY 1""")
        bs["us_billed_per_dollar"] = us_bill.us_billed_per_dollar.iloc[-1]
        save(bs, "billed_state")
        # national billed-per-dollar by year (Medicare inpatient file starts in 2013)
        save(cost.merge(us_bill.rename(columns={"data_year": "fiscal_year"}), on="fiscal_year", how="left"), "cost_trend")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
