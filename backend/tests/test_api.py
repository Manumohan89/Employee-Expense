import io
from app import app
import app as appmod

def H(t): return {"Authorization": "Bearer " + t}

def signup(c, name, email):
    assert c.post("/api/register", json=dict(name=name, email=email, password="secret1")).status_code == 201
    return c.post("/api/login", json=dict(email=email, password="secret1")).json["token"]

EXP = dict(employee_id="X", date="2026-09-01", category="Food", amount="25.5", description="Lunch", approval_status="Approved")

def test_roles_and_approval():
    c = app.test_client()
    admin = signup(c, "Ada", "a@t.com")
    emp = signup(c, "Eve", "e@t.com")
    assert c.get("/api/me", headers=H(admin)).json["role"] == "admin"
    r = c.post("/api/expenses", headers=H(emp), json=EXP)
    assert r.status_code == 201
    eid = r.json["id"]
    row = c.get("/api/expenses", headers=H(emp)).json["rows"][0]
    assert row["approval_status"] == "Pending" and row["employee_id"] == "EMP102"   # forced by server
    assert c.get("/api/dashboard", headers=H(emp)).json["status_counts"] == {"Pending": 1, "Approved": 0, "Rejected": 0}
    assert c.post(f"/api/expenses/{eid}/review", headers=H(emp), json={"decision": "Approved"}).status_code == 403
    assert c.get("/api/users", headers=H(emp)).status_code == 403
    assert c.post(f"/api/expenses/{eid}/review", headers=H(admin), json={"decision": "Approved", "comment": "ok"}).status_code == 200
    assert c.get("/api/dashboard", headers=H(emp)).json["status_counts"] == {"Pending": 0, "Approved": 1, "Rejected": 0}
    assert c.put(f"/api/expenses/{eid}", headers=H(emp), json=EXP).status_code == 403   # approved -> locked for employee
    assert c.get("/api/expenses", headers=H(admin)).json["rows"][0]["approval_status"] == "Approved"
    users = c.get("/api/users", headers=H(admin)).json["rows"]
    uid = [u for u in users if u["role"] == "employee"][0]["id"]
    assert c.put(f"/api/users/{uid}", headers=H(admin), json={"role": "manager"}).status_code == 200
    assert c.get("/api/me", headers=H(emp)).json["role"] == "manager"
    assert c.put("/api/budgets", headers=H(emp), json={"category": "Food", "limit": 100}).status_code == 403
    assert c.get("/api/users", headers=H(emp)).status_code == 403
    assert c.get("/api/audit", headers=H(emp)).status_code == 403
    assert c.get("/api/audit", headers=H(admin)).json["rows"]

def test_scope_budget_receipt_exports():
    c = app.test_client()
    admin = signup(c, "Ada", "a@t.com")
    emp = signup(c, "Eve", "e@t.com")
    f = open("sample_data/expenses.csv", "rb").read()
    assert c.post("/api/upload", headers=H(emp), data=dict(file=(io.BytesIO(f), "x.csv"))).status_code == 403
    assert c.post("/api/upload", headers=H(admin), data=dict(file=(io.BytesIO(f), "x.csv"), replace="1")).status_code == 200
    d = c.get("/api/dashboard", headers=H(admin)).json
    assert d["total"] > 0 and d["trend"]["forecast"] is not None and d["months"]
    assert sum(d["status_counts"].values()) == d["n"]
    assert d["pending"] == d["status_counts"]["Pending"]
    mine = c.get("/api/expenses?per_page=100", headers=H(emp)).json
    assert 0 < mine["total"] < 60 and all(r["employee_id"] == "EMP102" for r in mine["rows"])   # employee sees only own
    pg = c.get("/api/expenses?per_page=10&page=2", headers=H(admin)).json
    assert pg["page"] == 2 and len(pg["rows"]) == 10 and pg["pages"] > 1
    m = d["months"][0]
    assert all(r["date"].startswith(m) for r in c.get(f"/api/expenses?month={m}", headers=H(admin)).json["rows"])
    assert c.put("/api/budgets", headers=H(emp), json=dict(category="Food", limit=10)).status_code == 403
    assert c.put("/api/budgets", headers=H(admin), json=dict(category="food", limit=1)).status_code == 200
    assert c.get("/api/budgets", headers=H(admin)).json["rows"][0]["category"] == "Food"
    eid = c.post("/api/expenses", headers=H(emp), json=EXP).json["id"]
    assert c.post(f"/api/expenses/{eid}/receipt", headers=H(emp), data=dict(file=(io.BytesIO(b"%PDF-1.4 x"), "r.pdf"))).status_code == 200
    assert c.post(f"/api/expenses/{eid}/receipt", headers=H(emp), data=dict(file=(io.BytesIO(b"x"), "r.exe"))).status_code == 400
    assert c.get(f"/api/expenses/{eid}/receipt", headers=H(emp)).data.startswith(b"%PDF")
    assert c.get("/api/export/excel", headers=H(admin)).status_code == 200
    assert c.get("/api/export/pdf?month=" + m, headers=H(admin)).data[:4] == b"%PDF"

