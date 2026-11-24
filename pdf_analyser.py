"""
PDF Analyser - Pune Housing Board "Draft List of Accepted Applications"

Extracts the table (Sr No, Application, Scheme, Category, Applicant Name) from the
PDF, ignores the notice text / page headers / footers, and reports:
  1. Total number of applications for a scheme you enter
  2. List of all schemes with their total applications
  3. Flats available per scheme and per category (from the MHADA lottery
     advertisement, data/mhada_flats_aug2026.csv) next to the applications

Usage:
  python pdf_analyser.py                       # summary + interactive scheme lookup
  python pdf_analyser.py --scheme 936          # one scheme, by scheme code (e.g. 936, 1000-A)
  python pdf_analyser.py --best                # schemes with the highest chance of winning
  python pdf_analyser.py --best --category GP  # best schemes for one category
  python pdf_analyser.py --best --category GP --max-price 20   # ... within a budget (lakh Rs)
  python pdf_analyser.py --refresh             # re-extract from the PDF (ignore cached CSV)
  python pdf_analyser.py --pdf "other.pdf"     # use a different PDF of the same layout

The first run parses the PDF (takes a few minutes) and caches the rows in
output/applications.csv; later runs read the CSV and are instant.
"""

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd
import pymupdf

DEFAULT_PDF = r"C:\Users\dnyaneshwarit\Downloads\10fd6c50-6651-4f38-a05a-8e569c7353ad.pdfreload=385398753.pdf"

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "output"
ROWS_CSV = OUT_DIR / "applications.csv"
SUMMARY_CSV = OUT_DIR / "scheme_summary.csv"

# Column boundaries (x-coordinates in PDF points) measured from the table layout.
# Sr No is right-aligned and ends before x=60; the other columns start at fixed x.
COLUMNS = [            # (name, x_start, x_end)
    ("sr_no", 0, 60),
    ("application", 60, 135),
    ("scheme", 135, 290),
    ("category", 290, 390),
    ("applicant_name", 390, 10_000),
]
TABLE_TOP = 166        # below the "Sr No | Application | Scheme ..." header row
TABLE_BOTTOM = 795     # above the footer (date/time + page number)

SR_NO_RE = re.compile(r"^\d+$")


def column_of(x0):
    for name, start, end in COLUMNS:
        if start <= x0 < end:
            return name
    return None


def join_parts(parts):
    """Join wrapped text lines: 'ANTALYA-NEXT-' + 'TATHAWADE-1RK' -> no space;
    'SG - State' + 'Government Employee' -> with a space."""
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not out:
            out = p
        elif out.endswith("-"):
            out += p
        else:
            out += " " + p
    return out


def parse_page(page):
    """Return a list of row fragments {sr_no, application, scheme, ...} for one page.
    A row is anchored by its Sr No; wrapped lines below it belong to it."""
    words = [w for w in page.get_text("words") if TABLE_TOP <= w[1] < TABLE_BOTTOM]
    if not words:
        return [], 0

    anchors = sorted(
        (w for w in words if column_of(w[0]) == "sr_no" and SR_NO_RE.match(w[4])),
        key=lambda w: w[1],
    )
    if not anchors:
        return [], len(words)

    rows = [{"sr_no": int(a[4]), "_y": a[1], "lines": {c[0]: {} for c in COLUMNS}} for a in anchors]
    orphans = 0
    for x0, y0, x1, y1, text, *_ in words:
        col = column_of(x0)
        if col is None or col == "sr_no":
            continue
        # the row whose anchor is the last one at or above this word (2pt tolerance)
        idx = None
        for i, r in enumerate(rows):
            if r["_y"] <= y0 + 2:
                idx = i
            else:
                break
        if idx is None:
            orphans += 1
            continue
        line_key = round(y0)
        rows[idx]["lines"][col].setdefault(line_key, []).append((x0, text))

    fragments = []
    for r in rows:
        frag = {"sr_no": r["sr_no"]}
        for col, lines in r["lines"].items():
            if col == "sr_no":
                continue
            line_texts = [" ".join(t for _, t in sorted(ws)) for _, ws in sorted(lines.items())]
            frag[col] = join_parts(line_texts)
        fragments.append(frag)
    return fragments, orphans


