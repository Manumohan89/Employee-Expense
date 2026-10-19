"""Ledgerly REST API v2: roles, approvals, receipts, budgets, audit log, reset, cron report."""
import hashlib, io, json, math, os, re, secrets, smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
import jwt
import numpy as np
import pandas as pd
from bson import ObjectId
from bson.binary import Binary
from bson.errors import InvalidId
from flask import Flask, request, jsonify, g, send_file
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import expense_processor as ep
import exports
from db import get_db
from openapi import spec

DEFAULT_KEY = "dev-only-change-me-use-a-local-random-secret"
SECRET = os.environ.get("SECRET_KEY", DEFAULT_KEY)
if SECRET == DEFAULT_KEY and (os.environ.get("RENDER") or os.environ.get("VERCEL") or os.environ.get("PRODUCTION")):
    raise RuntimeError("Set a strong SECRET_KEY environment variable before running in production.")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
CORS(app, origins=origins, allow_headers=["Content-Type", "Authorization"], expose_headers=["Content-Disposition"])
COLS = ["employee_id", "date", "month", "category", "amount", "description", "approval_status", "source_row"]
ROLES = ("employee", "manager", "admin")
RECEIPT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "pdf": "application/pdf"}


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def err(msg, code=400):
    return jsonify(error=msg), code


