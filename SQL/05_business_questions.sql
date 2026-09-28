-- =====================================================================
-- Business questions. Run by Python/03_run_sql_queries.py.
-- Study hospitals: general acute care and critical access, 50 states + DC.
-- 2023 is the latest year (4,330 hospitals, close to 2022's 4,386).
-- =====================================================================

-- Q1: How profitable are US hospitals, and how has that changed? (aggregate and median total margin)
SELECT fiscal_year, hospitals,
       round(100 * total_margin_aggregate, 1)     AS total_margin_pct,
       round(100 * total_margin_median, 1)        AS median_margin_pct,
       round(100 * share_losing_money, 1)         AS pct_losing_money,
       round(total_revenue / 1e9, 0)              AS revenue_billions
FROM analytics.v_national_trend ORDER BY fiscal_year;

-- Q2: Which year was worst, and how fast did hospitals recover? (LAG over the median margin)
SELECT fiscal_year, round(100 * total_margin_median, 1) AS median_margin_pct,
       round(100 * (total_margin_median - lag(total_margin_median) OVER (ORDER BY fiscal_year)), 1) AS change_pts
FROM analytics.v_national_trend ORDER BY fiscal_year;

-- Q3: For-profit vs nonprofit vs government, general acute care hospitals, 2019 and 2023
SELECT ownership, fiscal_year, hospitals,
       round(100 * total_margin_median, 1) AS median_margin_pct,
       round(100 * share_losing_money, 1)  AS pct_losing_money
FROM analytics.v_type_ownership
WHERE hospital_type = 'General acute care' AND fiscal_year IN (2019, 2023)
ORDER BY ownership, fiscal_year;

-- Q4: Rural vs urban, 2023
SELECT rural_urban, hospitals, round(100 * total_margin_median, 1) AS median_margin_pct,
       round(100 * share_losing_money, 1) AS pct_losing_money
FROM analytics.v_rural_urban WHERE fiscal_year = 2023 ORDER BY rural_urban;

-- Q5: Did Medicaid expansion reduce hospitals' unpaid care? Uncompensated care as % of costs
SELECT fiscal_year,
       round(100 * max(uncompensated_care_pct) FILTER (WHERE medicaid_status = 'Expanded'), 2)     AS expanded_pct,
       round(100 * max(uncompensated_care_pct) FILTER (WHERE medicaid_status = 'Not expanded'), 2) AS not_expanded_pct
FROM analytics.v_medicaid_uncompensated GROUP BY fiscal_year ORDER BY fiscal_year;

-- Q6: How many hospitals are under financial stress (lost money two years in a row)?
SELECT fiscal_year, sum(hospitals)::int AS hospitals, sum(stressed)::int AS stressed,
       round(100.0 * sum(stressed) / sum(hospitals), 1) AS stressed_pct,
       rank() OVER (ORDER BY sum(stressed)::numeric / sum(hospitals) DESC) AS rank_worst
FROM analytics.v_financial_stress GROUP BY fiscal_year ORDER BY fiscal_year;

-- Q7: Which hospitals are most stressed? By type, area and ownership, 2023 (groups of 30+ hospitals)
SELECT hospital_type, rural_urban, ownership, hospitals, stressed, round(100 * stressed_share, 1) AS stressed_pct
FROM analytics.v_financial_stress WHERE fiscal_year = 2023 AND hospitals >= 30 ORDER BY stressed_share DESC;

-- Q8: Cost pressure: cost per discharge and contract (agency) labor, general acute care
SELECT fiscal_year, round(cost_per_discharge_median, 0) AS median_cost_per_discharge,
       round(100 * contract_labor_pct, 1) AS contract_labor_pct_of_salaries,
       round(charge_to_cost, 2) AS charges_per_dollar_of_cost
FROM analytics.v_cost_trend ORDER BY fiscal_year;

-- Q9: Prices: how much do hospitals bill Medicare for each dollar they are paid? Top 10 states, 2023
SELECT state_abbrev, hospitals, round(billed_per_dollar_paid, 2) AS billed_per_dollar_paid,
       round(payment_per_discharge, 0) AS paid_per_stay, round(charge_per_discharge, 0) AS billed_per_stay
FROM analytics.v_charges_vs_payments WHERE data_year = 2023
ORDER BY billed_per_dollar_paid DESC LIMIT 10;

-- Q10: States with the highest share of hospitals losing money, 2023
SELECT rank_most_losing AS rank, state_abbrev, medicaid_status, hospitals,
       round(100 * share_losing_money, 1) AS pct_losing_money, round(100 * total_margin_median, 1) AS median_margin_pct
FROM analytics.v_state_scorecard ORDER BY rank_most_losing LIMIT 10;

-- Q11: Hospitals losing money several years in a row (count by streak length, streaks still running in 2023)
SELECT years_in_a_row, count(*) AS hospitals,
       count(*) FILTER (WHERE rural_urban = 'Rural') AS rural
FROM analytics.v_loss_streaks GROUP BY years_in_a_row ORDER BY years_in_a_row;

-- Q12: Do hospitals with more Medicaid patients earn less? (correlation and slope, 2023)
SELECT count(*) AS hospitals,
       round(corr(medicaid_day_share, total_margin)::numeric, 3) AS correlation,
       round(regr_slope(total_margin, medicaid_day_share)::numeric, 3) AS margin_change_per_unit_share
FROM analytics.v_hospital_year WHERE fiscal_year = 2023 AND medicaid_day_share BETWEEN 0 AND 1;
