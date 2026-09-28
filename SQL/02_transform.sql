-- =====================================================================
-- raw -> core. Every rule fixes a problem found while profiling the raw data
-- (see README "Data problems found and fixed").
-- =====================================================================

-- ---------------------------------------------------------------------
-- States: 50 + DC (US territories are excluded), with Medicaid expansion year.
-- Effective year = first calendar year with at least six months of expansion.
-- ---------------------------------------------------------------------
INSERT INTO core.dim_state
SELECT trim(k.state_abbrev), trim(k.state_name), y.expansion_year,
       CASE WHEN y.expansion_year IS NOT NULL THEN 'Expanded' ELSE 'Not expanded' END
FROM raw.kff_expansion k
CROSS JOIN LATERAL (
    SELECT to_date(substring(k.implementation_note FROM '\d{1,2}/\d{1,2}/\d{4}'), 'MM/DD/YYYY') AS d
) dt
CROSS JOIN LATERAL (
    SELECT CASE WHEN dt.d IS NULL THEN NULL
                WHEN extract(month FROM dt.d) <= 6 THEN extract(year FROM dt.d)
                ELSE extract(year FROM dt.d) + 1 END::smallint AS expansion_year
) y;

-- ---------------------------------------------------------------------
-- Typed cost reports. Blank fields arrive as '' and become NULL.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE cr AS
SELECT r.file_year::smallint                                        AS fiscal_year,
       r.provider_ccn                                               AS ccn,
       initcap(r.hospital_name)                                     AS hospital_name,
       initcap(r.city)                                              AS city,
       initcap(r.county)                                            AS county,
       r.state_code                                                 AS state_abbrev,
       CASE r.rural_urban WHEN 'R' THEN 'Rural' WHEN 'U' THEN 'Urban' END AS rural_urban,
       r.facility_type,
       nullif(r.type_of_control, '')::int                           AS control_code,
       to_date(r.fy_begin, 'MM/DD/YYYY')                            AS fy_begin,
       to_date(r.fy_end, 'MM/DD/YYYY')                              AS fy_end,
       nullif(r.beds, '')::numeric::int                             AS beds,
       nullif(r.fte_employees, '')::numeric                         AS fte_employees,
       nullif(r.discharges_total, '')::numeric                      AS discharges,
       nullif(r.days_total, '')::numeric                            AS patient_days,
       nullif(r.days_medicare, '')::numeric                         AS medicare_days,
       nullif(r.days_medicaid, '')::numeric                         AS medicaid_days,
       nullif(r.bed_days_available, '')::numeric                    AS bed_days_available,
       nullif(r.net_patient_revenue, '')::numeric                   AS net_patient_revenue,
       nullif(r.total_other_income, '')::numeric                    AS other_income,
       nullif(r.total_operating_expense, '')::numeric               AS operating_expense,
       nullif(r.net_income_patients, '')::numeric                   AS net_income_patients,
       nullif(r.net_income, '')::numeric                            AS net_income,
       nullif(r.total_costs, '')::numeric                           AS total_costs,
       nullif(r.total_charges, '')::numeric                         AS total_charges,
       nullif(r.total_salaries, '')::numeric                        AS salaries,
       nullif(r.contract_labor, '')::numeric                        AS contract_labor,
       nullif(r.cost_charity_care, '')::numeric                     AS charity_care_cost,
       nullif(r.bad_debt_expense, '')::numeric                      AS bad_debt,
       nullif(r.cost_uncompensated_care, '')::numeric               AS uncompensated_care_cost,
       coalesce(nullif(r.cash, '')::numeric, 0) + coalesce(nullif(r.temporary_investments, '')::numeric, 0)
                                                                    AS cash_and_investments,
       nullif(r.cash, '') IS NULL AND nullif(r.temporary_investments, '') IS NULL AS cash_missing,
       nullif(r.total_current_assets, '')::numeric                  AS current_assets,
       nullif(r.total_current_liabilities, '')::numeric             AS current_liabilities,
       nullif(r.total_assets, '')::numeric                          AS total_assets,
       nullif(r.total_liabilities, '')::numeric                     AS total_liabilities,
       nullif(r.net_revenue_medicaid, '')::numeric                  AS medicaid_net_revenue
FROM raw.cost_report r
WHERE r.state_code IN (SELECT state_abbrev FROM core.dim_state);      -- drop PR, GU, VI, AS, MP

-- ---------------------------------------------------------------------
-- One report per hospital per fiscal year: hospitals that change owner or fiscal
-- calendar file two reports (e.g. a 2-month stub then a full year). Keep the longest.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE cr1 AS
SELECT * FROM (
    SELECT cr.*, (fy_end - fy_begin + 1)                             AS period_days,
           count(*) OVER (PARTITION BY ccn, fiscal_year)             AS reports_in_year,
           row_number() OVER (PARTITION BY ccn, fiscal_year
                              ORDER BY (fy_end - fy_begin) DESC, fy_end DESC) AS rn
    FROM cr
) x WHERE rn = 1;

