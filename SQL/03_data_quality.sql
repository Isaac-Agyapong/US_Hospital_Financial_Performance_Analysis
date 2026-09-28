-- =====================================================================
-- Data quality checks. Run by Python/03_run_sql_queries.py; results in SQL/query_results.md.
-- =====================================================================

-- Q1: Row counts by layer
SELECT 'raw.cost_report' AS layer, count(*) AS rows FROM raw.cost_report
UNION ALL SELECT 'core.dim_hospital (all types)', count(*) FROM core.dim_hospital
UNION ALL SELECT 'core.dim_hospital (study: general acute + critical access)', count(*) FROM core.dim_hospital WHERE in_study
UNION ALL SELECT 'core.fact_hospital_year', count(*) FROM core.fact_hospital_year
UNION ALL SELECT 'analytics.v_hospital_year (study, no outliers)', count(*) FROM analytics.v_hospital_year
UNION ALL SELECT 'core.fact_inpatient_year', count(*) FROM core.fact_inpatient_year;

-- Q2: Reports removed and why (territories, duplicate partial-year reports, periods under 300 days)
SELECT 'Territories (PR, GU, VI, AS, MP)' AS reason,
       count(*) FILTER (WHERE state_code NOT IN (SELECT state_abbrev FROM core.dim_state)) AS reports
FROM raw.cost_report
UNION ALL
SELECT 'Second report for the same hospital and year (kept the longest)',
       (SELECT count(*) FROM raw.cost_report r WHERE state_code IN (SELECT state_abbrev FROM core.dim_state))
     - (SELECT count(DISTINCT (provider_ccn, file_year)) FROM raw.cost_report WHERE state_code IN (SELECT state_abbrev FROM core.dim_state))
UNION ALL
SELECT 'Kept report shorter than 300 days (partial year)',
       (SELECT count(DISTINCT (provider_ccn, file_year)) FROM raw.cost_report WHERE state_code IN (SELECT state_abbrev FROM core.dim_state))
     - (SELECT count(*) FROM core.fact_hospital_year);

-- Q3: Duplicate keys after cleaning. Pass: 0 rows
SELECT ccn, fiscal_year, count(*) FROM core.fact_hospital_year GROUP BY 1, 2 HAVING count(*) > 1;

-- Q4: Missing values in key financial fields (study hospitals). Share of hospital-years with the field missing
SELECT round(avg((net_patient_revenue IS NULL)::int), 4)      AS missing_net_patient_revenue,
       round(avg((net_income IS NULL)::int), 4)               AS missing_net_income,
       round(avg((total_costs IS NULL)::int), 4)              AS missing_total_costs,
       round(avg((cash_and_investments IS NULL)::int), 4)     AS missing_cash,
       round(avg((current_liabilities IS NULL)::int), 4)      AS missing_current_liabilities,
       round(avg((uncompensated_care_cost IS NULL)::int), 4)  AS missing_uncompensated_care,
       round(avg((discharges IS NULL OR discharges = 0)::int), 4) AS missing_discharges
FROM core.fact_hospital_year f JOIN core.dim_hospital h USING (ccn) WHERE h.in_study;

-- Q5: Impossible margins (|total margin| > 100% or revenue <= 0): kept in core, left out of analysis
SELECT h.hospital_type, count(*) AS hospital_years, count(*) FILTER (WHERE f.margin_outlier) AS outliers,
       round(100.0 * count(*) FILTER (WHERE f.margin_outlier) / count(*), 2) AS outlier_pct
FROM core.fact_hospital_year f JOIN core.dim_hospital h USING (ccn) WHERE h.in_study
GROUP BY 1;

-- Q6: Accounting identity: total income - total other expenses = net income (within $1,000). Pass: ~all rows
SELECT count(*) AS reports_checked,
       count(*) FILTER (WHERE abs(nullif(total_income, '')::numeric - coalesce(nullif(total_other_expenses, '')::numeric, 0)
                                  - nullif(net_income, '')::numeric) <= 1000) AS identity_holds
FROM raw.cost_report
WHERE total_income <> '' AND net_income <> '';

-- Q7: Reconciliation with MedPAC's published all-payer total margin for IPPS hospitals
--     (July 2025 Data Book, Chart 6-5: 7.8% 2019, 6.6% 2020, 10.6% 2021, 2.3% 2022, 6.4% 2023).
--     MedPAC dates a report by the federal fiscal year containing its midpoint and excludes Maryland
--     (not paid under IPPS) and statistical outliers.
SELECT midpoint_ffy AS fiscal_year, count(*) AS hospitals,
       round(100 * sum(f.net_income) / sum(f.total_revenue), 1) AS total_margin_pct_this_project,
       CASE midpoint_ffy WHEN 2019 THEN 7.8 WHEN 2020 THEN 6.6 WHEN 2021 THEN 10.6 WHEN 2022 THEN 2.3 WHEN 2023 THEN 6.4 END
                                                              AS total_margin_pct_medpac
FROM core.fact_hospital_year f JOIN core.dim_hospital h USING (ccn)
WHERE h.hospital_type = 'General acute care' AND h.state_abbrev <> 'MD' AND NOT f.margin_outlier
  AND midpoint_ffy BETWEEN 2019 AND 2023
GROUP BY midpoint_ffy ORDER BY midpoint_ffy;

-- Q8: Hospitals per year by type (coverage check: roughly 3,100 general acute + 1,300 critical access)
SELECT fiscal_year,
       count(*) FILTER (WHERE hospital_type = 'General acute care') AS general_acute_care,
       count(*) FILTER (WHERE hospital_type = 'Critical access')    AS critical_access
FROM analytics.v_hospital_year GROUP BY fiscal_year ORDER BY fiscal_year;

-- Q9: Days of cash is not reliable for hospitals in a health system: the parent company holds the cash,
--     so many report almost none. This is why financial stress is defined by losses, not cash. (2023)
SELECT ownership, count(*) AS hospitals,
       round(100 * avg((days_cash_on_hand < 5)::int), 1)                              AS pct_under_5_days_cash,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY days_cash_on_hand)::numeric, 0) AS median_days_cash
FROM analytics.v_hospital_year WHERE fiscal_year = 2023 AND days_cash_on_hand IS NOT NULL
GROUP BY ownership ORDER BY median_days_cash;

-- Q10: Medicare inpatient file matches cost-report hospitals (CCN join coverage)
SELECT i.data_year, count(*) AS inpatient_hospitals,
       count(*) FILTER (WHERE h.ccn IS NOT NULL) AS matched_to_cost_reports,
       round(100.0 * count(*) FILTER (WHERE h.ccn IS NOT NULL) / count(*), 1) AS match_pct
FROM core.fact_inpatient_year i LEFT JOIN core.dim_hospital h USING (ccn)
GROUP BY i.data_year ORDER BY i.data_year;
