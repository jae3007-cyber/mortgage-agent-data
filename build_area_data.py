#!/usr/bin/env python3
"""Builds AreaData.json + manifest.json — the data behind the app's Address Lookup screen.

Runs unattended (GitHub Actions, monthly — see .github/workflows/monthly-update.yml) and needs no
API keys. Every source sits at a predictable yearly URL, so the newest year is found by probing:

  Loan limits  HUD CHUMS files, one pair per calendar year, back to 2020:
               https://www.hud.gov/pub/chums/cyYYYY-gse-limits.txt      (conforming — same values
               https://www.hud.gov/pub/chums/cyYYYY-forward-limits.txt   as FHFA's list) / FHA
               HUD posts next year's files each November/December.
  Area medians Census ACS 5-year table-based summary files (median value B25077, gross rent B25064,
               real estate taxes B25103) — https://www2.census.gov/programs-surveys/acs/summary_file/
               Census posts each new 5-year release in December.

Output is deterministic (no timestamps), so a month with no new source data produces byte-identical
files and the workflow commits nothing. Sanity checks abort the run rather than publish a broken
file — the app keeps whatever it already has.

    python3 build_area_data.py [--out DIR]
"""
import argparse, datetime, hashlib, json, os, sys, urllib.error, urllib.request
from collections import Counter

SCHEMA = 2          # bump (and the app's supported schema) only for incompatible format changes
FIRST_YEAR = 2020   # oldest loan-limit year kept for the history chart
UA = {"User-Agent": "Mozilla/5.0 (area-data builder)"}

HUD = "https://www.hud.gov/pub/chums/cy{year}-{kind}-limits.txt"
ACS = ("https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/"
       "5YRData/acsdt5y{year}-{table}.dat")

# State FIPS by postal abbreviation — HUD's files carry the abbreviation, Census uses the code.
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10",
    "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19",
    "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35",
    "NY": "36", "NC": "37", "ND": "38", "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
    "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56", "AS": "60", "GU": "66", "MP": "69", "PR": "72", "VI": "78",
}


def fetch(url):
    """Returns the body as text, or None if the file doesn't exist (yet)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
            return r.read().decode("latin-1")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def parse_hud(text):
    """HUD CHUMS limit file: fixed width, 174 chars. Rows without a state are the national
    floor/ceiling lines. Returns {county FIPS: [1-unit, 2-unit, 3-unit, 4-unit]}."""
    out = {}
    for line in text.splitlines():
        if len(line) < 106 or line[101:103] not in STATE_FIPS:
            continue
        try:
            limits = [int(line[i:i + 7]) for i in (73, 80, 87, 94)]
        except ValueError:
            continue
        out[STATE_FIPS[line[101:103]] + line[103:106]] = (line[101:103], limits)
    return out


def load_limits():
    years = {}
    for year in range(FIRST_YEAR, datetime.date.today().year + 2):
        gse, fha = fetch(HUD.format(year=year, kind="gse")), fetch(HUD.format(year=year, kind="forward"))
        if gse and fha:
            years[year] = (parse_hud(gse), parse_hud(fha))
            print(f"limits {year}: {len(years[year][0])} counties")
    if not years:
        sys.exit("no HUD loan limit files found")
    return years


def load_acs():
    """Newest ACS 5-year release that has all three tables. Negative estimates are Census
    'not available' codes; topcoded values (2000001 / 3501 / 10001) are kept — the app shows X+."""
    this_year = datetime.date.today().year
    for year in range(this_year - 1, this_year - 4, -1):
        tables = {t: fetch(ACS.format(year=year, table=t)) for t in ("b25077", "b25064", "b25103")}
        if not all(tables.values()):
            continue
        county, zips = {}, {}
        for slot, t in enumerate(("b25077", "b25064", "b25103")):
            for line in tables[t].splitlines()[1:]:
                parts = line.split("|")
                try:
                    v = int(parts[1])
                except (IndexError, ValueError):
                    continue
                if v < 0:
                    continue
                geo = parts[0]
                if geo.startswith("0500000US"):
                    county.setdefault(geo[9:], [0, 0, 0])[slot] = v
                elif geo.startswith("860Z200US"):
                    zips.setdefault(geo[9:], [0, 0, 0])[slot] = v
        print(f"ACS {year}: {len(county)} counties, {len(zips)} ZIPs")
        return year, county, zips
    sys.exit("no ACS 5-year release found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    out_dir = parser.parse_args().out

    limit_years = load_limits()
    acs_year, acs_county, acs_zips = load_acs()

    years = sorted(limit_years)
    current_gse, current_fha = limit_years[years[-1]]

    counties = {}
    for fips, (state, conforming) in sorted(current_gse.items()):
        if fips not in current_fha:
            continue
        v, r, t = acs_county.get(fips, [0, 0, 0])
        history = []
        for y in years:
            gse, fha = limit_years[y]
            history.append(gse[fips][1] + fha[fips][1] if fips in gse and fips in fha else None)
        counties[fips] = {"s": state, "c": conforming, "f": current_fha[fips][1],
                          "v": v, "r": r, "t": t, "h": history}

    # National baseline conforming limit and FHA floor per year, per unit count — the reference
    # lines on the app's history chart. Taken as the most common county value rather than the
    # minimum: HUD's files occasionally keep retired county codes with stale limits (cy2025 has
    # three dissolved Alaska census areas still at 2008-era values).
    def most_common(values):
        return Counter(values).most_common(1)[0][0]

    national = {"baseline": [], "fhaFloor": []}
    for y in years:
        gse, fha = limit_years[y]
        national["baseline"].append([most_common(v[1][i] for v in gse.values()) for i in range(4)])
        national["fhaFloor"].append([most_common(v[1][i] for v in fha.values()) for i in range(4)])

    # Abort instead of publishing something broken; the app keeps its current copy.
    if len(counties) < 3000 or len(acs_zips) < 25000 or national["baseline"][-1][0] < 100000:
        sys.exit(f"sanity check failed: {len(counties)} counties, {len(acs_zips)} ZIPs")

    data = {
        "schema": SCHEMA,
        "limitsYear": years[-1],
        "acsLabel": f"{acs_year - 4}–{acs_year} ACS 5-Year",
        "years": years,
        "national": national,
        "counties": counties,
        "zips": dict(sorted(acs_zips.items())),
    }
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    manifest = {
        "schema": SCHEMA,
        "version": digest[:16],
        "sha256": digest,
        "bytes": len(body),
        "file": "AreaData.json",
        "limitsYear": years[-1],
        "acsYear": acs_year,
    }

    with open(os.path.join(out_dir, "AreaData.json"), "wb") as f:
        f.write(body)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {len(counties)} counties, {len(acs_zips)} ZIPs, {len(body) // 1024} KB, "
          f"version {manifest['version']}")


if __name__ == "__main__":
    main()
