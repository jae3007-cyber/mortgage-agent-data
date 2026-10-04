#!/usr/bin/env python3
"""Builds LLPAData.json + llpa-manifest.json — the data behind the app's LLPA Pricing Map.

Source: Freddie Mac Single-Family Seller/Servicer Guide, Exhibit 19 "Credit Fees" (a public PDF,
re-issued whenever a bulletin changes the grids). Fannie Mae's matrix is not fetched: Fannie's site
sits behind a bot check that blocks unattended downloads, and since FHFA's January 2023 pricing
framework the two agencies' base grids and special attributes have been identical. The app uses
this one dataset for both agencies and says so.

The PDF is found at its stable guide URL, with Freddie's own content API as a fallback:
  https://guide.freddiemac.com/euf/assets/pdfs/Exhibit_19.pdf
  https://guide.freddiemac.com/cc/data/getAnswerById/answerId/1001717   (Exhibit 19 -> PDF link)

Output is deterministic (no timestamps), so a run with no new bulletin produces byte-identical files
and the workflow commits nothing. If the PDF layout changes in a way the parser doesn't recognize,
validation fails, the run aborts, and nothing is published — the app keeps its last good copy.

    pip install pdfplumber
    python3 build_llpa_data.py [--out DIR] [--pdf LOCAL.pdf]
"""
import argparse, hashlib, json, os, re, sys, urllib.request

SCHEMA = 1   # bump (and the app's LLPADataStore.supportedSchema) only for incompatible changes
UA = {"User-Agent": "Mozilla/5.0 (llpa data builder)"}
PDF_URL = "https://guide.freddiemac.com/euf/assets/pdfs/Exhibit_19.pdf"
API_URL = "https://guide.freddiemac.com/cc/data/getAnswerById/answerId/1001717"

# Credit-score rows, top to bottom, as they appear in every base grid.
SCORE_ROWS = [r"≥ 780", r"≥ 760 & < 780", r"≥ 740 & < 760", r"≥ 720 & < 740", r"≥ 700 & < 720",
              r"≥ 680 & < 700", r"≥ 660 & < 680", r"≥ 640 & < 660", r"< 640"]

# Exhibit label -> key the app reads. Anything else found is kept under its own slug (the app
# ignores keys it doesn't know), so a new attribute row never breaks the build.
ATTRIBUTES = [
    ("Adjustable Rate Mortgage", "arm"),
    ("Condominium Unit", "condo"),
    ("Investment Property", "investment"),
    ("Manufactured Homes", "manufactured"),
    ("Number of Units > 1", "multiUnit"),
    ("Second Home", "secondHome"),
    ("Secondary Financing", "subordinate"),
    ("Super Conforming ARM", "highBalanceARM"),
    ("Super Conforming FRM", "highBalanceFixed"),
    ("Alt FICO", "altFico"),
]
REQUIRED = {"arm", "condo", "investment", "secondHome", "manufactured", "multiUnit",
            "subordinate", "highBalanceFixed", "highBalanceARM"}

PCT = re.compile(r"(\d+\.\d{3})%")

# (heading that identifies the page's grid, key, number of LTV columns the grid has)
GRIDS = [
    ("BASE GRID – PURCHASE", "grid", "purchase", 9),
    ("BASE GRID – NO CASH-OUT", "grid", "limitedCashOut", 9),
    ("BASE GRID – CASH-OUT", "grid", "cashOut", 5),
    ("SPECIAL ATTRIBUTES – PURCHASE", "attrs", "purchase", 9),
    ("SPECIAL ATTRIBUTES – NO CASH-OUT", "attrs", "limitedCashOut", 9),
    ("SPECIAL ATTRIBUTES – CASH-OUT", "attrs", "cashOut", 5),
]


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def get_pdf_bytes():
    try:
        data = fetch(PDF_URL)
        if data[:4] == b"%PDF":
            return data
    except Exception as e:  # fall through to the API
        print(f"direct PDF failed ({e}); trying the content API", file=sys.stderr)
    meta = json.loads(fetch(API_URL))
    content = meta["data"][0]["content"]
    link = content["EXHIBIT/EXHIBIT_PDF"]["filePath"]
    data = fetch(link)
    if data[:4] != b"%PDF":
        raise SystemExit("Exhibit 19 download did not return a PDF")
    return data


