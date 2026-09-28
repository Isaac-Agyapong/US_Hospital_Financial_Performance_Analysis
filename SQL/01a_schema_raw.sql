-- =====================================================================
-- Raw layer: source columns loaded as text with COPY. Typing and cleaning
-- happen in 02_transform.sql. UNLOGGED: reloadable from Data/raw, and it keeps
-- write-ahead log (which lives on the small C: drive) small.
-- Street addresses are not loaded: the project analyses hospitals as
-- organisations, and city, county and state are enough.
-- =====================================================================

DROP SCHEMA IF EXISTS raw CASCADE;
CREATE SCHEMA raw;

-- CMS Hospital Provider Cost Report (one row per cost report; file_year = fiscal year it begins in)
CREATE UNLOGGED TABLE raw.cost_report (
    file_year text, rpt_rec_num text, provider_ccn text, hospital_name text, city text, state_code text,
    zip_code text, county text, cbsa text, rural_urban text, facility_type text, provider_type text,
    type_of_control text, fy_begin text, fy_end text, fte_employees text, fte_residents text,
    days_medicare text, days_medicaid text, days_total text, beds text, bed_days_available text,
    discharges_medicare text, discharges_medicaid text, discharges_total text,
    cost_charity_care text, bad_debt_expense text, cost_uncompensated_care text, total_salaries text,
    total_costs text, inpatient_charges text, outpatient_charges text, total_charges text,
    contract_labor text, cash text, temporary_investments text, accounts_receivable text,
    total_current_assets text, total_fixed_assets text, total_assets text, total_current_liabilities text,
    total_long_term_liabilities text, total_liabilities text, total_fund_balances text,
    dsh_adjustment text, ime_payment text, total_patient_revenue text, contractual_allowances text,
    net_patient_revenue text, total_operating_expense text, net_income_patients text,
    total_other_income text, total_income text, total_other_expenses text, net_income text,
    cost_to_charge_ratio text, net_revenue_medicaid text, medicaid_charges text
);

-- CMS Medicare Inpatient Hospitals - by Provider (one row per hospital per year)
CREATE UNLOGGED TABLE raw.inpatient (
    data_year text, ccn text, org_name text, state text, ruca text, total_benes text,
    submitted_charges text, total_payment text, medicare_payment text, discharges text, covered_days text,
    bene_avg_age text, bene_dual_cnt text, bene_avg_risk_score text
);

-- KFF Medicaid expansion status by state
CREATE UNLOGGED TABLE raw.kff_expansion (
    state_name text, state_abbrev text, expansion_status text, implementation_note text
);