def auth(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            try:
                p = jwt.decode(request.headers.get("Authorization", "").split(" ", 1)[1], SECRET, algorithms=["HS256"])
                u = get_db().users.find_one({"_id": ObjectId(p["uid"])})
            except Exception:
                u = None
            if not u:
                return err("Please sign in again.", 401)
            g.uid, g.name, g.role, g.emp = str(u["_id"]), u["name"], u["role"], u["emp_id"]
            if roles and g.role not in roles:
                return err("You do not have permission to do that.", 403)
            return f(*a, **k)
        return w
    return deco


def audit(action, target="", detail=""):
    get_db().audit.insert_one({"ts": now(), "user": g.get("name", "system"), "role": g.get("role", ""), "action": action, "target": target, "detail": detail})


def priv():
    return g.role in ("manager", "admin")


def month_ok(m):
    return m if m and re.fullmatch(r"\d{4}-\d{2}", m) else None


def scope_q():
    return {} if priv() else {"employee_id": g.emp}


def fetch(q):
    return list(get_db().expenses.find(q).sort("date", -1))


def docs_for(month=None):
    q, m = scope_q(), month_ok(month)
    if m:
        q["date"] = {"$regex": "^" + m}
    return fetch(q)


def frame(docs):
    return pd.DataFrame([{"employee_id": d["employee_id"], "date": pd.to_datetime(d["date"]).date(), "month": d["date"][:7], "category": d["category"],
                          "amount": float(d["amount"]), "description": d.get("description", ""), "approval_status": d["approval_status"], "source_row": i}
                         for i, d in enumerate(docs)], columns=COLS)


def recs(df):
    return json.loads(df.to_json(orient="records")) if len(df) else []


def analyse(docs):
    clean, exc = ep.flag_exceptions(frame(docs), [])
    return clean, exc, ep.summarize(clean)


def jclean(x):
    return json.loads(json.dumps(x, default=str))


def all_exc(exc):
    saved = list(get_db().exceptions.find({}, {"_id": 0, "user_id": 0})) if priv() else []
    return jclean(saved + exc)


def ser(d):
    return {"id": str(d["_id"]), "employee_id": d["employee_id"], "date": d["date"], "category": d["category"], "amount": d["amount"],
            "description": d.get("description", ""), "approval_status": d["approval_status"], "has_receipt": bool(d.get("has_receipt")),
            "review_comment": (d.get("review") or {}).get("comment", "")}


def doc_from(data):
    clean, exc = ep.validate(pd.DataFrame([{k: str(data.get(k, "")).strip() for k in ep.REQUIRED}]))
    if exc:
        return None, exc[0]["detail"]
    r = clean.iloc[0]
    return {"employee_id": r["employee_id"], "date": r["date"].isoformat(), "category": r["category"], "amount": float(r["amount"]),
            "description": r["description"], "approval_status": r["approval_status"]}, None


def get_exp(eid):
    try:
        return get_db().expenses.find_one({"_id": ObjectId(eid), **scope_q()})
    except InvalidId:
        return None


def can_modify(d):
    return priv() or d["approval_status"] == "Pending"


def next_month(m):
    y, mo = map(int, m.split("-"))
    return f"{y + mo // 12}-{mo % 12 + 1:02d}"


def trend_info(mon):
    t = [m["total"] for m in mon]
    out = {"mom_pct": None, "forecast": None, "forecast_month": None}
    if len(t) >= 2 and t[-2]:
        out["mom_pct"] = round((t[-1] - t[-2]) / t[-2] * 100, 1)
    if len(t) >= 3:
        slope, icpt = np.polyfit(range(len(t)), t, 1)
        out["forecast"] = round(max(0.0, float(slope * len(t) + icpt)), 2)
        out["forecast_month"] = next_month(mon[-1]["month"])
    return out


def budget_status(docs, month):
    spent = {}
    for d in docs:
        if d["date"].startswith(month) and d["approval_status"] != "Rejected":
            spent[d["category"]] = spent.get(d["category"], 0) + d["amount"]
    rows = []
    for b in get_db().budgets.find({}, {"_id": 0}).sort("category", 1):
        s = round(spent.get(b["category"], 0), 2)
        pct = round(s / b["limit"] * 100, 1) if b["limit"] else 0
        rows.append({"category": b["category"], "limit": b["limit"], "spent": s, "pct": pct, "level": "over" if pct > 100 else "warn" if pct >= 80 else "ok"})
    return rows


def send_mail(to, subject, body, attach=None):
    host = os.environ.get("SMTP_HOST")
    if not host:
        app.logger.warning("MAIL (SMTP not configured) to=%s subject=%s\n%s", to, subject, body)
        return False
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = os.environ.get("MAIL_FROM", os.environ.get("SMTP_USER", "noreply@ledgerly.app")), to, subject
    m.set_content(body)
    if attach:
        m.add_attachment(attach[0], maintype="application", subtype="pdf", filename=attach[1])
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(m)
    return True


# ---------- public ----------
@app.get("/api/health")
def health():
    try:
        get_db().command("ping")
    except Exception:
        app.logger.exception("Database health check failed.")
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", database="ok")


@app.get("/api/openapi.json")
def openapi():
    return jsonify(spec())


@app.post("/api/register")
def register():
    d, db = request.get_json(silent=True) or {}, get_db()
    name, email, pw = str(d.get("name", "")).strip(), str(d.get("email", "")).strip().lower(), str(d.get("password", ""))
    if not name or "@" not in email or len(pw) < 6:
        return err("Enter your name, a valid email and a password of at least 6 characters.")
    if "confirm_password" in d and pw != str(d["confirm_password"]):
        return err("Passwords do not match.")
    if db.users.find_one({"email": email}):
        return err("That email is already registered. Sign in instead.", 409)
    n = db.users.count_documents({})
    emp = f"EMP{101 + n}"
    while db.users.find_one({"emp_id": emp}):
        n += 1
        emp = f"EMP{101 + n}"
    db.users.insert_one({"name": name, "email": email, "pw": generate_password_hash(pw), "role": "admin" if n == 0 else "employee", "emp_id": emp, "created": now()})
    return jsonify(message="Account created." + (" You are the admin." if n == 0 else "")), 201


@app.post("/api/login")
def login():
    d, db = request.get_json(silent=True) or {}, get_db()
    email = str(d.get("email", "")).strip().lower()
    if db.attempts.count_documents({"key": email, "ts": {"$gte": now() - timedelta(minutes=15)}}) >= 5:
        return err("Too many failed attempts. Try again in 15 minutes.", 429)
    u = db.users.find_one({"email": email})
    if not u or not check_password_hash(u["pw"], str(d.get("password", ""))):
        db.attempts.insert_one({"key": email, "ts": now()})
        return err("Email or password is incorrect.", 401)
    db.attempts.delete_many({"key": email})
    t = jwt.encode({"uid": str(u["_id"]), "exp": datetime.now(timezone.utc) + timedelta(days=7)}, SECRET, algorithm="HS256")
    return jsonify(token=t, name=u["name"], role=u["role"], emp_id=u["emp_id"])


@app.post("/api/forgot")
def forgot():
    email, db = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower(), get_db()
    if db.users.find_one({"email": email}):
        tok = secrets.token_urlsafe(32)
        db.resets.delete_many({"email": email})
        db.resets.insert_one({"email": email, "hash": hashlib.sha256(tok.encode()).hexdigest(), "exp": now() + timedelta(hours=1)})
        link = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/") + "/reset.html?token=" + tok
        send_mail(email, "Reset your Ledgerly password", f"Use this link within 1 hour to set a new password:\n{link}")
    return jsonify(message="If that email is registered, a reset link has been sent.")


@app.post("/api/reset")
def reset():
    d, db = request.get_json(silent=True) or {}, get_db()
    pw = str(d.get("password", ""))
    r = db.resets.find_one({"hash": hashlib.sha256(str(d.get("token", "")).encode()).hexdigest(), "exp": {"$gte": now()}})
    if not r or len(pw) < 6:
        return err("Reset link is invalid or expired, or the password is shorter than 6 characters.")
    db.users.update_one({"email": r["email"]}, {"$set": {"pw": generate_password_hash(pw)}})
    db.resets.delete_many({"email": r["email"]})
    db.attempts.delete_many({"key": r["email"]})
    audit("password_reset", r["email"])
    return jsonify(message="Password updated. You can sign in now.")


@app.post("/api/cron/monthly-report")
def cron():
    sec = os.environ.get("CRON_SECRET")
    if not sec or request.headers.get("X-Cron-Secret") != sec:
        return err("Unauthorized", 401)
    prev = (now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    _, exc, s = analyse(fetch({"date": {"$regex": "^" + prev}}))
    pdf = exports.make_pdf(f"All teams - {prev}", s, jclean(exc)).read()
    sent = sum(send_mail(u["email"], f"Ledgerly expense report {prev}", f"Attached is the expense report for {prev}.", (pdf, f"expense_report_{prev}.pdf"))
               for u in get_db().users.find({"role": {"$in": ["manager", "admin"]}}))
    return jsonify(month=prev, emails_sent=sent, smtp_configured=bool(os.environ.get("SMTP_HOST")))


# ---------- authenticated ----------
@app.get("/api/me")
@auth()
def me():
    return jsonify(name=g.name, role=g.role, emp_id=g.emp)


@app.get("/api/dashboard")
@auth()
def dashboard():
    month = month_ok(request.args.get("month"))
    all_docs = docs_for()
    docs = [d for d in all_docs if not month or d["date"].startswith(month)]
    _, exc, s = analyse(docs)
    mon = recs(analyse(all_docs)[2]["monthly"])
    status_counts = {status: sum(d["approval_status"] == status for d in docs) for status in ("Pending", "Approved", "Rejected")}
    return jsonify(name=g.name, role=g.role, total=s["grand_total"], n=len(docs), n_exc=len(all_exc(exc)), pending=status_counts["Pending"], status_counts=status_counts, cat=recs(s["category"]), mon=mon,
                   emp=recs(s["employee"]), recent=[ser(d) for d in docs[:6]], trend=trend_info(mon), months=sorted({d["date"][:7] for d in all_docs}, reverse=True),
                   budgets=budget_status(all_docs, month or now().strftime("%Y-%m")))


@app.get("/api/expenses")
@auth()
def list_expenses():
    all_docs = docs_for()
    month = month_ok(request.args.get("month"))
    docs = [d for d in all_docs if not month or d["date"].startswith(month)]
    clean, _ = ep.flag_exceptions(frame(docs), [])
    flags = {str(docs[int(r["source_row"])]["_id"]): r["flags"] for _, r in clean.iterrows()} if len(clean) else {}
    q, st, cat = request.args.get("q", "").lower(), request.args.get("status", ""), request.args.get("category", "")
    rows = [{**ser(d), "flags": flags.get(str(d["_id"]), "")} for d in docs
            if (not q or q in (d["employee_id"] + d.get("description", "")).lower()) and (not st or d["approval_status"] == st) and (not cat or d["category"] == cat)]
    try:
        pp = min(max(int(request.args.get("per_page", 15) or 15), 1), 100)
        pg = max(int(request.args.get("page", 1) or 1), 1)
    except ValueError:
        return err("Page and per_page must be whole numbers.")
    return jsonify(rows=rows[(pg - 1) * pp: pg * pp], total=len(rows), page=pg, pages=max(1, -(-len(rows) // pp)),
                   categories=sorted(c.title() for c in ep.CATEGORIES), months=sorted({d["date"][:7] for d in all_docs}, reverse=True))


@app.post("/api/expenses")
@auth()
def add_expense():
    data = request.get_json(silent=True) or {}
    if not priv():
        data.update(employee_id=g.emp, approval_status="Pending")
    d, e = doc_from(data)
    if e:
        return err(e)
    d.update(user_id=g.uid, created=now())
    r = get_db().expenses.insert_one(d)
    audit("expense_add", str(r.inserted_id), f"{d['employee_id']} {d['category']} {d['amount']}")
    return jsonify(message="Expense added.", id=str(r.inserted_id)), 201


@app.get("/api/expenses/<eid>")
@auth()
def get_expense(eid):
    d = get_exp(eid)
    return jsonify(ser(d)) if d else err("Expense not found.", 404)


@app.put("/api/expenses/<eid>")
@auth()
def edit_expense(eid):
    old = get_exp(eid)
    if not old:
        return err("Expense not found.", 404)
    if not can_modify(old):
        return err("Only pending expenses can be changed by employees.", 403)
    data = request.get_json(silent=True) or {}
    if not priv():
        data.update(employee_id=g.emp, approval_status="Pending")
    d, e = doc_from(data)
    if e:
        return err(e)
    get_db().expenses.update_one({"_id": old["_id"]}, {"$set": d})
    audit("expense_edit", eid, f"{d['category']} {d['amount']} {d['approval_status']}")
    return jsonify(message="Expense updated.")


@app.delete("/api/expenses/<eid>")
@auth()
def delete_expense(eid):
    d = get_exp(eid)
    if not d:
        return err("Expense not found.", 404)
    if not can_modify(d):
        return err("Only pending expenses can be deleted by employees.", 403)
    get_db().expenses.delete_one({"_id": d["_id"]})
    get_db().receipts.delete_many({"expense_id": eid})
    audit("expense_delete", eid, f"{d['employee_id']} {d['amount']}")
    return jsonify(message="Expense deleted.")


@app.delete("/api/expenses")
@auth("admin")
def clear_all():
    get_db().expenses.delete_many({})
    get_db().exceptions.delete_many({})
    get_db().receipts.delete_many({})
    audit("clear_all")
    return jsonify(message="All expenses, receipts and logs cleared.")


@app.post("/api/expenses/<eid>/review")
@auth("manager", "admin")
def review(eid):
    d, body = get_exp(eid), request.get_json(silent=True) or {}
    if not d:
        return err("Expense not found.", 404)
    if body.get("decision") not in ("Approved", "Rejected"):
        return err("Decision must be Approved or Rejected.")
    if d["employee_id"] == g.emp and g.role != "admin":
        return err("You cannot review your own expense.", 403)
    c = str(body.get("comment", ""))[:300]
    get_db().expenses.update_one({"_id": d["_id"]}, {"$set": {"approval_status": body["decision"], "review": {"by": g.name, "comment": c, "at": now()}}})
    audit("expense_" + body["decision"].lower(), eid, c)
    return jsonify(message=f"Expense {body['decision'].lower()}.")


@app.post("/api/expenses/<eid>/receipt")
@auth()
def upload_receipt(eid):
    d, f = get_exp(eid), request.files.get("file")
    if not d:
        return err("Expense not found.", 404)
    ext = (f.filename.rsplit(".", 1)[-1].lower() if f and "." in f.filename else "")
    data = f.read() if f else b""
    if ext not in RECEIPT_TYPES or not data:
        return err("Receipt must be a PNG, JPG, WEBP or PDF file.")
    if len(data) > 1_500_000:
        return err("Receipt is larger than 1.5 MB.")
    get_db().receipts.replace_one({"expense_id": eid}, {"expense_id": eid, "type": RECEIPT_TYPES[ext], "name": f.filename, "data": Binary(data)}, upsert=True)
    get_db().expenses.update_one({"_id": d["_id"]}, {"$set": {"has_receipt": True}})
    audit("receipt_upload", eid, f.filename)
    return jsonify(message="Receipt attached.")


@app.get("/api/expenses/<eid>/receipt")
@auth()
def get_receipt(eid):
    r = get_exp(eid) and get_db().receipts.find_one({"expense_id": eid})
    return send_file(io.BytesIO(bytes(r["data"])), mimetype=r["type"], download_name=r["name"]) if r else err("No receipt attached.", 404)


@app.post("/api/upload")
@auth("manager", "admin")
def upload():
    f, db = request.files.get("file"), get_db()
    if not f or not f.filename.lower().endswith((".csv", ".xlsx")):
        return err("Choose a .csv or .xlsx file.")
    try:
        raw = ep.load_file(io.BytesIO(f.read()), os.path.splitext(f.filename)[1].lower())
    except ValueError as e:
        return err(str(e))
    clean, exc = ep.validate(raw)
    if request.form.get("replace"):
        if g.role != "admin":
            return err("Only admins can replace existing data.", 403)
        db.expenses.delete_many({})
        db.receipts.delete_many({})
    db.exceptions.delete_many({})
    if len(clean):
        db.expenses.insert_many([{"user_id": g.uid, "employee_id": r["employee_id"], "date": r["date"].isoformat(), "category": r["category"], "amount": r["amount"],
                                  "description": r["description"], "approval_status": r["approval_status"], "created": now()} for _, r in clean.iterrows()])
    if exc:
        db.exceptions.insert_many([{**{k: (v if isinstance(v, (str, int)) else str(v)) for k, v in x.items()}, "user_id": g.uid} for x in exc])
    audit("import", f.filename, f"{len(clean)} valid, {len(exc)} exceptions")
    return jsonify(message=f"Imported {len(clean)} valid rows. {len(exc)} rows were logged as exceptions.")


@app.get("/api/sample")
def sample():
    return send_file(os.path.join(os.path.dirname(__file__), "sample_data", "expenses.csv"), as_attachment=True, download_name="sample_expenses.csv")


@app.get("/api/exceptions")
@auth()
def exceptions():
    return jsonify(rows=all_exc(analyse(docs_for())[1]))


@app.get("/api/budgets")
@auth()
def budgets():
    return jsonify(rows=budget_status(docs_for(), now().strftime("%Y-%m")), categories=sorted(c.title() for c in ep.CATEGORIES))


@app.put("/api/budgets")
@auth("admin")
def set_budget():
    d = request.get_json(silent=True) or {}
    cat = str(d.get("category", "")).title()
    try:
        lim = float(d.get("limit"))
    except (TypeError, ValueError):
        return err("Enter a valid limit.")
    if cat.lower() not in ep.CATEGORIES or not math.isfinite(lim) or lim < 0:
        return err("Choose a valid category and a limit of 0 or more.")
    if lim == 0:
        get_db().budgets.delete_one({"category": cat})
    else:
        get_db().budgets.update_one({"category": cat}, {"$set": {"limit": lim}}, upsert=True)
    audit("budget_set", cat, str(lim))
    return jsonify(message=f"Budget for {cat} " + ("removed." if lim == 0 else "saved."))


@app.get("/api/users")
@auth("admin")
def users():
    return jsonify(rows=[{"id": str(u["_id"]), "name": u["name"], "email": u["email"], "role": u["role"], "emp_id": u["emp_id"]} for u in get_db().users.find().sort("emp_id", 1)])


@app.put("/api/users/<uid>")
@auth("admin")
def set_role(uid):
    role = (request.get_json(silent=True) or {}).get("role")
    if role not in ROLES or uid == g.uid:
        return err("Choose a valid role. You cannot change your own role.")
    try:
        r = get_db().users.update_one({"_id": ObjectId(uid)}, {"$set": {"role": role}})
    except InvalidId:
        return err("User not found.", 404)
    audit("role_change", uid, role)
    return jsonify(message="Role updated.") if r.matched_count else err("User not found.", 404)


@app.get("/api/audit")
@auth("admin")
def audit_log():
    return jsonify(rows=[{**{k: v for k, v in a.items() if k != "_id"}, "ts": a["ts"].strftime("%Y-%m-%d %H:%M")} for a in get_db().audit.find().sort("ts", -1).limit(150)])


@app.get("/api/export/<kind>")
@auth()
def export(kind):
    docs = docs_for(request.args.get("month"))
    _, exc, s = analyse(docs)
    stamp = month_ok(request.args.get("month")) or datetime.now().strftime("%Y%m%d")
    if kind == "excel":
        return send_file(exports.make_excel(docs, s, all_exc(exc)), as_attachment=True, download_name=f"expense_report_{stamp}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if kind == "pdf":
        return send_file(exports.make_pdf(g.name, s, all_exc(exc)), as_attachment=True, download_name=f"expense_report_{stamp}.pdf", mimetype="application/pdf")
    return err("Unknown export type.", 404)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
