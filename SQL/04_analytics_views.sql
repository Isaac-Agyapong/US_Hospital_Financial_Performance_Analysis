-- =====================================================================
-- Analytics layer: one view per business question. The notebook, Power BI and
-- the companion machine learning project read these views, never core directly.
--
-- Study population: general acute care and critical access hospitals in the 50
-- states + DC. Aggregate margins are totals (sum of profit / sum of revenue),
-- so big hospitals weigh more; medians describe the typical hospital. Reports
-- with impossible margins (|total margin| > 100% or revenue <= 0) are left out.
-- =====================================================================

CREATE VIEW analytics.v_hospital_year AS
SELECT f.*, h.hospital_name, h.city, h.county, h.state_abbrev, h.hospital_type, s.medicaid_status, s.expansion_year,
       (s.expansion_year IS NOT NULL AND f.fiscal_year >= s.expansion_year) AS expanded_now
FROM core.fact_hospital_year f
JOIN core.dim_hospital h USING (ccn)
JOIN core.dim_state s USING (state_abbrev)
WHERE h.in_study AND NOT f.margin_outlier;

-- Q1. National trend: aggregate and median margins, share of hospitals losing money
CREATE VIEW analytics.v_national_trend AS
SELECT fiscal_year,
       count(*)                                                                   AS hospitals,
       sum(net_income) / sum(total_revenue)                                       AS total_margin_aggregate,
       sum(net_income_patients) / sum(net_patient_revenue)                        AS operating_margin_aggregate,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY total_margin)::numeric                  AS total_margin_median,
       avg((net_income < 0)::int)                                                 AS share_losing_money,
       sum(total_revenue)                                                         AS total_revenue,
       sum(net_income)                                                            AS net_income,
       sum(uncompensated_care_cost) / sum(total_costs)                            AS uncompensated_care_pct,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY days_cash_on_hand)::numeric             AS days_cash_median
FROM analytics.v_hospital_year
GROUP BY fiscal_year;

-- Q2. By hospital type and ownership
CREATE VIEW analytics.v_type_ownership AS
SELECT fiscal_year, hospital_type, ownership,
       count(*)                                                                   AS hospitals,
       sum(net_income) / sum(total_revenue)                                       AS total_margin_aggregate,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY total_margin)::numeric                  AS total_margin_median,
       avg((net_income < 0)::int)                                                 AS share_losing_money
FROM analytics.v_hospital_year
WHERE ownership <> 'Unknown'
GROUP BY fiscal_year, hospital_type, ownership;

-- Q3. Rural vs urban
CREATE VIEW analytics.v_rural_urban AS
SELECT fiscal_year, rural_urban,
       count(*)                                                                   AS hospitals,
       sum(net_income) / sum(total_revenue)                                       AS total_margin_aggregate,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY total_margin)::numeric                  AS total_margin_median,
       avg((net_income < 0)::int)                                                 AS share_losing_money,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY days_cash_on_hand)::numeric             AS days_cash_median
FROM analytics.v_hospital_year
WHERE rural_urban IS NOT NULL
GROUP BY fiscal_year, rural_urban;

-- Q4. Medicaid expansion and unpaid care: uncompensated care as a share of costs
CREATE VIEW analytics.v_medicaid_uncompensated AS
SELECT fiscal_year, medicaid_status,
       count(*)                                                                   AS hospitals,
       sum(uncompensated_care_cost) / sum(total_costs)                            AS uncompensated_care_pct,
       sum(charity_care_cost) / sum(total_costs)                                  AS charity_care_pct,
       sum(net_income) / sum(total_revenue)                                       AS total_margin_aggregate
FROM analytics.v_hospital_year
WHERE uncompensated_care_cost IS NOT NULL
GROUP BY fiscal_year, medicaid_status;

-- Q5. Financial stress: hospitals that lost money two years in a row.
--     (Days of cash is not used: hospitals in a health system often hold almost no cash of their
--     own because the parent company keeps it, so a low number does not mean trouble.)
CREATE VIEW analytics.v_financial_stress AS
WITH h AS (
    SELECT fiscal_year, hospital_type, rural_urban, ownership, net_income,
           lag(net_income)  OVER (PARTITION BY ccn ORDER BY fiscal_year)          AS prev_net_income,
           lag(fiscal_year) OVER (PARTITION BY ccn ORDER BY fiscal_year)          AS prev_year
    FROM analytics.v_hospital_year
)
SELECT fiscal_year, hospital_type, rural_urban, ownership,
       count(*)                                                                   AS hospitals,
       count(*) FILTER (WHERE net_income < 0 AND prev_net_income < 0)             AS stressed,
       avg((net_income < 0 AND prev_net_income < 0)::int)                         AS stressed_share