def merge_fragment(existing, frag):
    """A row split across a page break appears on both pages. Merge the pieces:
    identical text is kept once, different text is appended."""
    for col in ("application", "scheme", "category", "applicant_name"):
        a, b = existing.get(col, ""), frag.get(col, "")
        if not b or a == b:
            continue
        if not a:
            existing[col] = b
        else:
            existing[col] = join_parts([a, b])


def extract(pdf_path):
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    print(f"Parsing {total_pages:,} pages from: {pdf_path}")
    rows = {}
    split_rows = 0
    orphan_words = 0
    start = time.time()
    for pno in range(total_pages):
        frags, orphans = parse_page(doc[pno])
        orphan_words += orphans
        for f in frags:
            if f["sr_no"] in rows:
                merge_fragment(rows[f["sr_no"]], f)
                split_rows += 1
            else:
                rows[f["sr_no"]] = f
        if (pno + 1) % 1000 == 0 or pno + 1 == total_pages:
            print(f"  {pno + 1:>6,}/{total_pages:,} pages  |  {len(rows):,} rows  |  {time.time() - start:.0f}s")
    doc.close()

    df = pd.DataFrame(sorted(rows.values(), key=lambda r: r["sr_no"]))
    df = df[["sr_no", "application", "scheme", "category", "applicant_name"]]
    df["scheme_code"] = df["scheme"].str.split(" - ", n=1).str[0].str.strip()

    validate(df, split_rows, orphan_words)
    OUT_DIR.mkdir(exist_ok=True)
    df.to_csv(ROWS_CSV, index=False, encoding="utf-8-sig")
    print(f"Saved {len(df):,} rows -> {ROWS_CSV}")
    return df


def validate(df, split_rows, orphan_words):
    print("\nValidation")
    n = len(df)
    max_sr = int(df["sr_no"].max())
    missing = sorted(set(range(1, max_sr + 1)) - set(df["sr_no"]))
    print(f"  Rows extracted              : {n:,}")
    print(f"  Highest Sr No               : {max_sr:,}")
    print(f"  Missing Sr Nos              : {len(missing):,}" + (f"  e.g. {missing[:10]}" if missing else ""))
    print(f"  Rows split across pages     : {split_rows:,} (merged)")
    print(f"  Unassigned words            : {orphan_words:,}")
    bad_app = (~df["application"].astype(str).str.fullmatch(r"\d{10}")).sum()
    print(f"  Non-10-digit application nos: {bad_app:,}")
    print(f"  Duplicate application nos   : {df['application'].duplicated().sum():,}")
    for col in ("scheme", "category", "applicant_name"):
        blanks = (df[col].fillna("").str.strip() == "").sum()
        if blanks:
            print(f"  Blank {col:<22}: {blanks:,}")
    print()


def load(pdf_path, refresh):
    if ROWS_CSV.exists() and not refresh:
        df = pd.read_csv(ROWS_CSV, dtype={"application": str, "scheme_code": str})
        print(f"Loaded {len(df):,} rows from cache: {ROWS_CSV}  (use --refresh to re-read the PDF)\n")
        return df
    return extract(pdf_path)




# ---------------------------------------------------------------------------
# Flats available (from the MHADA Pune lottery advertisement, Aug 2026)
# ---------------------------------------------------------------------------
# The advertisement PDF is a scanned image (Marathi), so its table was
# transcribed into data/mhada_flats_aug2026.csv and validated:
#   * the "total flats" column adds up to 4,462 = the total printed in the ad
#   * every row's category split was checked against its total; 8 schemes
#     (937, 939-B, 943, 945, 948-A, 948-B, 949-B, 950) are printed in the ad
#     with a category split that adds up to less than their total - kept as printed.
#   * the flat count of all 168 schemes matches MHADA's official information
#     booklet (appro.mhada.gov.in/guest/helpful-link/Final Booklet.pdf)
#
# Prices (data/mhada_prices_aug2026.csv): the official booklet has no prices and
# the advertisement scan is too blurred to read every digit, so prices come from
# sarkariproperty.com (which publishes MHADA's lottery feed). All 168 prices were
# checked against the price column of the scanned advertisement and agree;
# 885-B and 1017 (merged into one row there) were read from the advertisement.
FLATS_CSV = BASE_DIR / "data" / "mhada_flats_aug2026.csv"
PRICES_CSV = BASE_DIR / "data" / "mhada_prices_aug2026.csv"
CAT_CODES = ["SC", "ST", "NT", "DT", "JR", "FF", "PH", "DF", "EX",
             "MP/MLA/MLC", "ME", "SG", "CG", "AR", "GP"]
