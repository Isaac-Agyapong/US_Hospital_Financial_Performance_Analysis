-- =====================================================================
-- Core layer: typed star schema.
--   dim_state            50 states + DC, Medicaid expansion year (KFF)
--   dim_hospital         one row per hospital (CCN), latest name, location, type and ownership
--   fact_hospital_year   one cost report per hospital per fiscal year, with financial KPIs
--   fact_inpatient_year  Medicare inpatient charges and payments per hospital per year
-- =====================================================================

DROP SCHEMA IF EXISTS analytics CASCADE;
DROP SCHEMA IF EXISTS core CASCADE;
CREATE SCHEMA core;
CREATE SCHEMA analytics;

CREATE TABLE core.dim_state (
    state_abbrev     char(2) PRIMARY KEY,
    state_name       text NOT NULL,
    expansion_year   smallint,           -- first calendar year with >= 6 months of Medicaid expansion
    medicaid_status  text NOT NULL       -- 'Expanded' / 'Not expanded'
);

CREATE TABLE core.dim_hospital (
    ccn              char(6) PRIMARY KEY,
    hospital_name    text NOT NULL,
    city             text,
    county           text,
    state_abbrev     char(2) NOT NULL REFERENCES core.dim_state,
    rural_urban      text,               -- 'Rural' / 'Urban' by location (CBSA), latest report
    hospital_type    text NOT NULL,      -- 'General acute care' / 'Critical access' / other
    ownership        text NOT NULL,      -- 'Nonprofit' / 'For-profit' / 'Government'
    beds_latest      integer,
    first_year       smallint,
    last_year        smallint,
    in_study         boolean NOT NULL    -- general acute care or critical access hospital
);

CREATE TABLE core.fact_hospital_year (
    ccn                       char(6)  NOT NULL REFERENCES core.dim_hospital,
    fiscal_year               smallint NOT NULL,   -- CMS year: federal fiscal year the report begins in
    fy_begin                  date     NOT NULL,
    fy_end                    date     NOT NULL,
    midpoint_ffy              smallint NOT NULL,   -- federal fiscal year containing the report's midpoint (MedPAC convention)
    period_days               smallint NOT NULL,
    reports_in_year           smallint NOT NULL,   -- >1 when a hospital filed more than one report that year
    ownership                 text NOT NULL,       -- ownership at the time of this report
    rural_urban               text,      -- by location: CBSA 999xx = rural
    paid_as_rural_in_city     boolean NOT NULL,  -- city hospital reclassified as rural for Medicare payment
    beds                      integer,
    fte_employees             numeric,
    discharges                numeric,
    patient_days              numeric,
    medicare_days             numeric,
    medicaid_days             numeric,
    bed_days_available        numeric,
    net_patient_revenue       numeric,
    other_income              numeric,
    total_revenue             numeric,
    operating_expense         numeric,
    net_income_patients       numeric,
    net_income                numeric,
    total_costs               numeric,
    total_charges             numeric,
    salaries                  numeric,
    contract_labor            numeric,
    charity_care_cost         numeric,
    bad_debt                  numeric,
    uncompensated_care_cost   numeric,
    cash_and_investments      numeric,
    current_assets            numeric,
    current_liabilities       numeric,
    total_assets              numeric,
    total_liabilities         numeric,
    medicaid_net_revenue      numeric,
    -- KPIs (NULL when the inputs are missing or not meaningful)
    operating_margin          numeric,   -- patient-care profit / net patient revenue
    total_margin              numeric,   -- net income / (net patient revenue + other income)
    days_cash_on_hand         numeric,   -- cash and short-term investments / daily operating expense
    current_ratio             numeric,   -- current assets / current liabilities
    debt_ratio                numeric,   -- total liabilities / total assets
    uncompensated_care_pct    numeric,   -- uncompensated care cost / total costs
    medicaid_day_share        numeric,
    medicare_day_share        numeric,
    occupancy_rate            numeric,
    cost_per_discharge        numeric,
    charge_to_cost            numeric,   -- total charges / total costs (price markup)
    contract_labor_pct        numeric,   -- contract labor / salaries
    margin_outlier            boolean NOT NULL,  -- |total margin| > 100% or revenue <= 0: left out of margin averages
    PRIMARY KEY (ccn, fiscal_year)
);

CREATE TABLE core.fact_inpatient_year (
    ccn                  char(6)  NOT NULL,
    data_year            smallint NOT NULL,
    medicare_benes       integer,
    discharges           integer,
    covered_days         integer,
    submitted_charges    numeric,   -- what the hospital billed Medicare
    total_payment        numeric,   -- what it was actually paid (Medicare + patient + other)
    medicare_payment     numeric,
    bene_avg_age         numeric,
    dual_eligible_share  numeric,
    bene_avg_risk_score  numeric,
    PRIMARY KEY (ccn, data_year)
);
