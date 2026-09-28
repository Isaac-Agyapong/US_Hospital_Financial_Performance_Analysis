"""Download every source file and record URL, size and SHA-256 in Data/raw/manifest.json.

Sources (all public):
  - CMS Hospital Provider Cost Report, 2011-2023: finances of every Medicare-certified hospital
  - CMS Medicare Inpatient Hospitals - by Provider, 2013-2023: what hospitals charge vs what Medicare pays
  - KFF Medicaid expansion status by state (the table embedded in the KFF page)

File URLs are looked up in the CMS catalog (data.cms.gov/data.json) by dataset title and year, not hard-coded, so the
script keeps working when CMS re-publishes a file. Downloads use curl (resumable, retried).
"""
import csv
import hashlib
import io
import json
import re
import subprocess
import urllib.parse
from datetime import date
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "Data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
CATALOG = "https://data.cms.gov/data.json"
DATASETS = {"Hospital Provider Cost Report": ("cost_report", range(2011, 2024)),
            "Medicare Inpatient Hospitals - by Provider": ("inpatient", range(2013, 2024))}
KFF = "https://www.kff.org/affordable-care-act/issue-brief/status-of-state-medicaid-expansion-decisions/"


def curl(url, out):
    subprocess.run(["curl", "-L", "--fail", "--retry", "5", "--silent", "--show-error", "-A", "Mozilla/5.0",
                    "-o", str(out), url], check=True)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def catalog_urls():
    curl(CATALOG, RAW / "cms_catalog.json")
    cat = json.loads((RAW / "cms_catalog.json").read_text(encoding="utf-8"))
    (RAW / "cms_catalog.json").unlink()
    files = {}
    for title, (stem, years) in DATASETS.items():
        ds = next(d for d in cat["dataset"] if d["title"] == title)
        for dist in ds["distribution"]:
            url = dist.get("downloadURL", "")
            year = int(dist.get("temporal", "0000")[:4] or 0)
            if url.lower().endswith(".csv") and year in years:
                files[f"{stem}_{year}.csv"] = url
    return files


def kff_table():
    html = RAW / "kff_expansion_status.html"
    curl(KFF, html)
    t = html.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'data:text/csv;charset=utf-8,(State%2CState%20Abbrev\.%2CExpansion%20Status[^"]+)', t)
    if not m:
        raise RuntimeError("KFF page layout changed: embedded CSV not found")
    rows = list(csv.reader(io.StringIO(urllib.parse.unquote(m.group(1)))))
    out = RAW / "kff_expansion_status.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["state_name", "state_abbrev", "expansion_status", "implementation_note"])
        for r in rows[1:]:
            w.writerow([r[0].strip(), r[1].strip(" *"), r[2].strip(), " ".join(r[3].split())])
    html.unlink()
    return out


def main():
    manifest = {"downloaded": str(date.today()), "files": {}}
    for name, url in sorted(catalog_urls().items()):
        out = RAW / name
        if not out.exists() or out.stat().st_size == 0:
            print("downloading", name)
            curl(url, out)
        manifest["files"][name] = {"url": url, "bytes": out.stat().st_size, "sha256": sha256(out)}
    k = kff_table()
    manifest["files"][k.name] = {"url": KFF + " (embedded CSV)", "bytes": k.stat().st_size, "sha256": sha256(k)}
    (RAW / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"{len(manifest['files'])} files, {sum(v['bytes'] for v in manifest['files'].values()) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
