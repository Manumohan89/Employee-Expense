"""Core engine: load -> validate -> clean -> categorize -> total -> report."""
import json, logging, math, os
from datetime import datetime
import pandas as pd

REQUIRED = ["employee_id", "date", "category", "amount", "description", "approval_status"]
CATEGORIES = {"travel", "food", "office", "software", "training", "accommodation", "other"}
STATUSES = {"approved", "pending", "rejected"}
LARGE_LIMIT = 1000.0  # fixed threshold for "unusually large"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("expenses")


def load_file(path, ext=None):
    """Read CSV/XLSX (path or file-like) into a DataFrame with normalised column names."""
    ext = ext or os.path.splitext(path)[1].lower()
    try:
        df = pd.read_csv(path, dtype=str) if ext == ".csv" else pd.read_excel(path, dtype=str)
    except FileNotFoundError:
        raise ValueError(f"File not found: {path}")
    except Exception as e:
        raise ValueError(f"Could not read file: {e}")
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    return df[REQUIRED].copy()


def validate(df):
    """Return (clean_df, exceptions list). Each exception is a dict."""
    exceptions, keep = [], []
    seen = set()
    for i, r in df.iterrows():
        row_no = i + 2  # header is row 1
        problems = []
        emp = (r["employee_id"] or "").strip() if isinstance(r["employee_id"], str) else ""
        if not emp:
            problems.append("Missing employee ID")
        date = pd.to_datetime(r["date"], errors="coerce")
        if pd.isna(date):
            problems.append("Invalid date")
        elif date > datetime.now():
            problems.append("Date is in the future")
        amount = pd.to_numeric(str(r["amount"]).replace(",", "").replace("$", ""), errors="coerce")
        if pd.isna(amount) or not math.isfinite(float(amount)):
            problems.append("Amount must be a finite number")
        elif amount <= 0:
            problems.append("Amount must be greater than zero")
        cat = str(r["category"]).strip().lower() if isinstance(r["category"], str) else ""
        if cat not in CATEGORIES:
            problems.append(f"Unknown category '{r['category']}'")
        status = str(r["approval_status"]).strip().lower() if isinstance(r["approval_status"], str) else ""
        if status not in STATUSES:
            problems.append(f"Unknown approval status '{r['approval_status']}'")
        key = (emp, str(date), cat, amount)
        if not problems and key in seen:
            problems.append("Duplicate record")
        seen.add(key)
        if problems:
            exceptions.append({"row": row_no, "employee_id": emp, "type": "Invalid data",
                               "detail": "; ".join(problems), "amount": r["amount"]})
            log.warning("Row %s rejected: %s", row_no, problems)
            continue
        keep.append({"employee_id": emp.upper(), "date": date.date(), "month": date.strftime("%Y-%m"),
                     "category": cat.title(), "amount": round(float(amount), 2),
                     "description": (r["description"] or "").strip() if isinstance(r["description"], str) else "",
                     "approval_status": status.title(), "source_row": row_no})
    return pd.DataFrame(keep), exceptions


def flag_exceptions(clean, exceptions):
    """Flag rejected claims and unusually large amounts (per-category outlier test when 5+ samples)."""
    if clean.empty:
        return clean, exceptions
    gm, gs = clean["amount"].mean(), clean["amount"].std(ddof=0)
    glimit = min(LARGE_LIMIT, gm + 2 * gs) if gs else LARGE_LIMIT
    limits = {}
    for cat, grp in clean.groupby("category"):
        sd = grp["amount"].std(ddof=0)
        limits[cat] = min(LARGE_LIMIT, grp["amount"].mean() + 2 * sd) if len(grp) >= 5 and sd else glimit
    flags = []
    for _, r in clean.iterrows():
        f, limit = [], limits.get(r["category"], glimit)
        if r["approval_status"] == "Rejected":
            f.append("Rejected")
            exceptions.append({"row": r["source_row"], "employee_id": r["employee_id"], "type": "Rejected expense",
                               "detail": f"{r['category']} - {r['description']}", "amount": r["amount"]})
        if r["amount"] >= limit:
            f.append("Unusually large")
            exceptions.append({"row": r["source_row"], "employee_id": r["employee_id"], "type": "Unusually large",
                               "detail": f"{r['category']} - {r['description']} (limit {limit:,.0f})", "amount": r["amount"]})
        flags.append(", ".join(f))
    clean = clean.copy()
    clean["flags"] = flags
    return clean, exceptions