def test_security_flows(monkeypatch):
    c = app.test_client()
    signup(c, "Ada", "a@t.com")
    for _ in range(5):
        assert c.post("/api/login", json=dict(email="a@t.com", password="bad")).status_code == 401
    assert c.post("/api/login", json=dict(email="a@t.com", password="secret1")).status_code == 429   # rate limited
    sent = []
    monkeypatch.setattr(appmod, "send_mail", lambda to, s, b, a=None: sent.append(b) or True)
    assert c.post("/api/forgot", json=dict(email="a@t.com")).status_code == 200
    tok = sent[0].split("token=")[1].split()[0]
    assert c.post("/api/reset", json=dict(token="wrong", password="newpass1")).status_code == 400
    assert c.post("/api/reset", json=dict(token=tok, password="newpass1")).status_code == 200
    assert c.post("/api/login", json=dict(email="a@t.com", password="newpass1")).status_code == 200
    assert c.post("/api/cron/monthly-report").status_code == 401


def test_invalid_pagination_and_non_finite_amounts():
    c = app.test_client()
    admin = signup(c, "Ada", "a@t.com")
    for query in ("page=oops", "per_page=1.5", "page=1&per_page=nope"):
        response = c.get("/api/expenses?" + query, headers=H(admin))
        assert response.status_code == 400
        assert "whole numbers" in response.json["error"]
    for amount in ("inf", "-inf", "nan", "1e309"):
        response = c.post("/api/expenses", headers=H(admin), json={**EXP, "amount": amount})
        assert response.status_code == 400
        assert "finite number" in response.json["error"]
    for limit in ("inf", "-inf", "nan"):
        response = c.put("/api/budgets", headers=H(admin), json={"category": "Food", "limit": limit})
        assert response.status_code == 400


def test_registration_rejects_mismatched_password_confirmation():
    c = app.test_client()
    mismatch = c.post("/api/register", json={
        "name": "Ada",
        "email": "ada@example.com",
        "password": "correct-horse",
        "confirm_password": "different-password",
    })
    assert mismatch.status_code == 400
    assert mismatch.json["error"] == "Passwords do not match."

    match = c.post("/api/register", json={
        "name": "Ada",
        "email": "ada@example.com",
        "password": "correct-horse",
        "confirm_password": "correct-horse",
    })
    assert match.status_code == 201


def test_health_reports_database_failure(monkeypatch):
    class BrokenDatabase:
        def command(self, name):
            raise RuntimeError("database unavailable")

    c = app.test_client()
    monkeypatch.setattr(appmod, "get_db", lambda: BrokenDatabase())
    response = c.get("/api/health")
    assert response.status_code == 503
    assert response.json == {"status": "error", "database": "unavailable"}
