"""Minimal OpenAPI document for the Swagger UI page (/api/openapi.json)."""
EP = [("post", "/api/register", "Create account (first user becomes admin)", 0), ("post", "/api/login", "Sign in, returns JWT (rate limited)", 0),
      ("post", "/api/forgot", "Request password reset email", 0), ("post", "/api/reset", "Set new password with reset token", 0),
      ("get", "/api/me", "Current user", 1), ("get", "/api/dashboard", "Totals, trend, forecast, budget alerts (?month=YYYY-MM)", 1),
      ("get", "/api/expenses", "List expenses (q,status,category,month,page,per_page)", 1), ("post", "/api/expenses", "Add expense", 1),
      ("get", "/api/expenses/{id}", "Get expense", 1), ("put", "/api/expenses/{id}", "Edit expense", 1), ("delete", "/api/expenses/{id}", "Delete expense", 1),
      ("post", "/api/expenses/{id}/review", "Approve or reject (manager/admin)", 1), ("post", "/api/expenses/{id}/receipt", "Upload receipt (png/jpg/webp/pdf, 1.5MB)", 1),
      ("get", "/api/expenses/{id}/receipt", "Download receipt", 1), ("delete", "/api/expenses", "Delete all data (admin)", 1),
      ("post", "/api/upload", "Import CSV/XLSX (manager/admin)", 1), ("get", "/api/exceptions", "Exception log", 1),
      ("get", "/api/budgets", "Budgets with current-month spend", 1), ("put", "/api/budgets", "Set category budget (admin; limit 0 removes)", 1),
      ("get", "/api/users", "List users (admin)", 1), ("put", "/api/users/{id}", "Change role (admin)", 1), ("get", "/api/audit", "Audit log (admin)", 1),
      ("get", "/api/export/{kind}", "Download excel or pdf (?month=YYYY-MM)", 1), ("post", "/api/cron/monthly-report", "Email last month's PDF (X-Cron-Secret)", 0),
      ("get", "/api/health", "Health check", 0)]


def spec():
    paths = {}
    for m, p, s, a in EP:
        paths.setdefault(p, {})[m] = {"summary": s, "responses": {"200": {"description": "OK"}}, **({"security": [{"bearer": []}]} if a else {})}
    return {"openapi": "3.0.3", "info": {"title": "Ledgerly API", "version": "2.0"}, "paths": paths,
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}}}