SCHEME_REPORT_CSV = OUT_DIR / "scheme_flats_vs_applications.csv"
CATEGORY_REPORT_CSV = OUT_DIR / "scheme_category_flats_vs_applications.csv"
CATEGORY_TOTALS_CSV = OUT_DIR / "category_totals.csv"


def load_flats():
    if not FLATS_CSV.exists():
        print(f"(Flats data not found: {FLATS_CSV} - showing applications only)\n")
        return None
    return pd.read_csv(FLATS_CSV, dtype={"scheme_code": str})


def load_prices():
    if not PRICES_CSV.exists():
        return None
    return pd.read_csv(PRICES_CSV, dtype={"scheme_code": str})


def fmt_price(lo, hi):
    """Price in lakh rupees, e.g. '27.80-27.91 L' or '19.49 L'."""
    if lo is None or pd.isna(lo):
        return "-"
    return f"{lo:.2f} L" if hi == lo or pd.isna(hi) else f"{lo:.2f}-{hi:.2f} L"


def per_flat(apps, flats):
    return round(apps / flats, 1) if flats else None


def chance_pct(flats, apps):
    """Chance of one applicant getting a flat = flats / applications (max 100%)."""
    if not flats:
        return 0.0
    if not apps:
        return 100.0
    return round(min(100.0, flats / apps * 100), 2)


def build_tables(df, flats, prices=None):
    """Returns (schemes, scheme_cat, cat_totals, cat_labels)."""
    df = df.copy()
    df["cat_code"] = df["category"].str.extract(r"^([A-Z/]+)", expand=False)
    cat_labels = df.drop_duplicates("cat_code").set_index("cat_code")["category"].to_dict()
    for c in CAT_CODES:
        cat_labels.setdefault(c, c)

    names = df.drop_duplicates("scheme_code").set_index("scheme_code")["scheme"]
    apps_total = df.groupby("scheme_code").size().rename("total_applications")
    apps_cat = df.groupby(["scheme_code", "cat_code"]).size().rename("applications")

    if flats is not None:
        flats_total = flats.set_index("scheme_code")["total_flats"]
        flats_cat = flats.melt(id_vars="scheme_code", value_vars=CAT_CODES, var_name="cat_code",
                               value_name="flats").set_index(["scheme_code", "cat_code"])["flats"]
    else:
        flats_total = pd.Series(dtype=int)
        flats_cat = pd.Series(dtype=int)

    schemes = pd.concat([flats_total.rename("total_flats"), apps_total], axis=1).fillna(0).astype(int)
    schemes.index.name = "scheme_code"
    schemes.insert(0, "scheme", [names.get(c, f"{c} - (in advertisement, no applications)") for c in schemes.index])
    schemes["applications_per_flat"] = [per_flat(a, f) for a, f in zip(schemes.total_applications, schemes.total_flats)]
    schemes["chance_pct"] = [chance_pct(f, a) for f, a in zip(schemes.total_flats, schemes.total_applications)]
    schemes = schemes.sort_values("total_applications", ascending=False).reset_index()
    price_cols = ["price_min_lakh", "price_max_lakh", "builtup_area_sqm"]
    if prices is not None:
        schemes = schemes.merge(prices[["scheme_code"] + price_cols], on="scheme_code", how="left")
    else:
        for c in price_cols:
            schemes[c] = float("nan")

    scheme_cat = pd.concat([flats_cat.rename("flats"), apps_cat], axis=1).fillna(0).astype(int).reset_index()
    scheme_cat.columns = ["scheme_code", "cat_code", "flats", "applications"]
    scheme_cat["applications_per_flat"] = [per_flat(a, f) for a, f in zip(scheme_cat.applications, scheme_cat.flats)]
    scheme_cat["chance_pct"] = [chance_pct(f, a) for f, a in zip(scheme_cat.flats, scheme_cat.applications)]

    cat_totals = (scheme_cat.groupby("cat_code")[["flats", "applications"]].sum()
                  .reindex(CAT_CODES).fillna(0).astype(int))
    cat_totals["applications_per_flat"] = [per_flat(a, f) for a, f in zip(cat_totals.applications, cat_totals.flats)]
    cat_totals["chance_pct"] = [chance_pct(f, a) for f, a in zip(cat_totals.flats, cat_totals.applications)]
    cat_totals.insert(0, "category", [cat_labels[c] for c in cat_totals.index])
    return schemes, scheme_cat, cat_totals, cat_labels


