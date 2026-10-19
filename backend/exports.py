"""Excel and PDF report builders (in memory, so they work on serverless hosts)."""
import io
from datetime import datetime
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

EXP_COLS = ["employee_id", "date", "category", "amount", "description", "approval_status"]


def make_excel(docs, s, exc):
    buf = io.BytesIO()
    exp = pd.DataFrame([{k: d.get(k, "") for k in EXP_COLS} for d in docs], columns=EXP_COLS)
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, d in [("Expenses", exp), ("Employees", s["employee"]), ("Categories", s["category"]),
                        ("Monthly", s["monthly"]), ("Exceptions", pd.DataFrame(exc, columns=["row", "employee_id", "type", "detail", "amount"]))]:
            d.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(40, max(12, max(len(str(c.value or "")) for c in col) + 2))
    buf.seek(0)
    return buf


def _table(title, df, cols, st):
    out = [Paragraph(title, st["Heading3"])]
    if df is None or len(df) == 0:
        return out + [Paragraph("No data.", st["Normal"]), Spacer(1, 8)]
    data = [[c.replace("_", " ").title() for c in cols]] + [[(f"{r[c]:,.2f}" if isinstance(r[c], float) else str(r[c])) for c in cols] for _, r in df.iterrows()]
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6f54")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d5ce")), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2ee")])]))
    return out + [t, Spacer(1, 10)]


def make_pdf(user, s, exc):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    st = getSampleStyleSheet()
    els = [Paragraph("Ledgerly - Monthly Expense Report", st["Title"]),
           Paragraph(f"Prepared for {user} on {datetime.now():%Y-%m-%d %H:%M}", st["Normal"]), Spacer(1, 10),
           Paragraph(f"Grand total (excluding rejected): <b>{s['grand_total']:,.2f}</b>", st["Heading2"])]
    els += _table("Category totals", s["category"], ["category", "claims", "total", "share_pct"], st)
    els += _table("Monthly totals", s["monthly"], ["month", "claims", "total"], st)
    els += _table("Employee totals", s["employee"], ["employee_id", "claims", "total", "average"], st)
    els += _table("Exceptions (rejected, unusually large, invalid)", pd.DataFrame(exc, columns=["row", "employee_id", "type", "detail", "amount"]).head(60), ["row", "employee_id", "type", "amount"], st)
    doc.build(els)
    buf.seek(0)
    return buf