def summarize(clean):
    """Totals on non-rejected spend, plus per-status counts."""
    valid = clean[clean["approval_status"] != "Rejected"] if not clean.empty else clean
    if valid.empty:
        empty = pd.DataFrame()
        return {"employee": empty, "category": empty, "monthly": empty, "grand_total": 0.0}
    emp = valid.groupby("employee_id")["amount"].agg(total="sum", claims="count", average="mean").round(2).reset_index().sort_values("total", ascending=False)
    cat = valid.groupby("category")["amount"].agg(total="sum", claims="count").round(2).reset_index().sort_values("total", ascending=False)
    cat["share_pct"] = (cat["total"] / cat["total"].sum() * 100).round(1)
    mon = valid.groupby("month")["amount"].agg(total="sum", claims="count").round(2).reset_index()
    return {"employee": emp, "category": cat, "monthly": mon, "grand_total": round(float(valid["amount"].sum()), 2)}


def write_report(path, s, total_rows, clean, exceptions):
    lines = ["MONTHLY EXPENSE SUMMARY REPORT", "=" * 40,
             f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
             f"Records read: {total_rows} | Valid: {len(clean)} | Exceptions logged: {len(exceptions)}",
             f"Grand total (excluding rejected): {s['grand_total']:,.2f}", "", "CATEGORY TOTALS"]
    for _, r in s["category"].iterrows():
        lines.append(f"  {r['category']:<15}{r['total']:>12,.2f}  ({r['share_pct']}%)")
    lines += ["", "MONTHLY TOTALS"] + [f"  {r['month']:<15}{r['total']:>12,.2f}" for _, r in s["monthly"].iterrows()]
    lines += ["", "EMPLOYEE TOTALS"] + [f"  {r['employee_id']:<15}{r['total']:>12,.2f}  ({r['claims']} claims)" for _, r in s["employee"].iterrows()]
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def process(input_path, out_dir):
    """Run the full pipeline; write 4 deliverables + Excel workbook; return result dict."""
    os.makedirs(out_dir, exist_ok=True)
    raw = load_file(input_path)
    clean, exc = validate(raw)
    clean, exc = flag_exceptions(clean, exc)
    s = summarize(clean)
    exc_df = pd.DataFrame(exc, columns=["row", "employee_id", "type", "detail", "amount"])
    if not clean.empty:
        clean.drop(columns=["source_row"]).to_csv(os.path.join(out_dir, "cleaned_expenses.csv"), index=False)
    else:
        pd.DataFrame().to_csv(os.path.join(out_dir, "cleaned_expenses.csv"), index=False)
    exc_df.to_csv(os.path.join(out_dir, "exception_log.csv"), index=False)
    s["category"].to_csv(os.path.join(out_dir, "category_totals.csv"), index=False)
    write_report(os.path.join(out_dir, "summary_report.txt"), s, len(raw), clean, exc)
    with pd.ExcelWriter(os.path.join(out_dir, "expense_report.xlsx")) as xw:
        for name, d in [("Cleaned", clean), ("Employees", s["employee"]), ("Categories", s["category"]),
                        ("Monthly", s["monthly"]), ("Exceptions", exc_df)]:
            d.to_excel(xw, sheet_name=name, index=False)
    res = {"rows": len(raw), "valid": len(clean), "exceptions": len(exc), "grand_total": s["grand_total"],
           "category": s["category"].to_dict("records"), "monthly": s["monthly"].to_dict("records"),
           "employee": s["employee"].to_dict("records"), "exception_rows": exc_df.to_dict("records"),
           "flagged": clean[clean["flags"] != ""].drop(columns=["source_row"]).astype(str).to_dict("records") if not clean.empty else [],
           "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    json.dump(res, open(os.path.join(out_dir, "result.json"), "w"), default=str)
    log.info("Processed %s rows, %s exceptions", len(raw), len(exc))
    return res


if __name__ == "__main__":  # CLI: python expense_processor.py file.csv [out_dir]
    import sys
    r = process(sys.argv[1] if len(sys.argv) > 1 else "sample_data/expenses.csv", sys.argv[2] if len(sys.argv) > 2 else "outputs/cli")
    print(open(os.path.join(sys.argv[2] if len(sys.argv) > 2 else "outputs/cli", "summary_report.txt")).read())