def save_reports(schemes, scheme_cat, cat_totals, cat_labels):
    OUT_DIR.mkdir(exist_ok=True)
    out = schemes.copy()
    out.index = out.index + 1
    out.to_csv(SUMMARY_CSV, index_label="rank", encoding="utf-8-sig")

    # wide: one row per scheme, flats + applications for each category
    flats_w = scheme_cat.pivot(index="scheme_code", columns="cat_code", values="flats")
    apps_w = scheme_cat.pivot(index="scheme_code", columns="cat_code", values="applications")
    cols = {}
    for c in CAT_CODES:
        cols[f"{c} flats"] = flats_w[c] if c in flats_w else 0
        cols[f"{c} applications"] = apps_w[c] if c in apps_w else 0
    wide = pd.DataFrame(cols).fillna(0).astype(int)
    wide = schemes.set_index("scheme_code").join(wide).reset_index()
    wide.index = wide.index + 1
    wide.to_csv(SCHEME_REPORT_CSV, index_label="rank", encoding="utf-8-sig")

    # long: one row per scheme + category
    long = scheme_cat.merge(schemes[["scheme_code", "scheme"]], on="scheme_code", how="left")
    long["category"] = long["cat_code"].map(cat_labels)
    long = long[(long.flats > 0) | (long.applications > 0)]
    long[["scheme_code", "scheme", "category", "flats", "applications", "applications_per_flat", "chance_pct"]] \
        .sort_values(["scheme_code", "category"]).to_csv(CATEGORY_REPORT_CSV, index=False, encoding="utf-8-sig")

    cat_totals.to_csv(CATEGORY_TOTALS_CSV, index_label="code", encoding="utf-8-sig")


def fmt_ratio(r):
    return "-" if r is None or pd.isna(r) else f"{r:,.1f}"


def print_summary(schemes, cat_totals, has_flats):
    print(f"Schemes: flats available vs applications  ({len(schemes)} schemes)")
    width = max(schemes["scheme"].str.len().max(), 6)
    line = "-" * (width + 75)
    print(line)
    print(f"{'#':>4}  {'Scheme':<{width}}  {'Price (Rs)':>16}  {'Flats':>6}  {'Applications':>12}"
          f"  {'Apps/flat':>10}  {'Chance':>8}")
    for i, r in schemes.iterrows():
        print(f"{i + 1:>4}  {r['scheme']:<{width}}  {fmt_price(r['price_min_lakh'], r['price_max_lakh']):>16}"
              f"  {r['total_flats']:>6,}  {r['total_applications']:>12,}"
              f"  {fmt_ratio(r['applications_per_flat']):>10}  {r['chance_pct']:>7.2f}%")
    print(line)
    tf, ta = schemes.total_flats.sum(), schemes.total_applications.sum()
    print(f"{'':>4}  {'TOTAL':<{width}}  {'':>16}  {tf:>6,}  {ta:>12,}  {fmt_ratio(per_flat(ta, tf)):>10}\n")

    print("Category totals (all schemes)")
    print(f"  {'Category':<36} {'Flats':>6}  {'Applications':>12}  {'Apps/flat':>10}")
    for _, r in cat_totals.iterrows():
        print(f"  {r['category']:<36} {r['flats']:>6,}  {r['applications']:>12,}"
              f"  {fmt_ratio(r['applications_per_flat']):>10}")
    print()
    print("Saved:")
    for p in (SUMMARY_CSV, SCHEME_REPORT_CSV, CATEGORY_REPORT_CSV, CATEGORY_TOTALS_CSV):
        print(f"  {p}")
    if not has_flats:
        print("  (flats columns are 0 - flats data file missing)")
    print()