def attribute_key(label):
    for prefix, key in ATTRIBUTES:
        if label.startswith(prefix):
            return key
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return "x_" + slug


def parse_grid_page(text, columns):
    rows = []
    for pattern in SCORE_ROWS:
        found = None
        for line in text.split("\n"):
            if re.match(pattern + r"\s+\d", line):
                found = [float(v) for v in PCT.findall(line)]
                break
        if found is None or len(found) != columns:
            raise ValueError(f"grid row {pattern!r}: expected {columns} values, got {found}")
        rows.append(found)
    return rows


def parse_attribute_page(text, columns):
    out = {}
    for line in text.split("\n"):
        values = PCT.findall(line)
        if len(values) != columns:
            continue
        label = line[:line.index(values[0] + "%")].strip()
        if not label or label.startswith(("≥", "<", ">")) or re.match(r"^\d", label):
            continue
        out[attribute_key(label)] = [float(v) for v in values]
    return out


def build(pdf_path):
    import pdfplumber
    result = {"grids": {}, "attributes": {}}
    bulletin = effective = None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            m = re.search(r"Bulletin (\d{4}-[A-Z]+)", text)
            d = re.search(r"(\d{2}/\d{2}/\d{4}) Page E19-", text)
            if m and not bulletin:
                bulletin = m.group(1)
            if d and not effective:
                effective = d.group(1)
            for heading, kind, key, columns in GRIDS:
                if heading in text:
                    if kind == "grid":
                        result["grids"][key] = parse_grid_page(text, columns)
                    else:
                        result["attributes"][key] = parse_attribute_page(text, columns)
    return result, bulletin, effective


def validate(result, bulletin, effective):
    problems = []
    if not bulletin or not effective:
        problems.append("could not read the bulletin / effective date from the page footers")
    for key, columns in (("purchase", 9), ("limitedCashOut", 9), ("cashOut", 5)):
        grid = result["grids"].get(key)
        if not grid or len(grid) != 9 or any(len(r) != columns for r in grid):
            problems.append(f"{key} base grid is not 9 x {columns}")
        attrs = result["attributes"].get(key, {})
        missing = REQUIRED - set(attrs)
        if missing:
            problems.append(f"{key} attributes missing: {sorted(missing)}")
        for name, values in attrs.items():
            if len(values) != columns:
                problems.append(f"{key}.{name} has {len(values)} values, expected {columns}")
    for grid in result["grids"].values():
        for row in grid:
            if any(v < 0 or v > 10 for v in row):
                problems.append("a base grid value is outside 0-10%")
    for attrs in result["attributes"].values():
        for values in attrs.values():
            if any(v < 0 or v > 10 for v in values):
                problems.append("an attribute value is outside 0-10%")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    ap.add_argument("--pdf", help="use a local Exhibit 19 PDF instead of downloading")
    args = ap.parse_args()

    if args.pdf:
        pdf_path = args.pdf
    else:
        pdf_path = os.path.join(args.out, ".exhibit19.pdf")
        with open(pdf_path, "wb") as f:
            f.write(get_pdf_bytes())

    result, bulletin, effective = build(pdf_path)
    if not args.pdf:
        os.remove(pdf_path)
    problems = validate(result, bulletin, effective)
    if problems:
        print("LLPA data NOT published:", *problems, sep="\n  - ", file=sys.stderr)
        sys.exit(1)

    dataset = {
        "schema": SCHEMA,
        "source": f"Freddie Mac Exhibit 19, Guide Bulletin {bulletin}",
        "bulletin": bulletin,
        "effectiveDate": effective,
        "note": ("Fannie Mae's LLPA matrix matches these grids (FHFA-aligned since 2023), but Fannie's "
                 "site blocks automated download, so Fannie is not read directly."),
        "grids": result["grids"],
        "attributes": result["attributes"],
    }
    body = json.dumps(dataset, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    sha = hashlib.sha256(body).hexdigest()
    manifest = {
        "schema": SCHEMA, "file": "LLPAData.json", "bytes": len(body), "sha256": sha,
        "version": sha[:16], "bulletin": bulletin, "effectiveDate": effective,
    }
    with open(os.path.join(args.out, "LLPAData.json"), "wb") as f:
        f.write(body)
    with open(os.path.join(args.out, "llpa-manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote LLPAData.json ({len(body)} bytes), bulletin {bulletin}, effective {effective}")


if __name__ == "__main__":
    main()