FROM h
WHERE prev_year = fiscal_year - 1 AND rural_urban IS NOT NULL
GROUP BY fiscal_year, hospital_type, rural_urban, ownership;

-- Q6. Cost pressure: cost per discharge, staffing, contract labor
CREATE VIEW analytics.v_cost_trend AS
SELECT fiscal_year,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY cost_per_discharge)::numeric            AS cost_per_discharge_median,
       sum(total_costs) / sum(discharges)                                         AS cost_per_discharge_aggregate,
       sum(contract_labor) / sum(salaries)                                        AS contract_labor_pct,
       sum(salaries) / sum(total_costs)                                           AS salary_share_of_costs,
       sum(total_charges) / sum(total_costs)                                      AS charge_to_cost
FROM analytics.v_hospital_year
WHERE hospital_type = 'General acute care' AND discharges > 0
GROUP BY fiscal_year;

-- Q7. Prices: what hospitals bill Medicare vs what they are paid (inpatient)
CREATE VIEW analytics.v_charges_vs_payments AS
SELECT i.data_year, h.state_abbrev,
       count(*)                                                                   AS hospitals,
       sum(i.submitted_charges) / sum(i.total_payment)                            AS billed_per_dollar_paid,
       sum(i.total_payment) / sum(i.discharges)                                   AS payment_per_discharge,
       sum(i.submitted_charges) / sum(i.discharges)                               AS charge_per_discharge
FROM core.fact_inpatient_year i
JOIN core.dim_hospital h USING (ccn)
GROUP BY i.data_year, h.state_abbrev;

-- Q8. State scorecard, latest year
CREATE VIEW analytics.v_state_scorecard AS
WITH y AS (SELECT max(fiscal_year) AS yr FROM core.fact_hospital_year)
SELECT v.state_abbrev, s.state_name, s.medicaid_status, y.yr AS fiscal_year,
       count(*)                                                                   AS hospitals,
       sum(v.net_income) / sum(v.total_revenue)                                   AS total_margin_aggregate,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY v.total_margin)::numeric                AS total_margin_median,
       avg((v.net_income < 0)::int)                                               AS share_losing_money,
       sum(v.uncompensated_care_cost) / sum(v.total_costs)                        AS uncompensated_care_pct,
       rank() OVER (ORDER BY avg((v.net_income < 0)::int) DESC)                   AS rank_most_losing
FROM analytics.v_hospital_year v
JOIN core.dim_state s USING (state_abbrev)
CROSS JOIN y
WHERE v.fiscal_year = y.yr
GROUP BY v.state_abbrev, s.state_name, s.medicaid_status, y.yr;

-- Q9. Hospitals that lose money year after year (streaks of consecutive losses still running in the latest year)
CREATE VIEW analytics.v_loss_streaks AS
WITH f AS (
    SELECT ccn, fiscal_year, net_income < 0 AS loss,
           fiscal_year - row_number() OVER (PARTITION BY ccn, net_income < 0 ORDER BY fiscal_year) AS grp
    FROM analytics.v_hospital_year
), streaks AS (
    SELECT ccn, max(fiscal_year) AS last_year, count(*) AS years_in_a_row
    FROM f WHERE loss GROUP BY ccn, grp
)
SELECT s.ccn, h.hospital_name, h.state_abbrev, h.hospital_type, h.rural_urban, s.years_in_a_row
FROM streaks s JOIN core.dim_hospital h USING (ccn)
WHERE s.last_year = (SELECT max(fiscal_year) FROM core.fact_hospital_year);

-- Hospital-year panel for the companion ML project and the Power BI hospital explorer
CREATE MATERIALIZED VIEW analytics.mv_hospital_panel AS
SELECT ccn, fiscal_year, hospital_name, city, state_abbrev, hospital_type, ownership, rural_urban, medicaid_status,
       expanded_now, beds, fte_employees, discharges, occupancy_rate, net_patient_revenue, total_revenue,
       net_income, total_margin, operating_margin, days_cash_on_hand, current_ratio, debt_ratio,
       uncompensated_care_pct, medicaid_day_share, medicare_day_share, cost_per_discharge, charge_to_cost,
       contract_labor_pct
FROM analytics.v_hospital_year;
CREATE UNIQUE INDEX ON analytics.mv_hospital_panel (ccn, fiscal_year);