def find_schemes(schemes, query):
    """Match by scheme code only, e.g. '936' or '1000-A'.
    A base code that only exists with suffixes ('935' -> 935-A, 935-B) lists those."""
    q = query.strip().upper()
    codes = schemes["scheme_code"].str.upper()
    exact = schemes[codes == q]
    if not exact.empty:
        return exact
    return schemes[codes.str.split("-", n=1).str[0] == q]


def report_scheme(schemes, scheme_cat, cat_labels, query):
    m = find_schemes(schemes, query)
    if m.empty:
        print(f"No scheme with code '{query}'.\n")
        return
    if len(m) > 1:
        print(f"'{query}' matches {len(m)} schemes:")
        print(f"  {'Scheme':<60} {'Price (Rs)':>16} {'Flats':>6} {'Applications':>13}")
        for _, r in m.sort_values("scheme_code").iterrows():
            print(f"  {r['scheme']:<60} {fmt_price(r['price_min_lakh'], r['price_max_lakh']):>16}"
                  f" {r['total_flats']:>6,} {r['total_applications']:>13,}")
        print(f"  {'TOTAL of matched schemes':<60} {'':>16} {m.total_flats.sum():>6,} {m.total_applications.sum():>13,}")
        print(f"Enter the full code (e.g. {m.scheme_code.iloc[0]}) to see a single scheme.\n")
        return

    r = m.iloc[0]
    rows = scheme_cat[(scheme_cat.scheme_code == r["scheme_code"])
                      & ((scheme_cat.flats > 0) | (scheme_cat.applications > 0))]
    rows = rows.set_index("cat_code").reindex([c for c in CAT_CODES if c in set(rows.cat_code)])
    print(f"Scheme             : {r['scheme']}")
    price = fmt_price(r["price_min_lakh"], r["price_max_lakh"])
    area = f"  (built-up area ~{r['builtup_area_sqm']:.1f} sq m)" if pd.notna(r["builtup_area_sqm"]) else ""
    print(f"Price              : Rs {price}{area}" if price != "-" else "Price              : -")
    print(f"Flats available    : {r['total_flats']:,}")
    ratio = f"  ({fmt_ratio(r['applications_per_flat'])} applications per flat)" if r["total_flats"] else ""
    print(f"Total applications : {r['total_applications']:,}{ratio}")
    print(f"Chance of winning  : {r['chance_pct']:.2f}%  (overall, flats / applications)")
    print(f"\n  {'Category':<36} {'Flats':>6}  {'Applications':>12}  {'Apps/flat':>10}  {'Chance':>8}")
    for code, c in rows.iterrows():
        print(f"  {cat_labels.get(code, code):<36} {c['flats']:>6,}  {c['applications']:>12,}"
              f"  {fmt_ratio(c['applications_per_flat']):>10}  {c['chance_pct']:>7.2f}%")
    print(f"  {'TOTAL':<36} {rows.flats.sum():>6,}  {rows.applications.sum():>12,}")
    if r["total_flats"] and rows.flats.sum() != r["total_flats"]:
        print(f"  Note: in the advertisement the category split for this scheme adds up to "
              f"{rows.flats.sum()}, but its printed total is {r['total_flats']}.")
    print()


BEST_CSV = OUT_DIR / "best_chances.csv"


