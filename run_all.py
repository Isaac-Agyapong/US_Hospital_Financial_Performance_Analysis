"""Rebuild the whole project in order.

    python run_all.py              full rebuild (a few minutes after the first download)
    python run_all.py --skip-download

Steps: download sources -> load PostgreSQL (raw, core, analytics) -> data quality and business queries
-> analysis notebook -> Power BI extracts -> Power BI project.
The machine learning forecast is a separate project that reads this database.
"""
import subprocess
import sys
import time
from pathlib import Path

PY = Path(__file__).resolve().parent / "Python"
STEPS = [
    ("01_download_data.py", "download source files"),
    ("02_load_postgres.py", "load PostgreSQL star schema"),
    ("03_run_sql_queries.py", "data quality + business questions -> SQL/query_results.md"),
    ("04_build_notebook.py", "build and execute Python/04_analysis.ipynb"),
    ("05_export_powerbi.py", "export tables for the Power BI report"),
    ("06_build_powerbi_project.py", "generate the Power BI project (PBIP)"),
]


def main():
    start = time.time()
    for script, what in STEPS:
        if script.startswith("01_") and "--skip-download" in sys.argv:
            continue
        if not (PY / script).exists():
            print(f"-- skipping {script} (not present)")
            continue
        print(f"\n== {script}: {what}")
        t = time.time()
        subprocess.run([sys.executable, script], cwd=PY, check=True)
        print(f"   done in {time.time() - t:.0f}s")
    print(f"\nall steps finished in {(time.time() - start) / 60:.1f} min")


if __name__ == "__main__":
    main()
