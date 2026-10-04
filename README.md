# Mortgage Agent — Area Data

Public data file behind the app's **Address Lookup** screen: county loan limits (conforming + FHA,
2020 → current year) and Census area medians (home value, rent, property tax) by county and ZIP.

A GitHub Actions job rebuilds it on the 2nd of every month. Most months nothing upstream changes
and nothing is committed. New loan limits (HUD/FHFA, Nov–Dec) and new Census medians (Dec) are
picked up automatically. The app checks `manifest.json` every 30 days and downloads the new file
when its `version` changes.

| File | What it is |
|---|---|
| `AreaData.json` | The data (~2.5 MB) |
| `manifest.json` | Version + SHA-256 the app checks before downloading |
| `build_area_data.py` | Builder — downloads HUD + Census files, no API keys |
| `.github/workflows/monthly-update.yml` | The monthly job |

## Sources

- HUD CHUMS loan limit files — `https://www.hud.gov/pub/chums/cyYYYY-{gse,forward}-limits.txt`
- U.S. Census Bureau, ACS 5-year table-based summary files (B25077, B25064, B25103)

All public domain U.S. government data.

## Run it by hand

```bash
python3 build_area_data.py
```

Or on GitHub: **Actions → Monthly area data update → Run workflow**.

## If the format ever changes incompatibly

Bump `SCHEMA` in `build_area_data.py` *and* the app's `AreaDataStore.supportedSchema` together,
ship the app update first, then push the new builder. Older app versions ignore any manifest whose
schema they don't support and keep their last good copy.


## LLPA data (LLPA Pricing Map)

`LLPAData.json` + `llpa-manifest.json` hold the LLPA grids behind the app's **LLPA Pricing Map**:
the credit-score × LTV base grids for purchase, no-cash-out refinance and cash-out, plus the
special attributes (condo, 2–4 units, investment, second home, manufactured, ARM, high-balance
fixed/ARM, subordinate financing).

Built every Monday by `build_llpa_data.py` (workflow `weekly-llpa-update.yml`) from Freddie Mac's
**Exhibit 19, Credit Fees** — a public PDF at
`https://guide.freddiemac.com/euf/assets/pdfs/Exhibit_19.pdf`. Fannie Mae's matrix isn't fetched
(their site blocks automated downloads) but has matched Freddie's grids since FHFA's 2023 pricing
alignment, so the app uses this one dataset for both and says so. The app checks `llpa-manifest.json`
weekly and downloads the file when `version` changes.

If Freddie changes the PDF layout, the parser's checks fail, the job goes red, and nothing is
published (the app keeps its last good copy). Run it by hand:

```bash
pip install pdfplumber
python3 build_llpa_data.py            # downloads the current PDF
python3 build_llpa_data.py --pdf Exhibit_19.pdf   # or use a local copy
```
Bump `SCHEMA` in `build_llpa_data.py` *and* the app's `LLPADataStore.supportedSchema` together for
any incompatible format change (ship the app update first).
