"""
Generate the Power BI Project (PBIP). The model imports the CSV extracts in dashboard/data/ (written by
05_export_powerbi.py from the PostgreSQL analytics views), so the report opens on any machine without a database.
The folder is a parameter (DataFolder).

    dashboard/Hospital_Finance.pbip                 open this in Power BI Desktop
    dashboard/Hospital_Finance.SemanticModel/       model (TMDL), columns read from the CSV headers
    dashboard/Hospital_Finance.Report/              6 pages + a state tooltip page (PBIR JSON)

Design: a dark executive dashboard. A sidebar holds the logo and title, page links and filters (year, hospital
type, owner, rural/urban, state) that stay in sync across pages. KPI cards show an icon, the number, the change
from the year before (arrow in green or red) and a small trend line. Chart titles are written by DAX measures, so
they state the finding for whatever is selected. The artwork (sidebar, rounded tiles, shadows, icons) is drawn
by make_background.py from the same layout, one image per page, and the visuals sit on top of it.

Colour meanings: mint = making money / good news, coral = losing money / bad news, amber = the highlighted item
(the selected year, rural hospitals, states that did not expand Medicaid), sky = all hospitals / neutral series,
violet and slate = extra categories in the ownership donut.
"""
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
DATA = DASH / "data"
ASSETS = DASH / "assets"
NAME = "Hospital_Finance"
SM = DASH / f"{NAME}.SemanticModel"
RPT = DASH / f"{NAME}.Report"

TABLES = ["hospital_year", "states", "hospital_profile", "unpaid_care", "cost_trend", "billed_state"]
HIDDEN = {"ccn", "loss", "has_prior", "stressed", "tile_x", "tile_y", "medicaid_fifth", "loss_band_order",
          "us_median_margin", "state_median_margin", "state_rank", "state_peers", "loss_streak"}
SORT_BY = {("hospital_year", "medicaid_fifth_label"): "medicaid_fifth",
           ("hospital_profile", "loss_band"): "loss_band_order"}
TEXT_COLS = {"ccn"}                      # keep leading zeros
INT_COLS = {"medicaid_fifth", "beds", "last_year", "fiscal_year", "loss_streak"}

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
S_PBIP = f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json"
S_PBISM = f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json"
S_PBIR = f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json"
S_VERSION = f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json"
S_REPORT = f"{SCHEMA}/item/report/definition/report/1.2.0/schema.json"
S_PAGES = f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json"
S_PAGE = f"{SCHEMA}/item/report/definition/page/1.3.0/schema.json"
S_VISUAL = f"{SCHEMA}/item/report/definition/visualContainer/1.4.0/schema.json"
BASE_THEME = "CY24SU10"
CUSTOM_THEME = "HospitalDarkTheme.json"

# palette
BG_TOP, BG_BOTTOM, SIDEBAR = "#0F1C1D", "#0A1314", "#08100F"
TILE, TILE_BORDER, GRID = "#132426", "#20373A", "#22383A"
TEXT, MUTED, INK_DARK = "#EEF4F3", "#8FA5A3", "#0B1718"
MINT, CORAL, CORAL_L, CORAL_D = "#34D399", "#FF6B6B", "#FFA8A8", "#C2413F"
AMBER, AMBER_L, SKY, SKY_L, SKY_D = "#FBBF24", "#FDE08A", "#60A5FA", "#A5CBFB", "#35608F"
VIOLET, SLATE = "#A78BFA", "#94A3B8"
FONT, FONT_BOLD = "Segoe UI", "Segoe UI Semibold"
PCT, PCT1, INT = "0%", "0.0%", "#,0"
Y = "hospital_year[fiscal_year]"
SEL = "VAR _y = [Selected Year]\n"