def best_chances(schemes, scheme_cat, cat_labels, top=10, category=None, max_price=None):
    """Schemes where one application has the highest chance of winning.
    Overall = all flats / all applications; per category = that category's
    reserved flats / applications in that category (what an applicant in
    that category actually competes for). max_price (lakh) keeps only schemes
    whose lowest price is within budget."""
    info = schemes.set_index("scheme_code")[["scheme", "price_min_lakh", "price_max_lakh"]]
    budget = f" (price up to Rs {max_price:g} L)" if max_price else ""

    def in_budget(codes):
        if not max_price:
            return pd.Series(True, index=codes.index)
        return codes.map(info["price_min_lakh"]) <= max_price

    def show(rows, title, flats_col, apps_col):
        print(title + budget)
        print(f"  {'#':>3}  {'Scheme':<62} {'Price (Rs)':>16} {'Flats':>6} {'Applications':>13} {'Chance':>8}")
        for i, (_, r) in enumerate(rows.iterrows(), 1):
            s = info.loc[r["scheme_code"]]
            print(f"  {i:>3}  {s['scheme']:<62} {fmt_price(s['price_min_lakh'], s['price_max_lakh']):>16}"
                  f" {r[flats_col]:>6,} {r[apps_col]:>13,} {r['chance_pct']:>7.2f}%")
        if rows.empty:
            print("  (no scheme in this budget)")
        print()

    # sort by chance, then by number of flats (more flats = more robust)
    if category is None:
        ranked = schemes[(schemes.total_flats > 0) & in_budget(schemes.scheme_code)]
        ranked = ranked.sort_values(["chance_pct", "total_flats"], ascending=False)
        show(ranked.head(top), f"Top {top} schemes - highest overall chance (all categories together)",
             "total_flats", "total_applications")
        cats = ["GP"] + [c for c in CAT_CODES if c != "GP"]
    else:
        cats = [category.upper()]

    best_rows = []
    for c in cats:
        sub = scheme_cat[(scheme_cat.cat_code == c) & (scheme_cat.flats > 0)]
        if sub.empty:
            print(f"No flats reserved for category '{c}'.\n")
            continue
        sub = sub[in_budget(sub.scheme_code)]
        ranked = sub.sort_values(["chance_pct", "flats"], ascending=False)
        n = top if category is not None or c == "GP" else 5
        show(ranked.head(n), f"Best schemes for {cat_labels.get(c, c)}", "flats", "applications")
        best_rows.append(ranked.assign(category=cat_labels.get(c, c)).join(info, on="scheme_code"))

    if category is None and best_rows:
        out = pd.concat(best_rows)[["category", "scheme_code", "scheme", "price_min_lakh", "price_max_lakh",
                                    "flats", "applications", "chance_pct"]]
        out.to_csv(BEST_CSV, index=False, encoding="utf-8-sig")
        print(f"All schemes ranked by chance, per category -> {BEST_CSV}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", default=DEFAULT_PDF, help="path to the applications PDF")
    ap.add_argument("--scheme", help="scheme code to look up, e.g. 936 or 1000-A")
    ap.add_argument("--best", action="store_true", help="schemes with the highest chance of winning")
    ap.add_argument("--category", help="with --best: only this category, e.g. GP, SC, PH")
    ap.add_argument("--top", type=int, default=10, help="with --best: how many schemes to list (default 10)")
    ap.add_argument("--max-price", type=float, help="with --best: budget in lakh rupees, e.g. 25")
    ap.add_argument("--refresh", action="store_true", help="re-extract from the PDF")
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    df = load(args.pdf, args.refresh)
    flats = load_flats()
    schemes, scheme_cat, cat_totals, cat_labels = build_tables(df, flats, load_prices())
    save_reports(schemes, scheme_cat, cat_totals, cat_labels)

    if args.best:
        best_chances(schemes, scheme_cat, cat_labels, args.top, args.category, args.max_price)
        return
    if args.scheme:
        report_scheme(schemes, scheme_cat, cat_labels, args.scheme)
        return

    print_summary(schemes, cat_totals, flats is not None)
    print("Enter a scheme code (e.g. 936) to see its flats and applications by category (blank to quit).")
    while True:
        try:
            q = input("Scheme > ").strip()
        except EOFError:
            break
        if not q:
            break
        report_scheme(schemes, scheme_cat, cat_labels, q)


if __name__ == "__main__":
    main()