-- Ownership groups (CMS worksheet S-2 "type of control" codes)
CREATE TEMP VIEW own AS
SELECT *, CASE WHEN control_code IN (1, 2)          THEN 'Nonprofit'
               WHEN control_code BETWEEN 3 AND 6    THEN 'For-profit'
               WHEN control_code BETWEEN 7 AND 13   THEN 'Government'
               ELSE 'Unknown' END AS ownership
FROM cr1;

-- ---------------------------------------------------------------------
-- Hospitals: attributes from the latest report
-- ---------------------------------------------------------------------
INSERT INTO core.dim_hospital
SELECT DISTINCT ON (ccn)
       ccn, hospital_name, city, county, state_abbrev, rural_urban,
       CASE facility_type WHEN 'STH' THEN 'General acute care' WHEN 'CAH' THEN 'Critical access'
                          WHEN 'PH' THEN 'Psychiatric' WHEN 'LTCH' THEN 'Long-term care'
                          WHEN 'RH' THEN 'Rehabilitation' WHEN 'CH' THEN 'Children''s' ELSE 'Other' END,
       ownership, beds,
       min(fiscal_year) OVER (PARTITION BY ccn), max(fiscal_year) OVER (PARTITION BY ccn),
       facility_type IN ('STH', 'CAH')
FROM own
ORDER BY ccn, fiscal_year DESC;

-- ---------------------------------------------------------------------
-- Hospital-years with KPIs. Reports shorter than 300 days are partial years
-- (openings, closures, fiscal calendar changes) and are left out.
-- ---------------------------------------------------------------------
INSERT INTO core.fact_hospital_year
SELECT o.ccn, o.fiscal_year,
       o.fy_begin, o.fy_end,
       extract(year FROM o.fy_begin + (o.fy_end - o.fy_begin) / 2 + interval '3 months')::smallint,
       o.period_days, o.reports_in_year, o.ownership, o.rural_urban, o.beds, o.fte_employees, o.discharges,
       o.patient_days, o.medicare_days, o.medicaid_days, o.bed_days_available,
       o.net_patient_revenue, o.other_income,
       o.net_patient_revenue + coalesce(o.other_income, 0),
       o.operating_expense, o.net_income_patients, o.net_income, o.total_costs, o.total_charges,
       o.salaries, o.contract_labor, o.charity_care_cost, o.bad_debt, o.uncompensated_care_cost,
       CASE WHEN o.cash_missing THEN NULL ELSE o.cash_and_investments END,
       o.current_assets, o.current_liabilities, o.total_assets, o.total_liabilities, o.medicaid_net_revenue,
       -- KPIs
       o.net_income_patients / nullif(o.net_patient_revenue, 0),
       o.net_income / nullif(o.net_patient_revenue + coalesce(o.other_income, 0), 0),
       CASE WHEN NOT o.cash_missing AND o.operating_expense > 0
            THEN o.cash_and_investments / (o.operating_expense / o.period_days) END,
       o.current_assets / nullif(o.current_liabilities, 0),
       o.total_liabilities / nullif(o.total_assets, 0),
       o.uncompensated_care_cost / nullif(o.total_costs, 0),
       o.medicaid_days / nullif(o.patient_days, 0),
       o.medicare_days / nullif(o.patient_days, 0),
       o.patient_days / nullif(o.bed_days_available, 0),
       o.total_costs / nullif(o.discharges, 0),
       o.total_charges / nullif(o.total_costs, 0),
       o.contract_labor / nullif(o.salaries, 0),
       coalesce(o.net_patient_revenue <= 0
                OR abs(o.net_income / nullif(o.net_patient_revenue + coalesce(o.other_income, 0), 0)) > 1, true)
FROM own o
WHERE o.period_days >= 300;

-- ---------------------------------------------------------------------
-- Medicare inpatient charges vs payments
-- ---------------------------------------------------------------------
INSERT INTO core.fact_inpatient_year
SELECT ccn, data_year::smallint, nullif(total_benes, '')::int, nullif(discharges, '')::int,
       nullif(covered_days, '')::int, nullif(submitted_charges, '')::numeric, nullif(total_payment, '')::numeric,
       nullif(medicare_payment, '')::numeric, nullif(bene_avg_age, '')::numeric,
       nullif(bene_dual_cnt, '')::numeric / nullif(nullif(total_benes, '')::numeric, 0),
       nullif(bene_avg_risk_score, '')::numeric
FROM raw.inpatient
WHERE ccn ~ '^[0-9A-Z]{6}$';

CREATE INDEX ON core.fact_hospital_year (fiscal_year);
CREATE INDEX ON core.dim_hospital (state_abbrev);
ANALYZE core.fact_hospital_year;
ANALYZE core.dim_hospital;