def tag(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hospital-finance-dark/" + "/".join(parts)))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path, obj):
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def q(name):
    return name if name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


def indent(text, tabs):
    return "\n".join("\t" * tabs + line if line else "" for line in text.splitlines())


# =====================================================================
# Measures: (home table, name, DAX, format, folder)
# =====================================================================
def at_y(measure, *filters):
    return f"CALCULATE ( [{measure}], {Y} = _y{''.join(', ' + f for f in filters)} )"


def delta(name, measure, kind, good_when_down, first_year=2011):
    """Text 'change vs last year' with an arrow, and its colour (mint when it moved the good way)."""
    body = (SEL + f"VAR _c = {at_y(measure)}\n"
            f"VAR _p = CALCULATE ( [{measure}], {Y} = _y - 1 )\n")
    if kind == "pts":
        body += 'VAR _d = ( _c - _p ) * 100\nVAR _t = FORMAT ( ABS ( _d ), "0.0" ) & " pts"\n'
    elif kind == "pct":
        body += 'VAR _d = DIVIDE ( _c - _p, _p ) * 100\nVAR _t = FORMAT ( ABS ( _d ), "0.0" ) & "%"\n'
    else:
        body += 'VAR _d = _c - _p\nVAR _t = FORMAT ( ABS ( _d ), "#,0" )\n'
    text = body + (f'RETURN IF ( _y <= {first_year}, "first year in the data",\n'
                   '    IF ( _d < 0, "▼ ", IF ( _d > 0, "▲ ", "● " ) ) & _t & " vs " & ( _y - 1 ) )')
    good = "_d <= 0" if good_when_down else "_d >= 0"
    colour = body + f'RETURN IF ( _y <= {first_year}, "{MUTED}", IF ( {good}, "{MINT}", "{CORAL}" ) )'
    return [("hospital_year", f"{name} Delta", text, None, "KPI"),
            ("hospital_year", f"{name} Delta Colour", colour, None, "KPI")]


def cost_delta(name, col, kind, good_when_down, first_year=2011):
    body = (SEL + f"VAR _c = CALCULATE ( SUM ( cost_trend[{col}] ), cost_trend[fiscal_year] = _y )\n"
            f"VAR _p = CALCULATE ( SUM ( cost_trend[{col}] ), cost_trend[fiscal_year] = _y - 1 )\n")
    if kind == "pts":
        body += 'VAR _d = ( _c - _p ) * 100\nVAR _t = FORMAT ( ABS ( _d ), "0.0" ) & " pts"\n'
    elif kind == "x":
        body += 'VAR _d = _c - _p\nVAR _t = FORMAT ( ABS ( _d ), "0.00" ) & "x"\n'
    else:
        body += 'VAR _d = DIVIDE ( _c - _p, _p ) * 100\nVAR _t = FORMAT ( ABS ( _d ), "0.0" ) & "%"\n'
    text = body + (f'RETURN IF ( _y <= {first_year} || ISBLANK ( _p ), "no earlier year",\n'
                   '    IF ( _d < 0, "▼ ", IF ( _d > 0, "▲ ", "● " ) ) & _t & " vs " & ( _y - 1 ) )')
    good = "_d <= 0" if good_when_down else "_d >= 0"
    colour = body + f'RETURN IF ( _y <= {first_year} || ISBLANK ( _p ), "{MUTED}", IF ( {good}, "{MINT}", "{CORAL}" ) )'
    return [("cost_trend", f"{name} Delta", text, None, "KPI"),
            ("cost_trend", f"{name} Delta Colour", colour, None, "KPI")]


REV_TEXT = ('IF ( _v >= 1e12, FORMAT ( _v / 1e12, "$0.00" ) & "T", IF ( _v >= 1e9, FORMAT ( _v / 1e9, "$0.0" ) & "B",'
            ' FORMAT ( _v / 1e6, "$0" ) & "M" ) )')

MEASURES = [
    ("hospital_year", "Selected Year", "SELECTEDVALUE ( Years[year], 2023 )", "0", "Core"),
    ("hospital_year", "Hospital Count", "COUNTROWS ( hospital_year )", INT, "Core"),
    ("hospital_year", "Losing Share", "DIVIDE ( SUM ( hospital_year[loss] ), COUNTROWS ( hospital_year ) )", PCT, "Core"),
    ("hospital_year", "Losing Count", "SUM ( hospital_year[loss] )", INT, "Core"),
    ("hospital_year", "Stressed Share", "DIVIDE ( SUM ( hospital_year[stressed] ), SUM ( hospital_year[has_prior] ) )", PCT, "Core"),
    ("hospital_year", "Stressed Count", "IF ( SUM ( hospital_year[has_prior] ) > 0, SUM ( hospital_year[stressed] ) )", INT, "Core"),
    ("hospital_year", "Typical Margin", "MEDIAN ( hospital_year[total_margin] )", PCT1, "Core"),
    ("hospital_year", "Combined Margin", "DIVIDE ( SUM ( hospital_year[net_income] ), SUM ( hospital_year[total_revenue] ) )", PCT1, "Core"),
    ("hospital_year", "Total Revenue", "SUM ( hospital_year[total_revenue] )", "#,0", "Core"),

    # ---- overview KPIs (selected year)
    ("hospital_year", "Losing Sel", SEL + f"RETURN {at_y('Losing Share')}", PCT, "KPI"),
    ("hospital_year", "Stressed Sel", SEL + f"RETURN {at_y('Stressed Count')}", INT, "KPI"),
    ("hospital_year", "Margin Sel", SEL + f"RETURN {at_y('Typical Margin')}", PCT1, "KPI"),
    ("hospital_year", "Revenue Sel", SEL + f"VAR _v = {at_y('Total Revenue')}\nRETURN {REV_TEXT}", None, "KPI"),
    *delta("Losing", "Losing Share", "pts", True),
    *delta("Stressed", "Stressed Count", "count", True, first_year=2012),
    *delta("Margin", "Typical Margin", "pts", False),
    *delta("Revenue", "Total Revenue", "pct", False),

    # ---- overview charts
    ("hospital_year", "Trend Title",
     SEL + f"VAR _c = {at_y('Losing Share')}\n"
     'VAR _t = TOPN ( 1, ADDCOLUMNS ( VALUES ( hospital_year[fiscal_year] ), "@v", [Losing Share] ), [@v], DESC )\n'
     'RETURN FORMAT ( _c, "0%" ) & " of hospitals lost money in " & _y & ". The worst year was "\n'
     '    & MAXX ( _t, hospital_year[fiscal_year] ) & " (" & FORMAT ( MAXX ( _t, [@v] ), "0%" ) & ")"', None, "Titles"),
    ("hospital_year", "Funnel Count",
     SEL + "RETURN SWITCH ( SELECTEDVALUE ( Stage[order] ),\n"
     f"    1, CALCULATE ( COUNTROWS ( hospital_year ), {Y} = _y ),\n"
     f"    2, CALCULATE ( SUM ( hospital_year[loss] ), {Y} = _y ),\n"
     f"    3, CALCULATE ( COUNTROWS ( hospital_year ), {Y} = _y, hospital_year[loss_streak] >= 2 ),\n"
     f"    4, CALCULATE ( COUNTROWS ( hospital_year ), {Y} = _y, hospital_year[loss_streak] >= 3 ) )", INT, "Charts"),
    ("hospital_year", "Funnel Title",
     SEL + f"VAR _n = CALCULATE ( COUNTROWS ( hospital_year ), {Y} = _y, hospital_year[loss_streak] >= 3 )\n"
     'RETURN FORMAT ( _n, "#,0" ) & " hospitals had lost money 3+ years in a row by " & _y', None, "Titles"),
    ("hospital_year", "Losing Count Sel", SEL + f"RETURN {at_y('Losing Count')}", INT, "Charts"),
    ("hospital_year", "Donut Title",
     SEL + f'RETURN "Who owns the " & FORMAT ( {at_y("Losing Count")}, "#,0" ) & " hospitals that lost money in " & _y',
     None, "Titles"),
    ("hospital_year", "Area Colour",
     f'IF ( LEFT ( SELECTEDVALUE ( hospital_year[area_type] ), 5 ) = "Rural", "{AMBER}", "{SKY}" )', None, "Colours"),
    ("hospital_year", "Area Title",
     SEL + f'VAR _r = {at_y("Losing Share", chr(34).join(["hospital_year[rural_urban] = ", "Rural", ""]))}\n'
     f'VAR _u = {at_y("Losing Share", chr(34).join(["hospital_year[rural_urban] = ", "Urban", ""]))}\n'
     'RETURN "Rural: " & FORMAT ( _r, "0%" ) & " lost money vs " & FORMAT ( _u, "0%" ) & " of urban hospitals (" & _y & ")"',
     None, "Titles"),
    ("hospital_year", "Top 8 Losing",
     "// ranks every state, whatever else is clicked (REMOVEFILTERS, not ALLSELECTED); states with fewer than\n"
     "// 5 hospitals in the current selection are not ranked (one or two hospitals would show as 100%)\n"
     "VAR _cur = [Losing Sel]\nVAR _n = [State Hospitals Sel]\n"
     'VAR _all = CALCULATETABLE ( FILTER ( ADDCOLUMNS ( VALUES ( states[state_name] ), "@r", [Losing Sel], "@n", [State Hospitals Sel] ), [@n] >= 5 ), REMOVEFILTERS ( states ) )\n'
     "RETURN IF ( HASONEVALUE ( states[state_name] ) && _n >= 5 && COUNTROWS ( FILTER ( _all, [@r] > _cur ) ) < 8, _cur )", PCT, "Charts"),
    ("hospital_year", "States Title", SEL + 'RETURN "States with the most hospitals losing money, " & _y', None, "Titles"),
    ("hospital_year", "Year Colour", f'IF ( SELECTEDVALUE ( {Y} ) = [Selected Year], "{AMBER}", "{CORAL}" )', None, "Colours"),

    # ---- who is struggling
    ("hospital_year", "Group Stressed Sel",
     SEL + f"VAR _n = CALCULATE ( SUM ( hospital_year[has_prior] ), {Y} = _y )\n"
     f'RETURN IF ( _n >= 30 && SELECTEDVALUE ( hospital_year[group_label] ) <> "Unknown", {at_y("Stressed Share")} )',
     PCT, "Charts"),
    ("hospital_year", "Group Colour",
     f'IF ( LEFT ( SELECTEDVALUE ( hospital_year[group_label] ), 5 ) = "Rural", "{AMBER}", "{SKY}" )', None, "Colours"),
    ("hospital_year", "Group Title",
     SEL + 'VAR _t = TOPN ( 1, FILTER ( ADDCOLUMNS ( VALUES ( hospital_year[group_label] ), "@s", [Group Stressed Sel] ),\n'
     '    NOT ISBLANK ( [@s] ) ), [@s], DESC )\n'
     'RETURN MAXX ( _t, hospital_year[group_label] ) & " are the most likely to be stuck in losses: "\n'
     '    & FORMAT ( MAXX ( _t, [@s] ), "0%" ) & " (" & _y & ")"', None, "Titles"),
    ("hospital_year", "Stressed Count Sel", SEL + f"RETURN {at_y('Stressed Count')}", INT, "Charts"),
    ("hospital_year", "Treemap Title",
     SEL + f'RETURN "Where the " & FORMAT ( {at_y("Stressed Count")}, "#,0" ) & " hospitals that lost money 2 years running are (" & _y & ")"',
     None, "Titles"),
    ("hospital_year", "Streak Count Sel",
     SEL + f"RETURN IF ( SELECTEDVALUE ( hospital_year[streak_band_order] ) > 0, CALCULATE ( COUNTROWS ( hospital_year ), {Y} = _y ) )",
     INT, "Charts"),
    ("hospital_year", "Streak Colour",
     f'SWITCH ( SELECTEDVALUE ( hospital_year[streak_band_order] ), 1, "{SLATE}", 2, "{CORAL_L}", 3, "{CORAL}", "{CORAL_D}" )',
     None, "Colours"),
    ("hospital_year", "Streak Title",
     SEL + f'RETURN FORMAT ( CALCULATE ( COUNTROWS ( hospital_year ), {Y} = _y, hospital_year[loss_streak] >= 3 ), "#,0" )\n'
     '    & " hospitals had lost money 3 or more years in a row by " & _y', None, "Titles"),
    ("hospital_year", "Fifth Losing Sel",
     SEL + f"RETURN IF ( NOT ISBLANK ( SELECTEDVALUE ( hospital_year[medicaid_fifth] ) ), {at_y('Losing Share')} )", PCT, "Charts"),
    ("hospital_year", "Fifth Colour", f'IF ( SELECTEDVALUE ( hospital_year[medicaid_fifth] ) = 5, "{CORAL}", "{SKY_D}" )', None, "Colours"),
    ("hospital_year", "Fifth Title",
     SEL + f"VAR _hi = {at_y('Losing Share', 'hospital_year[medicaid_fifth] = 5')}\n"
     f"VAR _lo = {at_y('Losing Share', 'hospital_year[medicaid_fifth] = 1')}\n"
     'RETURN "Most Medicaid patients: " & FORMAT ( _hi, "0%" ) & " lost money vs " & FORMAT ( _lo, "0%" ) & " with the fewest (" & _y & ")"',
     None, "Titles"),

    # ---- states
    ("hospital_year", "State Hospitals Sel", SEL + f"RETURN {at_y('Hospital Count')}", INT, "States"),
    ("hospital_year", "State Margin Sel", SEL + f"RETURN {at_y('Typical Margin')}", PCT1, "States"),
    ("hospital_year", "Top 10 Losing",
     "VAR _cur = [Losing Sel]\nVAR _n = [State Hospitals Sel]\n"
     'VAR _all = CALCULATETABLE ( FILTER ( ADDCOLUMNS ( VALUES ( states[state_name] ), "@r", [Losing Sel], "@n", [State Hospitals Sel] ), [@n] >= 5 ), REMOVEFILTERS ( states ) )\n'
     "RETURN IF ( HASONEVALUE ( states[state_name] ) && _n >= 5 && COUNTROWS ( FILTER ( _all, [@r] > _cur ) ) < 10, _cur )", PCT, "States"),
    ("hospital_year", "Top 10 Margin", "IF ( NOT ISBLANK ( [Top 10 Losing] ), [State Margin Sel] )", PCT1, "States"),
    ("hospital_year", "Top 10 Hospitals", "IF ( NOT ISBLANK ( [Top 10 Losing] ), [State Hospitals Sel] )", INT, "States"),
    ("hospital_year", "Worst State",
     'VAR _t = TOPN ( 1, CALCULATETABLE ( ADDCOLUMNS ( VALUES ( states[state_name] ), "@r", [Losing Sel] ), REMOVEFILTERS ( states ) ), [@r], DESC )\n'
     "RETURN MAXX ( _t, states[state_name] )", None, "States"),
    ("hospital_year", "Worst State Context",
     SEL + 'VAR _t = TOPN ( 1, CALCULATETABLE ( ADDCOLUMNS ( VALUES ( states[state_name] ), "@r", [Losing Sel] ), REMOVEFILTERS ( states ) ), [@r], DESC )\n'
     'RETURN FORMAT ( MAXX ( _t, [@r] ), "0%" ) & " of its hospitals lost money in " & _y', None, "States"),
    ("hospital_year", "Half Losing States",
     'COUNTROWS ( FILTER ( CALCULATETABLE ( ADDCOLUMNS ( VALUES ( states[state_name] ), "@r", [Losing Sel] ), REMOVEFILTERS ( states ) ), [@r] >= 0.5 ) )',
     INT, "States"),
    ("hospital_year", "Half Losing Context", SEL + 'RETURN "of 51 states (with DC) in " & _y', None, "States"),
    ("hospital_year", "Expanded Losing",
     SEL + f'RETURN {at_y("Losing Share", chr(34).join(["hospital_year[medicaid_status] = ", "Expanded", ""]))}', PCT, "States"),
    ("hospital_year", "Expanded Losing Context",
     SEL + f'RETURN "vs " & FORMAT ( {at_y("Losing Share", chr(34).join(["hospital_year[medicaid_status] = ", "Not expanded", ""]))}, "0%" ) & " in states that did not expand"',
     None, "States"),
    ("unpaid_care", "Unpaid Care Share", "AVERAGE ( unpaid_care[unpaid_care_share] )", PCT1, "States"),
    ("unpaid_care", "Unpaid Not Expanded",
     SEL + 'RETURN CALCULATE ( [Unpaid Care Share], unpaid_care[fiscal_year] = _y, unpaid_care[medicaid_status] = "Not expanded" )',
     PCT1, "States"),
    ("unpaid_care", "Unpaid Context",
     SEL + 'RETURN "vs " & FORMAT ( CALCULATE ( [Unpaid Care Share], unpaid_care[fiscal_year] = _y, unpaid_care[medicaid_status] = "Expanded" ), "0.0%" ) & " where Medicaid expanded"',
     None, "States"),
    ("unpaid_care", "Unpaid Title",
     SEL + 'VAR _e = CALCULATE ( [Unpaid Care Share], unpaid_care[fiscal_year] = _y, unpaid_care[medicaid_status] = "Expanded" )\n'
     'VAR _n = CALCULATE ( [Unpaid Care Share], unpaid_care[fiscal_year] = _y, unpaid_care[medicaid_status] = "Not expanded" )\n'
     'RETURN "Unpaid care: " & FORMAT ( _e, "0.0%" ) & " of costs where Medicaid expanded vs " & FORMAT ( _n, "0.0%" ) & " elsewhere (" & _y & ")"',
     None, "Titles"),
    ("hospital_year", "Map Title", SEL + 'RETURN "Share of hospitals losing money by state, " & _y', None, "Titles"),
    ("hospital_year", "Tile Label", "SELECTEDVALUE ( states[state_abbrev] )", None, "Map"),
    ("hospital_year", "Tile Colour",
     "VAR _r = [Losing Sel]\n"
     "RETURN SWITCH ( TRUE (),\n"
     f'    ISBLANK ( SELECTEDVALUE ( states[state_abbrev] ) ), "{TILE}",\n'
     f'    ISBLANK ( _r ), "#24393B",\n'
     '    _r < 0.20, "#2B4A4C",\n    _r < 0.30, "#6B4A4C",\n    _r < 0.40, "#A14F4F",\n'
     f'    _r < 0.50, "{CORAL_D}",\n    "{CORAL}" )', None, "Map"),
    ("hospital_year", "Selected State", 'SELECTEDVALUE ( states[state_name], "All states" )', None, "Tooltip"),
    ("hospital_year", "State Status", 'SELECTEDVALUE ( states[medicaid_status] ) & " Medicaid"', None, "Tooltip"),
    ("hospital_year", "State Rank Text",
     SEL + "VAR _cur = [Losing Sel]\n"
     'VAR _all = CALCULATETABLE ( ADDCOLUMNS ( VALUES ( states[state_name] ), "@r", [Losing Sel] ), REMOVEFILTERS ( states ) )\n'
     'RETURN IF ( HASONEVALUE ( states[state_name] ), "#" & COUNTROWS ( FILTER ( _all, [@r] > _cur ) ) + 1 & " of 51 for share losing money in " & _y )',
     None, "Tooltip"),

    # ---- costs (national, general hospitals)
    ("cost_trend", "Cost per Stay", "SUM ( cost_trend[cost_per_stay] )", '"$"#,0', "Costs"),
    ("cost_trend", "Agency Staff Share", "SUM ( cost_trend[agency_staff_share] )", PCT1, "Costs"),
    ("cost_trend", "Markup", "SUM ( cost_trend[charge_to_cost] )", '0.0"x"', "Costs"),
    ("cost_trend", "Billed per Dollar", "SUM ( cost_trend[us_billed_per_dollar] )", '"$"0.00', "Costs"),
    ("cost_trend", "Cost Sel", SEL + "RETURN CALCULATE ( [Cost per Stay], cost_trend[fiscal_year] = _y )", '"$"#,0', "KPI"),
    ("cost_trend", "Agency Sel", SEL + "RETURN CALCULATE ( [Agency Staff Share], cost_trend[fiscal_year] = _y )", PCT1, "KPI"),
    ("cost_trend", "Markup Sel", SEL + "RETURN CALCULATE ( [Markup], cost_trend[fiscal_year] = _y )", '0.0"x"', "KPI"),
    ("cost_trend", "Billed Sel", SEL + "RETURN CALCULATE ( [Billed per Dollar], cost_trend[fiscal_year] = _y )", '"$"0.00', "KPI"),
    *cost_delta("Cost", "cost_per_stay", "pct", True),
    *cost_delta("Agency", "agency_staff_share", "pts", True),
    *cost_delta("Markup", "charge_to_cost", "x", True),
    *cost_delta("Billed", "us_billed_per_dollar", "x", True, first_year=2013),
    ("cost_trend", "Cost Year Colour", f'IF ( SELECTEDVALUE ( cost_trend[fiscal_year] ) = [Selected Year], "{AMBER}", "{SKY_D}" )', None, "Colours"),
    ("cost_trend", "Cost Title",
     'VAR _a = CALCULATE ( [Cost per Stay], cost_trend[fiscal_year] = 2011 )\nVAR _b = CALCULATE ( [Cost per Stay], cost_trend[fiscal_year] = 2023 )\n'
     'RETURN "A hospital stay now costs " & FORMAT ( _b, "$#,0" ) & ", up " & FORMAT ( _b / _a - 1, "0%" ) & " since 2011"', None, "Titles"),
    ("cost_trend", "Agency Title",
     'RETURN_PLACEHOLDER', None, "Titles"),
    ("billed_state", "Top Billed",
     "VAR _cur = SUM ( billed_state[billed_per_dollar] )\n"
     'VAR _all = CALCULATETABLE ( ADDCOLUMNS ( VALUES ( billed_state[state_name] ), "@b", CALCULATE ( SUM ( billed_state[billed_per_dollar] ) ) ), REMOVEFILTERS ( billed_state ) )\n'
     "RETURN IF ( HASONEVALUE ( billed_state[state_name] ) && COUNTROWS ( FILTER ( _all, [@b] > _cur ) ) < 10, _cur )",
     '"$"0.00', "Costs"),
]
AGENCY_TITLE = ('VAR _a = CALCULATE ( [Agency Staff Share], cost_trend[fiscal_year] = 2019 )\n'
                'VAR _b = CALCULATE ( [Agency Staff Share], cost_trend[fiscal_year] = 2022 )\n'
                'RETURN "Agency staff went from " & FORMAT ( _a, "0.0%" ) & " to " & FORMAT ( _b, "0.0%" ) & " of salaries in 3 years"')
MEASURES = [(h, n, AGENCY_TITLE if d == "RETURN_PLACEHOLDER" else d, f, fo) for h, n, d, f, fo in MEASURES]

ONE = "HASONEVALUE ( hospital_profile[hospital_label] )"
MEASURES += [
    ("hospital_profile", "Profile Name", f'IF ( {ONE}, SELECTEDVALUE ( hospital_profile[hospital_name] ), "Pick a hospital" )', None, "Hospital"),
    ("hospital_profile", "Profile Facts",
     f'IF ( {ONE}, SELECTEDVALUE ( hospital_profile[hospital_type] ) & "   ·   " & SELECTEDVALUE ( hospital_profile[ownership] )'
     ' & "   ·   " & SELECTEDVALUE ( hospital_profile[rural_urban] ) & "   ·   " & SELECTEDVALUE ( hospital_profile[city] ) & ", "'
     ' & SELECTEDVALUE ( hospital_profile[state_name] ) & "   ·   " & FORMAT ( SUM ( hospital_profile[beds] ), "#,0" ) & " beds",'
     ' "Type a hospital name in the search box on the left" )', None, "Hospital"),
    ("hospital_profile", "Profile Margin", f"IF ( {ONE}, SUM ( hospital_profile[last_margin] ) )", PCT1, "Hospital"),
    ("hospital_profile", "Profile Margin Context",
     f'IF ( {ONE}, "in " & MAX ( hospital_profile[last_year] ) & "  ·  US typical " & FORMAT ( MAX ( hospital_profile[us_median_margin] ), "0.0%" ) )',
     None, "Hospital"),
    ("hospital_profile", "Profile Margin Colour", f'IF ( [Profile Margin] < 0, "{CORAL}", "{MINT}" )', None, "Hospital"),
    ("hospital_profile", "Profile Result",
     "VAR _n = SUM ( hospital_profile[last_net_income] )\nVAR _m = ABS ( _n )\n"
     'VAR _t = "$" & IF ( _m >= 1e9, FORMAT ( _m / 1e9, "0.0" ) & "B", IF ( _m >= 1e6, FORMAT ( _m / 1e6, "0.0" ) & "M", FORMAT ( _m / 1e3, "0" ) & "K" ) )\n'
     f'RETURN IF ( {ONE}, IF ( _n < 0, "Lost ", "Made " ) & _t )', None, "Hospital"),
    ("hospital_profile", "Profile Result Context",
     'VAR _r = SUM ( hospital_profile[last_revenue] )\n'
     f'RETURN IF ( {ONE}, "on $" & IF ( _r >= 1e9, FORMAT ( _r / 1e9, "0.0" ) & "B", FORMAT ( _r / 1e6, "0" ) & "M" ) & " of revenue in " & MAX ( hospital_profile[last_year] ) )',
     None, "Hospital"),
    ("hospital_profile", "Profile Streak", f"IF ( {ONE}, SUM ( hospital_profile[loss_years] ) )", "0", "Hospital"),
    ("hospital_profile", "Profile Streak Context",
     f'IF ( {ONE}, IF ( SUM ( hospital_profile[loss_years] ) = 0, "made money in its latest year", "losing money up to " & MAX ( hospital_profile[last_year] ) ) )',
     None, "Hospital"),
    ("hospital_profile", "Profile Streak Colour", f'IF ( SUM ( hospital_profile[loss_years] ) >= 2, "{CORAL}", "{MINT}" )', None, "Hospital"),
    ("hospital_profile", "Profile Rank",
     f'IF ( {ONE}, "#" & SUM ( hospital_profile[state_rank] ) & " of " & SUM ( hospital_profile[state_peers] ) )', None, "Hospital"),
    ("hospital_profile", "Profile Rank Context",
     f'IF ( {ONE}, "in " & SELECTEDVALUE ( hospital_profile[state_name] ) & " by profit margin" )', None, "Hospital"),
    ("hospital_profile", "Hospital Margin", f"IF ( {ONE}, AVERAGE ( hospital_year[total_margin] ) )", PCT1, "Hospital"),
    ("hospital_profile", "Hospital Margin Colour", f'IF ( [Hospital Margin] < 0, "{CORAL}", "{MINT}" )', None, "Colours"),
    ("hospital_profile", "State Typical Margin", f"IF ( {ONE}, SUM ( hospital_profile[state_median_margin] ) )", PCT1, "Hospital"),
    ("hospital_profile", "Gauge Min", "-0.2", PCT, "Hospital"),
    ("hospital_profile", "Gauge Max", "0.3", PCT, "Hospital"),
    ("hospital_profile", "Profile Sentence",
     "VAR _m = SUM ( hospital_profile[last_margin] )\n"
     "VAR _s = SUM ( hospital_profile[state_median_margin] )\n"
     "VAR _k = SUM ( hospital_profile[loss_years] )\n"
     f"RETURN IF ( {ONE},\n"
     '    "In " & MAX ( hospital_profile[last_year] ) & ", this hospital " & IF ( _m < 0, "lost ", "kept " ) & FORMAT ( ABS ( _m ) * 100, "0.0" )\n'
     '        & " cents on every dollar it took in" & IF ( _m < 0, "", " as profit" ) & ". That is "\n'
     '        & IF ( _m > _s + 0.005, "better than", IF ( _m < _s - 0.005, "worse than", "about the same as" ) )\n'
     '        & " the typical hospital in " & SELECTEDVALUE ( hospital_profile[state_name] ) & " (" & FORMAT ( _s * 100, "0.0" ) & " cents). "\n'
     '        & SWITCH ( TRUE (), _k >= 3, "It has lost money " & _k & " years in a row: a sign of lasting financial stress.",\n'
     '                            _k = 2, "It has lost money two years in a row, which this report counts as financial stress.",\n'
     '                            _k = 1, "It lost money in its latest year only.",\n'
     '                            "It made money in its latest year." ),\n'
     '    "Pick a hospital in the search box to see what its numbers mean." )', None, "Hospital"),
    ("hospital_year", "Selection Text",
     'VAR _f =\n'
     '    IF ( ISFILTERED ( hospital_year[hospital_type] ), "  ·  " & CONCATENATEX ( VALUES ( hospital_year[hospital_type] ), hospital_year[hospital_type], ", " ) )\n'
     '    & IF ( ISFILTERED ( hospital_year[ownership] ), "  ·  " & CONCATENATEX ( VALUES ( hospital_year[ownership] ), hospital_year[ownership], ", " ) )\n'
     '    & IF ( ISFILTERED ( hospital_year[rural_urban] ), "  ·  " & CONCATENATEX ( VALUES ( hospital_year[rural_urban] ), hospital_year[rural_urban], ", " ) )\n'
     '    & IF ( ISFILTERED ( states[state_name] ), "  ·  " & CONCATENATEX ( VALUES ( states[state_name] ), states[state_name], ", " ) )\n'
     'VAR _y = [Selected Year]\n'
     f'VAR _n = CALCULATE ( COUNTROWS ( hospital_year ), {Y} = _y )\n'
     'RETURN _y & "  ·  " & FORMAT ( _n, "#,0" ) & " hospitals" & IF ( _f = "", "  ·  all of the US", _f )', None, "Core"),
]

CALC_COLUMNS = [
    ("hospital_year", "area_type", "string",
     'hospital_year[rural_urban] & IF ( hospital_year[hospital_type] = "General hospital", " general", " critical access" )'),
    ("hospital_year", "streak_band_order", "int64",
     "SWITCH ( TRUE (), hospital_year[loss_streak] = 0, 0, hospital_year[loss_streak] = 1, 1, hospital_year[loss_streak] = 2, 2,\n"
     "    hospital_year[loss_streak] <= 4, 3, 4 )"),
    ("hospital_year", "streak_band", "string",
     'SWITCH ( hospital_year[streak_band_order], 0, "No loss", 1, "1 year", 2, "2 years", 3, "3-4 years", "5+ years" )'),
]
SORT_BY[("hospital_year", "streak_band")] = "streak_band_order"
HIDDEN.add("streak_band_order")


def csv_columns(table):
    """Column names and types from the CSV extract (pandas infers the type)."""
    df = pd.read_csv(DATA / f"{table}.csv", dtype={c: str for c in TEXT_COLS})
    out = []
    for col, dt in df.dtypes.items():
        if col in TEXT_COLS:
            out.append((col, "string", "type text"))
        elif pd.api.types.is_bool_dtype(dt):
            out.append((col, "boolean", "type logical"))
        elif pd.api.types.is_integer_dtype(dt) or col in INT_COLS:
            out.append((col, "int64", "Int64.Type"))
        elif pd.api.types.is_float_dtype(dt):
            out.append((col, "double", "type number"))
        else:
            out.append((col, "string", "type text"))
    return out


def table_tmdl(table, columns):
    out = [f"table {table}", f"\tlineageTag: {tag(table)}", ""]
    for home, name, dax, fmt, folder in MEASURES:
        if home != table:
            continue
        out += [f"\tmeasure {q(name)} =", indent(dax, 3)] if "\n" in dax else [f"\tmeasure {q(name)} = {dax}"]
        if fmt:   # TMDL: a format containing quotes must itself be quoted, with inner quotes doubled
            out.append("\t\tformatString: " + ('"' + fmt.replace('"', '""') + '"' if '"' in fmt else fmt))
        out += [f"\t\tdisplayFolder: {folder}", f"\t\tlineageTag: {tag(table, 'm', name)}", ""]
    for home, name, dtype, dax in CALC_COLUMNS:
        if home == table:
            out += [f"\tcolumn {q(name)} =", indent(dax, 3), f"\t\tdataType: {dtype}",
                    *([f"\t\tisHidden"] if name in HIDDEN else []),
                    *([f"\t\tsortByColumn: {SORT_BY[(table, name)]}"] if (table, name) in SORT_BY else []),
                    f"\t\tlineageTag: {tag(table, 'calc', name)}", "\t\tsummarizeBy: none", "",
                    "\t\tannotation SummarizationSetBy = Automatic", ""]
    for col, dtype, _ in columns:
        out += [f"\tcolumn {col}", f"\t\tdataType: {dtype}"]
        if dtype == "double":
            out.append("\t\tformatString: #,0.00")
        elif dtype == "int64":
            out.append("\t\tformatString: 0")
        if col in HIDDEN:
            out.append("\t\tisHidden")
        if (table, col) in SORT_BY:
            out.append(f"\t\tsortByColumn: {SORT_BY[(table, col)]}")
        key = col in INT_COLS | {"loss_years", "state_rank", "loss_band_order", "tile_x", "tile_y"}
        out += [f"\t\tlineageTag: {tag(table, col)}",
                f"\t\tsummarizeBy: {'none' if dtype in ('string', 'boolean') or key else 'sum'}",
                f"\t\tsourceColumn: {col}", "", "\t\tannotation SummarizationSetBy = Automatic", ""]
    types = ", ".join(f'{{"{c}", {m}}}' for c, _, m in columns)
    m = (f'let\n    Source = Csv.Document(File.Contents(DataFolder & "{table}.csv"), '
         '[Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),\n'
         '    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),\n'
         f'    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")\nin\n    Typed')
    out += [f"\tpartition {table} = m", "\t\tmode: import", "\t\tsource =", indent(m, 4), "",
            "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


def calc_table(name, cols, dax):
    """Small DAX table. cols: (column, dataType, hidden, sortBy)."""
    out = [f"table {name}", f"\tlineageTag: {tag(name)}", ""]
    for col, dtype, hidden, sort_by in cols:
        out += [f"\tcolumn {col}", f"\t\tdataType: {dtype}", *(["\t\tisHidden"] if hidden else []),
                *([f"\t\tsortByColumn: {sort_by}"] if sort_by else []),
                f"\t\tlineageTag: {tag(name, col)}", "\t\tsummarizeBy: none", "\t\tisNameInferred",
                f"\t\tsourceColumn: [{col}]", "", "\t\tannotation SummarizationSetBy = Automatic", ""]
    out += [f"\tpartition {name} = calculated", "\t\tmode: import", "\t\tsource =", indent(dax, 4), ""]
    return "\n".join(out)


def check_names():
    """Power BI rejects a measure whose name matches any column (case-insensitive) or another measure."""
    cols = ({c.lower() for t in TABLES for c, *_ in csv_columns(t)} | {n.lower() for _, n, *_ in CALC_COLUMNS}
            | {"order", "place", "stage", "year"})
    names = [n.lower() for _, n, *_ in MEASURES]
    clash = sorted({n for n in names if n in cols} | {n for n in names if names.count(n) > 1})
    if clash:
        raise ValueError(f"measure names clash with columns or each other: {clash}")


def build_model():
    check_names()
    shutil.rmtree(SM, ignore_errors=True)
    d = SM / "definition"
    write_json(SM / "definition.pbism", {"$schema": S_PBISM, "version": "4.0", "settings": {}})
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    write(d / "model.tmdl", "\n".join([
        "model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull",
        "", "annotation __PBI_TimeIntelligenceEnabled = 0", "",
        *[f"ref table {t}" for t in TABLES + ["Years", "Stage"]], ""]))
    folder = str(DATA) + "\\"
    write(d / "expressions.tmdl", "\n".join([
        f'expression DataFolder = "{folder}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]',
        f"\tlineageTag: {tag('DataFolder')}", "", "\tannotation PBI_ResultType = Text", ""]))
    for t in TABLES:
        write(d / "tables" / f"{t}.tmdl", table_tmdl(t, csv_columns(t)))
    # Years: not related to anything, so picking a year sets the KPIs without cutting the trend charts
    write(d / "tables" / "Years.tmdl", calc_table("Years", [("year", "int64", False, None)],
                                                   'SELECTCOLUMNS ( GENERATESERIES ( 2011, 2023, 1 ), "year", [Value] )'))
    write(d / "tables" / "Stage.tmdl", calc_table(
        "Stage", [("order", "int64", True, None), ("stage", "string", False, "order")],
        'DATATABLE ( "order", INTEGER, "stage", STRING, {\n'
        '    { 1, "All hospitals" }, { 2, "Lost money" },\n'
        '    { 3, "2 years in a row" }, { 4, "3+ years in a row" } } )'))
    write(d / "relationships.tmdl", "\n".join([
        f"relationship {tag('rel', 'states')}", "\tfromColumn: hospital_year.state_abbrev", "\ttoColumn: states.state_abbrev", "",
        f"relationship {tag('rel', 'profile')}", "\tfromColumn: hospital_year.ccn", "\ttoColumn: hospital_profile.ccn", ""]))


# =====================================================================
# Report helpers (PBIR JSON)
# =====================================================================
def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def s(text):
    return lit("'" + text.replace("'", "''") + "'")


def solid(hex_):
    return {"solid": {"color": s(hex_)}}


def field(entity, prop, measure=False):
    return {("Measure" if measure else "Column"): {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def home(measure):
    return next(h for h, n, *_ in MEASURES if n == measure)


def mexpr(measure):
    return {"expr": field(home(measure), measure, True)}


def C(entity, prop, name=None):
    return (entity, prop, False, name) if name else (entity, prop, False)


def M(prop, name=None):
    return (home(prop), prop, True, name) if name else (home(prop), prop, True)


def projections(fields):
    out = []
    for e, p, m, *name in fields:
        pr = {"field": field(e, p, m), "queryRef": f"{e}.{p}", "nativeQueryRef": p}
        if name and name[0]:
            pr["displayName"] = name[0]
        out.append(pr)
    return {"projections": out}


def by_measure(measure):
    return {"solid": {"color": mexpr(measure)}}


def container(title=None, subtitle=None, tooltip_page=None, pad=(10, 6, 12, 12), background=None, radius=0):
    """Visuals are transparent: the tile, its shadow and border are drawn in the page artwork."""
    objs = {
        "background": [{"properties": {"show": lit("true" if background else "false"),
                                       **({"color": solid(background), "transparency": lit("0D")} if background else {})}}],
        "border": [{"properties": {"show": lit("true" if background else "false"),
                                   **({"color": solid(background), "radius": lit(f"{radius}D")} if background else {})}}],
        "dropShadow": [{"properties": {"show": lit("false")}}],
        "visualHeader": [{"properties": {"background": solid(TILE), "border": solid(TILE), "foreground": solid(MUTED)}}],
        "padding": [{"properties": {"top": lit(f"{pad[0]}D"), "bottom": lit(f"{pad[1]}D"),
                                    "left": lit(f"{pad[2]}D"), "right": lit(f"{pad[3]}D")}}],
        "title": [{"properties": {"show": lit("false")}}],
    }
    if title:
        text = mexpr(title[1:]) if title.startswith("=") else s(title)
        objs["title"] = [{"properties": {"show": lit("true"), "text": text, "fontColor": solid(TEXT), "fontSize": lit("13D"),
                                         "fontFamily": s(FONT_BOLD), "titleWrap": lit("true")}}]
    if subtitle:
        objs["subTitle"] = [{"properties": {"show": lit("true"), "text": s(subtitle), "fontColor": solid(MUTED),
                                            "fontSize": lit("10D"), "titleWrap": lit("true")}}]
    if tooltip_page:
        objs["visualTooltip"] = [{"properties": {"type": s("ReportPage"), "section": s(tooltip_page)}}]
    return objs


def axes(show_value=False, cat_size=10, inner_padding=None, label_area=None, categorical=False, show_cat=True, value_start=None):
    cat = {"show": lit("true" if show_cat else "false"), "showAxisTitle": lit("false"), "labelColor": solid(MUTED),
           "fontSize": lit(f"{cat_size}D")}
    if inner_padding is not None:
        cat["innerPadding"] = lit(f"{inner_padding}L")
    if label_area is not None:
        cat["maxMarginFactor"] = lit(f"{label_area}L")
    if categorical:
        cat["axisType"] = s("Categorical")
    val = {"show": lit("true" if show_value else "false"), "showAxisTitle": lit("false"), "labelColor": solid(MUTED),
           "fontSize": lit("10D"), "gridlineShow": lit("true" if show_value else "false"), "gridlineColor": solid(GRID)}
    if value_start is not None:
        val["start"] = lit(f"{value_start}D")
    return {"categoryAxis": [{"properties": cat}], "valueAxis": [{"properties": val}]}


def labels(size=10, colour=TEXT, show=True):
    return {"labels": [{"properties": {"show": lit("true" if show else "false"), "color": solid(colour),
                                       "fontSize": lit(f"{size}D"), "bold": lit("true")}}]}


def fill_by(measure):
    return [{"properties": {"fill": by_measure(measure)}, "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}]


def fill_by_value(entity, prop, colours):
    col = field(entity, prop)
    return [{"properties": {"fill": solid(c)}, "selector": {"data": [{"scopeId": {"Comparison": {
        "ComparisonKind": 0, "Left": col, "Right": {"Literal": {"Value": f"'{v}'"}}}}}]}} for v, c in colours.items()]


def chart(vtype, roles, title=None, subtitle=None, sort=None, objects=None, tooltip_page=None, pad=(10, 6, 12, 12)):
    v = {"visualType": vtype, "query": {"queryState": {r: projections(f) for r, f in roles.items()}},
         "visualContainerObjects": container(title, subtitle, tooltip_page, pad=pad), "drillFilterOtherVisuals": True}
    if sort:
        (e, p, m, *_), direction = sort
        v["query"]["sortDefinition"] = {"sort": [{"field": field(e, p, m), "direction": direction}], "isDefaultSort": False}
    if objects:
        v["objects"] = dict(objects)
    return v


def textbox(paragraphs, align=None, pad=(0, 0, 4, 4)):
    """paragraphs: list of (text, size, bold, colour[, font]) or lists of such runs for one line."""
    def run(t, size, bold, col, font=None):
        return {"value": t, "textStyle": {"fontSize": f"{size}pt", "color": col,
                                          **({"fontWeight": "bold"} if bold else {}),
                                          **({"fontFamily": font} if font else {})}}
    paras = [{"textRuns": [run(*r) for r in (p if isinstance(p, list) else [p])],
              **({"horizontalTextAlignment": align} if align else {})} for p in paragraphs if p]
    return {"visualType": "textbox", "drillFilterOtherVisuals": True,
            "objects": {"general": [{"properties": {"paragraphs": paras}}]},
            "visualContainerObjects": container(pad=pad)}


def card(measure, colour=TEXT, size=22, font=FONT_BOLD, colour_measure=None, pad=(0, 0, 2, 2), background=None, radius=0):
    col = by_measure(colour_measure) if colour_measure else solid(colour)
    v = {"visualType": "card", "query": {"queryState": {"Values": projections([M(measure, " ")])}},
         "objects": {"labels": [{"properties": {"color": col, "fontSize": lit(f"{size}D"), "fontFamily": s(font),
                                                "labelDisplayUnits": lit("1D")}}],
                     "categoryLabels": [{"properties": {"show": lit("false")}}]},
         "visualContainerObjects": container(pad=pad, background=background, radius=radius), "drillFilterOtherVisuals": True}
    return v


def sparkline(entity, axis_col, measure, colour):
    return chart("areaChart", {"Category": [C(entity, axis_col)], "Y": [M(measure)]}, pad=(0, 0, 0, 0),
                 objects={**axes(show_cat=False), **labels(show=False),
                          "legend": [{"properties": {"show": lit("false")}}],
                          "dataPoint": [{"properties": {"fill": solid(colour)}}],
                          "lineStyles": [{"properties": {"strokeWidth": lit("2D"), "lineChartType": s("smooth")}}]})


def slicer(entity, prop, header, group, default=None, search=False):
    v = {"visualType": "slicer", "query": {"queryState": {"Values": projections([C(entity, prop)])}},
         "objects": {"data": [{"properties": {"mode": s("Dropdown")}}],
                     # the label is drawn in the sidebar artwork above the box, so the visual is only the box
                     "header": [{"properties": {"show": lit("false")}}],
                     "items": [{"properties": {"fontColor": solid(TEXT), "background": solid(TILE), "fontSize": lit("10D")}}]},
         "visualContainerObjects": {**container(pad=(0, 0, 0, 0)),
                                    # no hover toolbar: it would sit on top of the filter above and block clicks
                                    "visualHeader": [{"properties": {"show": lit("false")}}]},
         "drillFilterOtherVisuals": True,
         "syncGroup": {"groupName": group, "fieldChanges": True, "filterChanges": True}}
    general = {}
    if default is not None:
        v["objects"]["selection"] = [{"properties": {"singleSelect": lit("true")}}]
        value = f"{default}L" if isinstance(default, int) else "'" + default.replace("'", "''") + "'"
        general["filter"] = {"filter": {"Version": 2, "From": [{"Name": "t", "Entity": entity, "Type": 0}],
                                        "Where": [{"Condition": {"In": {
                                            "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": prop}}],
                                            "Values": [[{"Literal": {"Value": value}}]]}}}]}}
    if search:
        general["selfFilterEnabled"] = lit("true")
    if general:
        v["objects"]["general"] = [{"properties": general}]
    return v


def navigator():
    state = lambda sid, props: {"properties": props, "selector": {"id": sid}}
    return {"visualType": "pageNavigator", "drillFilterOtherVisuals": True,
            "objects": {
                "layout": [{"properties": {"orientation": lit("1D"), "cellPadding": lit("4L")}}],
                "pages": [{"properties": {"showHiddenPages": lit("false"), "showTooltipPages": lit("false")}}],
                "shape": [{"properties": {"tileShape": s("rectangleRounded"), "rectangleRoundedCurve": lit("8L")}}],
                "fill": [state("default", {"show": lit("true"), "fillColor": solid(SIDEBAR), "transparency": lit("100D")}),
                         state("hover", {"fillColor": solid("#12211F"), "transparency": lit("0D")}),
                         state("selected", {"fillColor": solid("#16302D"), "transparency": lit("0D")})],
                "text": [state("default", {"fontColor": solid(MUTED), "fontSize": lit("11D"), "fontFamily": s(FONT),
                                           "horizontalAlignment": s("left"), "leftMargin": lit("14D")}),
                         state("selected", {"fontColor": solid(TEXT), "bold": lit("true")})],
                "outline": [state(k, {"show": lit("false"), "lineColor": solid(SIDEBAR), "transparency": lit("100D")})
                            for k in ("default", "hover", "selected")],
                "accentBar": [state("default", {"show": lit("false")}),
                              state("selected", {"show": lit("true"), "position": s("Left"), "width": lit("3D"),
                                                 "accentBarColor": solid(MINT)})],
            },
            "visualContainerObjects": {"background": [{"properties": {"show": lit("false")}}],
                                       "visualHeader": [{"properties": {"show": lit("false")}}]}}


class Page:
    def __init__(self, name, display, width=1280, height=720, kind=None):
        self.name, self.display, self.visuals = name, display, []
        self.no_filter, self.tiles, self.sidebar_labels = [], [], []
        self.width, self.height, self.kind = width, height, kind

    def add(self, vid, x, y, w, h, visual):
        n = len(self.visuals)
        self.visuals.append({"$schema": S_VISUAL, "name": vid, "visual": visual,
                             "position": {"x": x, "y": y, "z": n * 1000, "height": h, "width": w, "tabOrder": n * 1000}})

    def tile(self, vid, x, y, w, h, visual, **extra):
        """A rounded tile in the artwork with the visual inset on top of it."""
        self.tiles.append({"x": x, "y": y, "w": w, "h": h, **extra})
        if visual:
            self.add(vid, x + 4, y + 4, w - 8, h - 8, visual)

    def json(self):
        page = {"$schema": S_PAGE, "name": self.name, "displayName": self.display, "displayOption": "FitToPage",
                "height": self.height, "width": self.width,
                "objects": {"background": [{"properties": {"color": solid(BG_BOTTOM), "transparency": lit("0D"),
                                                           "image": {"image": {
                                                               "name": s(f"bg_{self.name}.png"),
                                                               "url": {"expr": {"ResourcePackageItem": {
                                                                   "PackageName": "RegisteredResources", "PackageType": 1,
                                                                   "ItemName": f"bg_{self.name}.png"}}},
                                                               "scaling": s("Fit")}}}}],
                            "outspace": [{"properties": {"color": solid(BG_BOTTOM)}}]}}
        if self.no_filter:
            page["visualInteractions"] = [{"source": a, "target": b, "type": "NoFilter"} for a, b in self.no_filter]
        if self.kind == "Tooltip":
            page.update({"displayOption": "ActualSize", "visibility": "HiddenInViewMode", "type": "Tooltip",
                         "pageBinding": {"name": f"{self.name}Binding", "type": "Tooltip", "parameters": []}})
            page["objects"] = {"background": [{"properties": {"color": solid(TILE), "transparency": lit("0D")}}]}
        return page


X0, W = 240, 1024           # main content area
GAP = 12
KPI_Y, KPI_H = 80, 106
R1, R2, RH = 198, 458, 248  # chart rows
FULL = 706 - R1


def sidebar(page, filters="all"):
    page.sidebar_labels.append(("PAGES", 104))
    page.add("navigator", 8, 116, 208, 172, navigator())

    def box(vid, y, label, visual):
        """Label in the artwork, then the dropdown box: 56 px per filter so nothing overlaps."""
        page.sidebar_labels.append((label, y))
        page.add(vid, 14, y + 12, 196, 40, visual)

    if filters in ("all", "year"):
        page.sidebar_labels.append(("FILTERS", 304))
        box("fYear", 326, "Year", slicer("Years", "year", "Year", "year", default=2023))
    if filters == "all":
        box("fType", 382, "Hospital type", slicer("hospital_year", "hospital_type", "Hospital type", "type"))
        box("fOwner", 438, "Owner", slicer("hospital_year", "ownership", "Owner", "owner"))
        box("fArea", 494, "Rural or urban", slicer("hospital_year", "rural_urban", "Rural or urban", "area"))
        box("fState", 550, "State", slicer("states", "state_name", "State", "state", search=True))
    if filters == "year":
        page.add("costNote", 14, 386, 196, 60, textbox(
            [("Costs and prices are national figures for general hospitals, so only the year filter applies here.",
              9, False, MUTED)]))
    if filters == "hospital":
        page.sidebar_labels.append(("LOOK UP", 304))
        box("search", 326, "Hospital name", slicer("hospital_profile", "hospital_label", "Hospital name", "hospital",
                                                   default="Cleveland Clinic Hospital (Cleveland, OH)", search=True))
        page.add("searchNote", 14, 386, 196, 60, textbox(
            [("Open the box and type part of a name, for example Mayo or Memorial.", 9, False, MUTED)]))
    page.add("credit", 14, 642, 200, 64, textbox(
        [("Data: CMS Hospital Cost Reports 2011-2023, CMS Medicare inpatient file, KFF", 8, False, MUTED),
         ("Built by Isaac Agyapong", 10, True, TEXT, FONT_BOLD)]))


def header(page, title, sub, chip=True):
    page.add("pageTitle", X0 - 4, 10, 720, 62, textbox(
        [(title, 19, True, TEXT, FONT_BOLD), (sub, 10, False, MUTED)]))
    if chip:
        page.add("chip", X0 + W - 300, 22, 300, 30, card("Selection Text", colour=MINT, size=9.5, font=FONT_BOLD,
                                                        pad=(0, 0, 10, 10), background=TILE, radius=15))


def kpi(page, i, x, y, w, glyph, label, accent, value, delta_text, delta_colour, spark=None, h=KPI_H, value_colour=TEXT,
        value_colour_measure=None, size=22):
    page.tile(f"kpiTile{i}", x, y, w, h, None, icon=glyph, label=label, accent=accent)
    vw = int(w * 0.58) if spark else w - 24
    page.add(f"kpi{i}", x + 10, y + 46, vw, 36, card(value, colour=value_colour, colour_measure=value_colour_measure, size=size))
    page.add(f"kpiDelta{i}", x + 10, y + 80, w - 20 if not spark else vw + 20, 20,
             card(delta_text, colour=MUTED, colour_measure=delta_colour, size=9, font=FONT_BOLD))
    if spark:
        entity, axis_col, measure, colour = spark
        page.add(f"kpiSpark{i}", x + vw + 4, y + 40, w - vw - 14, h - 50, sparkline(entity, axis_col, measure, colour))


def kpi_row(page, items, y=KPI_Y):
    w = (W - 3 * GAP) // 4
    for i, item in enumerate(items):
        kpi(page, i, X0 + i * (w + GAP), y, w, *item)


def build_pages():
    half = (W - GAP) // 2
    third = (W - 2 * GAP) // 3

    # ---------------------------------------------------------------- 1. Overview
    p1 = Page("overview", "Overview")
    sidebar(p1)
    header(p1, "U.S. Hospitals Financial Health Performance Overview",
           "Profits and losses of U.S. hospitals, 2011-2023, from their yearly reports to Medicare. Filter by year or group on the left.")
    kpi_row(p1, [
        ("↘", "Hospitals losing money", CORAL, "Losing Sel", "Losing Delta", "Losing Delta Colour",
         ("hospital_year", "fiscal_year", "Losing Share", CORAL)),
        ("⚠", "Lost money 2 years in a row", CORAL, "Stressed Sel", "Stressed Delta", "Stressed Delta Colour",
         ("hospital_year", "fiscal_year", "Stressed Count", CORAL)),
        ("%", "Typical profit margin", MINT, "Margin Sel", "Margin Delta", "Margin Delta Colour",
         ("hospital_year", "fiscal_year", "Typical Margin", MINT)),
        ("$", "Total hospital revenue", SKY, "Revenue Sel", "Revenue Delta", "Revenue Delta Colour",
         ("hospital_year", "fiscal_year", "Total Revenue", SKY)),
    ])
    p1.tile("trend", X0, R1, 600, RH, chart(
        "areaChart", {"Category": [C("hospital_year", "fiscal_year", "Year")], "Y": [M("Losing Share", "Share losing money")]},
        "=Trend Title", "Share of hospitals that spent more than they took in. COVID relief money kept 2020-2021 low.",
        objects={**axes(show_value=True, cat_size=10, value_start=0), **labels(9),
                 "dataPoint": [{"properties": {"fill": solid(CORAL)}}],
                 "lineStyles": [{"properties": {"strokeWidth": lit("3D"), "lineChartType": s("smooth"),
                                                "showMarker": lit("true"), "markerSize": lit("4D")}}]}))
    p1.tile("funnel", X0 + 600 + GAP, R1, W - 600 - GAP, RH, chart(
        "funnel", {"Category": [C("Stage", "stage", "Stage")], "Y": [M("Funnel Count", "Hospitals")]},
        "=Funnel Title", "From one bad year to lasting trouble",
        sort=(C("Stage", "stage"), "Ascending"),
        objects={"labels": [{"properties": {"show": lit("true"), "color": solid(TEXT), "fontSize": lit("11D"),
                                            "bold": lit("true"), "labelDisplayUnits": lit("1D")}}], "categoryAxis": [{"properties": {"color": solid(MUTED), "fontSize": lit("10D")}}],
                 "dataPoint": fill_by_value("Stage", "stage", {"All hospitals": SKY_D, "Lost money": CORAL_L,
                                                              "2 years in a row": CORAL, "3+ years in a row": CORAL_D})}))
    p1.tile("donut", X0, R2, third, RH, chart(
        "donutChart", {"Category": [C("hospital_year", "ownership", "Owner")], "Y": [M("Losing Count Sel", "Hospitals losing money")]},
        "=Donut Title", None,
        objects={"labels": [{"properties": {"show": lit("true"), "labelStyle": s("Percent of total"), "color": solid(TEXT),
                                            "fontSize": lit("11D"), "percentageLabelPrecision": lit("0L")}}],
                 "legend": [{"properties": {"show": lit("true"), "position": s("Bottom"), "labelColor": solid(TEXT),
                                            "fontSize": lit("10D"), "showTitle": lit("false")}}],
                 "slices": [{"properties": {"innerRadiusRatio": lit("62L")}}],
                 "dataPoint": fill_by_value("hospital_year", "ownership",
                                            {"Nonprofit": "#38BDF8", "For-profit": "#F472B6", "Government": "#E2E8F0"})}))
    p1.tile("areaType", X0 + third + GAP, R2, third, RH, chart(
        "clusteredBarChart", {"Category": [C("hospital_year", "area_type", "Group")], "Y": [M("Losing Sel", "Share losing money")]},
        "=Area Title", "Amber = rural, blue = urban",
        sort=(M("Losing Sel"), "Descending"),
        objects={**axes(cat_size=10, inner_padding=30, label_area=45), **labels(11), "dataPoint": fill_by("Area Colour")}))
    p1.tile("topStates", X0 + 2 * (third + GAP), R2, W - 2 * (third + GAP), RH, chart(
        "clusteredBarChart", {"Category": [C("states", "state_name", "State")], "Y": [M("Top 8 Losing", "Share losing money")]},
        "=States Title", None, sort=(M("Top 8 Losing"), "Descending"), tooltip_page="stateTooltip",
        objects={**axes(cat_size=10, inner_padding=12, label_area=35), **labels(10),
                 "dataPoint": [{"properties": {"fill": solid(CORAL)}}]}))

    # ---------------------------------------------------------------- 2. Who is struggling
    p2 = Page("struggling", "Who Is Struggling")
    sidebar(p2)
    header(p2, "Who is struggling",
           "Financial stress = lost money two years in a row. Critical access = small hospitals with 25 beds or fewer.")
    p2.tile("groups", X0, KPI_Y, 520, 706 - KPI_Y - RH - GAP, chart(
        "clusteredBarChart", {"Category": [C("hospital_year", "group_label", "Group")], "Y": [M("Group Stressed Sel", "Share under financial stress")]},
        "=Group Title", "Share that lost money two years in a row. Amber = rural, blue = urban. Groups with 30+ hospitals.",
        sort=(M("Group Stressed Sel"), "Descending"),
        objects={**axes(cat_size=10, inner_padding=26, label_area=50), **labels(10), "dataPoint": fill_by("Group Colour")}))
    p2.tile("treemap", X0 + 520 + GAP, KPI_Y, W - 520 - GAP, 706 - KPI_Y - RH - GAP, chart(
        "treemap", {"Group": [C("hospital_year", "area_type", "Group")], "Values": [M("Stressed Count Sel", "Hospitals")]},
        "=Treemap Title", "Bigger block = more hospitals",
        objects={**labels(12), "categoryLabels": [{"properties": {"show": lit("true"), "color": solid(INK_DARK), "fontSize": lit("11D")}}],
                 "legend": [{"properties": {"show": lit("false")}}],
                 "dataPoint": fill_by_value("hospital_year", "area_type", {
                     "Rural general": AMBER, "Rural critical access": AMBER_L,
                     "Urban general": SKY, "Urban critical access": SKY_L})}))
    p2.tile("streaks", X0, R2, half, RH, chart(
        "clusteredColumnChart", {"Category": [C("hospital_year", "streak_band", "Years in a row")], "Y": [M("Streak Count Sel", "Hospitals")]},
        "=Streak Title", "Hospitals that lost money in the selected year, by how many years in a row",
        sort=(C("hospital_year", "streak_band"), "Ascending"),
        objects={**axes(cat_size=10, inner_padding=30), **labels(11), "dataPoint": fill_by("Streak Colour")}))
    p2.tile("medicaid", X0 + half + GAP, R2, W - half - GAP, RH, chart(
        "clusteredColumnChart", {"Category": [C("hospital_year", "medicaid_fifth_label", "Medicaid share")], "Y": [M("Fifth Losing Sel", "Share losing money")]},
        "=Fifth Title", "Hospitals split into five equal groups by their share of Medicaid patients",
        sort=(C("hospital_year", "medicaid_fifth_label"), "Ascending"),
        objects={**axes(cat_size=10, inner_padding=30), **labels(11), "dataPoint": fill_by("Fifth Colour")}))

    # ---------------------------------------------------------------- 3. States
    p3 = Page("states", "States")
    sidebar(p3)
    header(p3, "Where hospitals are losing money",
           "Share of hospitals that lost money, by state. Hover over a state for details.")
    kpi_row(p3, [
        ("⚠", "Hardest-hit state", CORAL, "Worst State", "Worst State Context", None, None),
        ("⛨", "States where half or more lost money", CORAL, "Half Losing States", "Half Losing Context", None, None),
        ("⚕", "Losing money, Medicaid expanded", SKY, "Expanded Losing", "Expanded Losing Context", None, None),
        ("$", "Unpaid care, no expansion", AMBER, "Unpaid Not Expanded", "Unpaid Context", None, None),
    ])
    every_cell = {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": "hospital_year.Tile Label"}
    hidden_header = {"fontColor": solid(TILE), "backColor": solid(TILE), "fontSize": lit("6D")}
    tile_map = chart(
        "pivotTable", {"Rows": [C("states", "tile_y")], "Columns": [C("states", "tile_x")], "Values": [M("Tile Label", " ")]},
        "=Map Title", "Brighter red = more hospitals losing money", tooltip_page="stateTooltip",
        objects={
            "values": [{"properties": {"fontSize": lit("12D"), "bold": lit("true"), "fontColor": solid(TEXT),
                                       "backColorPrimary": solid(TILE), "backColorSecondary": solid(TILE)}},
                       {"properties": {"backColor": by_measure("Tile Colour")}, "selector": every_cell}],
            "columnHeaders": [{"properties": hidden_header}], "rowHeaders": [{"properties": hidden_header}],
            "subTotals": [{"properties": {"rowSubtotals": lit("false"), "columnSubtotals": lit("false")}}],
            "grid": [{"properties": {"gridVertical": lit("true"), "gridVerticalColor": solid(TILE),
                                     "gridVerticalWeight": lit("4D"), "gridHorizontal": lit("true"),
                                     "gridHorizontalColor": solid(TILE), "gridHorizontalWeight": lit("4D"),
                                     "outlineColor": solid(TILE), "rowPadding": lit("7D")}}]})
    p3.tile("tileMap", X0, R1, 560, FULL, tile_map)
    legend = [("Losing money   ", 9, True, MUTED)]
    for colr, lab in [("#2B4A4C", "under 20%"), ("#6B4A4C", "20-30%"), ("#A14F4F", "30-40%"), (CORAL_D, "40-50%"), (CORAL, "50%+")]:
        legend += [("■ ", 12, False, colr), (lab + "   ", 9, False, MUTED)]
    p3.add("mapLegend", X0 + 16, 706 - 40, 528, 30, textbox([legend]))
    p3.no_filter += [("tileMap", "top10")]
    tw = W - 560 - GAP
    p3.tile("top10", X0 + 560 + GAP, R1, tw, RH, chart(
        "tableEx", {"Values": [C("states", "state_name", "State"), C("states", "medicaid_status", "Medicaid"),
                               M("Top 10 Hospitals", "Hospitals"), M("Top 10 Losing", "Losing money"),
                               M("Top 10 Margin", "Margin")]},
        "The 10 states with the most hospitals losing money", None, tooltip_page="stateTooltip",
        sort=(M("Top 10 Losing"), "Descending"),
        objects={"values": [{"properties": {"fontSize": lit("10D"), "fontColor": solid(TEXT), "backColorPrimary": solid(TILE),
                                            "backColorSecondary": solid("#172B2D")}}],
                 "columnHeaders": [{"properties": {"fontSize": lit("10D"), "fontColor": solid(MUTED), "backColor": solid(TILE),
                                                   "fontFamily": s(FONT_BOLD)}}],
                 "grid": [{"properties": {"rowPadding": lit("1D"), "gridHorizontalColor": solid(GRID),
                                          "gridVerticalColor": solid(GRID), "outlineColor": solid(GRID)}}],
                 "total": [{"properties": {"totals": lit("false")}}],
                 "columnFormatting": [{"properties": {"dataBars": {"positiveColor": solid(CORAL_D), "negativeColor": solid(CORAL_D),
                                                                   "axisColor": solid(TILE), "reverseDirection": lit("false"),
                                                                   "hideText": lit("false")}},
                                       "selector": {"metadata": "hospital_year.Top 10 Losing"}}],
                 "columnWidth": [{"properties": {"value": lit(f"{w}D")}, "selector": {"metadata": k}} for k, w in {
                     "states.state_name": 100, "states.medicaid_status": 96, "hospital_year.Top 10 Hospitals": 74,
                     "hospital_year.Top 10 Losing": 84, "hospital_year.Top 10 Margin": 62}.items()]}))
    p3.tile("unpaid", X0 + 560 + GAP, R2, tw, RH, chart(
        "lineChart", {"Category": [C("unpaid_care", "fiscal_year", "Year")], "Series": [C("unpaid_care", "medicaid_status", "Medicaid")],
                      "Y": [M("Unpaid Care Share", "Unpaid care, share of costs")]},
        "=Unpaid Title", "Charity care and unpaid bills as a share of costs. Blue = expanded, amber = did not expand.",
        objects={**axes(show_value=True, cat_size=10, value_start=0), "legend": [{"properties": {"show": lit("false")}}],
                 "dataPoint": fill_by_value("unpaid_care", "medicaid_status", {"Expanded": SKY, "Not expanded": AMBER}),
                 "lineStyles": [{"properties": {"strokeWidth": lit("3D"), "lineChartType": s("smooth"),
                                                "showMarker": lit("true"), "markerSize": lit("4D")}}]}))

    # ---------------------------------------------------------------- 4. Costs and prices
    p4 = Page("costs", "Costs and Prices")
    sidebar(p4, filters="year")
    header(p4, "Costs and prices",
           "Cost per stay = total costs divided by patients discharged. Agency staff = travel nurses and other contract workers.")
    kpi_row(p4, [
        ("$", "Typical cost of a hospital stay", SKY, "Cost Sel", "Cost Delta", "Cost Delta Colour",
         ("cost_trend", "fiscal_year", "Cost per Stay", SKY)),
        ("⏱", "Agency staff, share of salaries", AMBER, "Agency Sel", "Agency Delta", "Agency Delta Colour",
         ("cost_trend", "fiscal_year", "Agency Staff Share", AMBER)),
        ("↗", "List price vs actual cost", VIOLET, "Markup Sel", "Markup Delta", "Markup Delta Colour",
         ("cost_trend", "fiscal_year", "Markup", VIOLET)),
        ("⚖", "Billed per $1 Medicare paid", VIOLET, "Billed Sel", "Billed Delta", "Billed Delta Colour",
         ("cost_trend", "fiscal_year", "Billed per Dollar", VIOLET)),
    ])
    p4.tile("costTrend", X0, R1, half, RH, chart(
        "areaChart", {"Category": [C("cost_trend", "fiscal_year", "Year")], "Y": [M("Cost per Stay", "Typical cost per stay")]},
        "=Cost Title", "Typical cost per patient discharged, general hospitals",
        objects={**axes(show_value=True, cat_size=10, value_start=0), **labels(show=False),
                 "dataPoint": [{"properties": {"fill": solid(SKY)}}],
                 "lineStyles": [{"properties": {"strokeWidth": lit("3D"), "lineChartType": s("smooth")}}]}))
    p4.tile("agency", X0 + half + GAP, R1, W - half - GAP, RH, chart(
        "clusteredColumnChart", {"Category": [C("cost_trend", "fiscal_year", "Year")], "Y": [M("Agency Staff Share", "Agency staff, share of salaries")]},
        "=Agency Title", "Contract (agency) labor as a share of hospital salaries. Amber = selected year.",
        sort=(C("cost_trend", "fiscal_year"), "Ascending"),
        objects={**axes(show_value=True, cat_size=9, inner_padding=20, categorical=True), "dataPoint": fill_by("Cost Year Colour")}))
    p4.tile("billed", X0, R2, half, RH, chart(
        "clusteredBarChart", {"Category": [C("billed_state", "state_name", "State")], "Y": [M("Top Billed", "Billed per $1 paid")]},
        "Nevada hospitals bill Medicare $8.85 for every $1 they are paid", "Top 10 states, Medicare inpatient stays 2023",
        sort=(M("Top Billed"), "Descending"),
        objects={**axes(cat_size=10, inner_padding=10, label_area=35), **labels(10),
                 "dataPoint": [{"properties": {"fill": solid(VIOLET)}}]}))
    p4.tile("prices", X0 + half + GAP, R2, W - half - GAP, RH, chart(
        "lineChart", {"Category": [C("cost_trend", "fiscal_year", "Year")],
                      "Y": [M("Markup", "List price vs cost"), M("Billed per Dollar", "Billed per $1 Medicare paid")]},
        "List prices keep climbing faster than costs and payments",
        "List price vs cost (violet) and amount billed per $1 Medicare paid (sky)",
        objects={**axes(show_value=True, cat_size=10, value_start=0),
                 "legend": [{"properties": {"show": lit("true"), "position": s("Top"), "labelColor": solid(MUTED), "fontSize": lit("9D")}}],
                 "dataPoint": [{"properties": {"fill": solid(VIOLET)}, "selector": {"metadata": "cost_trend.Markup"}},
                               {"properties": {"fill": solid(SKY)}, "selector": {"metadata": "cost_trend.Billed per Dollar"}}],
                 "lineStyles": [{"properties": {"strokeWidth": lit("3D"), "lineChartType": s("smooth")}}]}))

    # ---------------------------------------------------------------- 5. Find a hospital
    p5 = Page("lookup", "Find a Hospital")
    sidebar(p5, filters="hospital")
    header(p5, "Find a hospital", "Is it making or losing money, and how does it compare? Numbers come from its latest yearly cost report.",
           chip=False)
    p5.tile("nameTile", X0, KPI_Y, W, 64, None)
    p5.add("nameTitle", X0 + 14, KPI_Y + 6, W - 28, 32, card("Profile Name", colour=TEXT, size=17))
    p5.add("nameFacts", X0 + 14, KPI_Y + 36, W - 28, 22, card("Profile Facts", colour=MUTED, size=10, font=FONT))
    ky = KPI_Y + 64 + GAP
    kpi_row(p5, [
        ("%", "Profit margin", MINT, "Profile Margin", "Profile Margin Context", None,
         ("hospital_year", "fiscal_year", "Hospital Margin", MINT)),
        ("$", "Profit or loss", SKY, "Profile Result", "Profile Result Context", None, None),
        ("⌛", "Years in a row losing money", CORAL, "Profile Streak", "Profile Streak Context", None, None),
        ("★", "Rank in its state", AMBER, "Profile Rank", "Profile Rank Context", None, None),
    ], y=ky)
    for i, m in [(0, "Profile Margin Colour"), (2, "Profile Streak Colour")]:
        p5.visuals[[v["name"] for v in p5.visuals].index(f"kpi{i}")]["visual"]["objects"]["labels"][0]["properties"]["color"] = by_measure(m)
    ry = ky + KPI_H + GAP
    rh = 706 - ry
    p5.tile("hospTrend", X0, ry, 560, rh, chart(
        "clusteredColumnChart", {"Category": [C("hospital_year", "fiscal_year", "Year")], "Y": [M("Hospital Margin", "Profit margin")]},
        "Profit margin by year", "Green = made money, red = lost money",
        sort=(C("hospital_year", "fiscal_year"), "Ascending"),
        objects={**axes(cat_size=10, inner_padding=22, categorical=True), **labels(9), "dataPoint": fill_by("Hospital Margin Colour")}))
    gw = W - 560 - GAP
    p5.tile("gauge", X0 + 560 + GAP, ry, gw, rh // 2 + 30, chart(
        "gauge", {"Y": [M("Profile Margin", "Profit margin")], "MinValue": [M("Gauge Min")], "MaxValue": [M("Gauge Max")],
                  "TargetValue": [M("State Typical Margin", "Typical in its state")]},
        "Its profit margin against the typical hospital in its state", "Amber marker = typical hospital in the same state",
        objects={"dataPoint": [{"properties": {"fill": solid(MINT), "target": solid(AMBER)}}],
                 "calloutValue": [{"properties": {"color": solid(TEXT), "fontSize": lit("20D")}}],
                 "labels": [{"properties": {"color": solid(MUTED), "fontSize": lit("9D")}}],
                 "target": [{"properties": {"color": solid(AMBER), "fontSize": lit("9D")}}]}))
    my = ry + rh // 2 + 30 + GAP
    p5.tile("meaning", X0 + 560 + GAP, my, gw, 706 - my, None)
    p5.add("meaningTitle", X0 + 560 + GAP + 14, my + 8, gw - 28, 26, textbox([("What this means", 13, True, TEXT, FONT_BOLD)]))
    p5.add("meaningText", X0 + 560 + GAP + 10, my + 34, gw - 20, 706 - my - 40,
           card("Profile Sentence", colour=TEXT, size=11, font=FONT))

    # ---------------------------------------------------------------- 6. Data notes
    p6 = Page("dataNotes", "Data Notes")
    sidebar(p6, filters="none")
    header(p6, "Data notes", "What the words mean, where the data comes from, and what to keep in mind", chip=False)
    cols = [
        ("Words used here", MINT, [
            "Profit margin: the share of each dollar of income a hospital keeps after paying its costs "
            "(all income, including investments and donations)",
            "Losing money: costs were higher than income for the year",
            "Financial stress: lost money two years in a row",
            "Typical: the middle hospital (median), so a few very large hospitals do not skew it",
            "Critical access hospital: a small rural hospital (25 beds or fewer) that Medicare pays based on its costs",
            "Unpaid care: free (charity) care plus bills patients never paid"]),
        ("Where the data comes from", SKY, [
            "CMS Hospital Provider Cost Reports 2011-2023: the yearly financial report every Medicare hospital files",
            "CMS Medicare Inpatient Hospitals by Provider 2013-2023 (billed vs paid)",
            "KFF tracker of when each state expanded Medicaid",
            "Loaded into PostgreSQL and checked: my all-hospital margins match the figures MedPAC (the commission that "
            "advises Congress on Medicare) publishes from the same reports, within about 1 point",
            "Forecasts of which hospitals will fall into financial stress: see the companion machine learning project"]),
        ("Keep in mind", CORAL, [
            "Hospitals in a health system often hold little cash of their own, so cash is not used to judge them",
            "Reports shorter than 300 days (openings, closures, owner changes) and impossible margins (over 100%) are left out",
            "A hospital's fiscal year may not match the calendar year",
            "Maryland sets hospital prices by law, so its billed vs paid numbers are not comparable",
            "COVID relief money counts as income, which lifted 2020 and 2021 margins",
            "Rural means outside a metro area. Some city hospitals are paid by Medicare as if rural; here they count as urban"]),
    ]
    for i, (heading, colour, lines) in enumerate(cols):
        x = X0 + i * (third + GAP)
        p6.tile(f"notes{i + 1}", x, KPI_Y, third, 706 - KPI_Y, textbox(
            [(heading, 15, True, colour, FONT_BOLD)] + [("•  " + t, 11, False, TEXT) for t in lines], pad=(14, 10, 16, 16)))

    # ---------------------------------------------------------------- tooltip: state
    tt = Page("stateTooltip", "State Tooltip", width=320, height=240, kind="Tooltip")
    tt.add("ttName", 4, 4, 312, 36, card("Selected State", colour=TEXT, size=16))
    tt.add("ttStatus", 4, 40, 312, 24, card("State Status", colour=AMBER, size=10, font=FONT))
    tt.add("ttLosingLabel", 12, 76, 140, 20, textbox([("Losing money", 9, False, MUTED)]))
    tt.add("ttLosing", 8, 94, 148, 40, card("Losing Sel", colour=CORAL, size=20))
    tt.add("ttMarginLabel", 168, 76, 140, 20, textbox([("Typical margin", 9, False, MUTED)]))
    tt.add("ttMargin", 164, 94, 148, 40, card("State Margin Sel", colour=MINT, size=20))
    tt.add("ttCountLabel", 12, 146, 140, 20, textbox([("Hospitals", 9, False, MUTED)]))
    tt.add("ttCount", 8, 164, 148, 40, card("State Hospitals Sel", colour=TEXT, size=20))
    tt.add("ttRank", 164, 150, 148, 70, card("State Rank Text", colour=MUTED, size=10, font=FONT))
    return [p1, p2, p3, p4, p5, p6, tt]


def find_base_theme():
    install = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-AppxPackage Microsoft.MicrosoftPowerBIDesktop | Sort-Object Version | Select-Object -Last 1).InstallLocation"],
        capture_output=True, text=True).stdout.strip()
    f = Path(install) / "bin/WebView2Resources/minerva/sharedresources/BaseThemes" / f"{BASE_THEME}.json"
    if install and f.exists():
        return f
    raise FileNotFoundError("Power BI Desktop (Microsoft Store version) base theme not found")


def build_report():
    shutil.rmtree(RPT, ignore_errors=True)
    pages = build_pages()
    # artwork: one background per page, drawn from the same tile positions
    colours = {"bg_top": BG_TOP, "bg_bottom": BG_BOTTOM, "sidebar": SIDEBAR, "tile": TILE, "tile_border": TILE_BORDER,
               "text": TEXT, "muted": MUTED, "mint": MINT, "sky": SKY, "glow": MINT, "ink_dark": INK_DARK}
    ASSETS.mkdir(parents=True, exist_ok=True)
    write_json(ASSETS / "layout.json", {"colors": colours, "pages": {
        p.name: {"tiles": p.tiles, "sidebar_labels": p.sidebar_labels} for p in pages if p.kind != "Tooltip"}})
    subprocess.run([sys.executable, str(ROOT / "Python" / "make_background.py")], check=True)
    images = [f"bg_{p.name}.png" for p in pages if p.kind != "Tooltip"]

    d = RPT / "definition"
    write_json(RPT / "definition.pbir", {"$schema": S_PBIR, "version": "4.0",
                                         "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    write_json(d / "version.json", {"$schema": S_VERSION, "version": "2.0.0"})
    write_json(d / "report.json", {
        "$schema": S_REPORT,
        "themeCollection": {
            "baseTheme": {"name": BASE_THEME, "reportVersionAtImport": "5.59", "type": "SharedResources"},
            "customTheme": {"name": CUSTOM_THEME, "reportVersionAtImport": "5.59", "type": "RegisteredResources"}},
        "layoutOptimization": "None",
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": BASE_THEME, "path": f"BaseThemes/{BASE_THEME}.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": CUSTOM_THEME, "path": CUSTOM_THEME, "type": "CustomTheme"},
                       *[{"name": im, "path": im, "type": "Image"} for im in images]]}]})
    static = RPT / "StaticResources"
    (static / "SharedResources" / "BaseThemes").mkdir(parents=True, exist_ok=True)
    shutil.copy(find_base_theme(), static / "SharedResources" / "BaseThemes" / f"{BASE_THEME}.json")
    (static / "RegisteredResources").mkdir(parents=True, exist_ok=True)
    for im in images:
        shutil.copy(ASSETS / im, static / "RegisteredResources" / im)
    write_json(static / "RegisteredResources" / CUSTOM_THEME, {
        "name": "Hospital Dark",
        "dataColors": [SKY, CORAL, MINT, AMBER, VIOLET, SLATE, SKY_L, CORAL_L],
        "foreground": TEXT, "foregroundNeutralSecondary": MUTED, "background": TILE, "backgroundLight": "#1B3033",
        "tableAccent": MINT,
        "textClasses": {"title": {"fontFace": FONT_BOLD, "color": TEXT}, "label": {"fontFace": FONT, "color": MUTED},
                        "callout": {"fontFace": FONT_BOLD, "color": TEXT}}})
    write_json(d / "pages" / "pages.json", {"$schema": S_PAGES, "pageOrder": [p.name for p in pages],
                                            "activePageName": pages[0].name})
    for p in pages:
        write_json(d / "pages" / p.name / "page.json", p.json())
        for v in p.visuals:
            write_json(d / "pages" / p.name / "visuals" / v["name"] / "visual.json", v)


def main():
    build_model()
    build_report()
    write_json(DASH / f"{NAME}.pbip", {"$schema": S_PBIP, "version": "1.0",
                                       "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                       "settings": {"enableAutoRecovery": True}})
    print(f"wrote dashboard/{NAME}.pbip")


if __name__ == "__main__":
    main()
