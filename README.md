# Ledgerly v2 - Expense management with approvals, budgets and reports

Separate **frontend** (static, Vercel) and **backend** (Flask REST API + MongoDB, Render).

```
ledgerly/
  backend/    Flask API, MongoDB, Excel/PDF export, tests, Dockerfile   -> Render
  frontend/   Static HTML/CSS/JS, PWA, dark mode, Dockerfile            -> Vercel
  render.yaml, docker-compose.yml, .github/workflows/ci.yml
```

## Features
- **Roles:** employee (own claims only), manager (all claims, approve/reject, import), admin (users, budgets, delete-all, audit). The first registered account becomes admin; admins promote others on the Users page.
- **Approval workflow:** employees submit as Pending; managers approve/reject with a comment (cannot review their own claims). Employees can edit/delete only Pending claims. Rules are enforced on the server.
- **Receipts:** attach PNG/JPG/WEBP/PDF (max 1.5 MB, stored in MongoDB) and open from the table.
- **Budgets & alerts:** monthly limit per category; warning at 80%, over-budget at 100%.
- **Analytics:** month filter, month-over-month change, linear forecast, per-category outlier detection for "unusually large" claims.
- **Exports:** Excel workbook and PDF report for all time or a chosen month.
- **Import:** drag-and-drop CSV/XLSX with validation and an exception log.
- **Security:** hashed passwords, JWT auth with live role lookup, login rate limit (5 failures / 15 min), password reset by email token (1 h), refuses to start in production with the default `SECRET_KEY`, CORS allow-list, security headers on the frontend.
- **Ops:** audit log, pagination, OpenAPI + Swagger UI (`docs.html`), Dockerfiles + docker-compose, GitHub Actions CI, monthly report email (cron endpoint).
- **UX:** dark mode, installable PWA, responsive layout.

## Run locally (no Docker)
```
cd backend && pip install -r requirements.txt && python app.py        # http://127.0.0.1:5000
cd frontend && python -m http.server 3000                              # http://localhost:3000
```
Without `MONGO_URI` the backend uses an in-memory demo database that resets on restart.

## Run with Docker
`docker compose up --build` then open http://localhost:3000 (MongoDB included).

## Deploy
1. **MongoDB Atlas:** free cluster, database user, Network Access `0.0.0.0/0`, copy the connection string.
2. **Render (backend):** push the repo to GitHub, then create a Blueprint from `render.yaml`. In the API service's Environment settings, set `MONGO_URI` to your Atlas connection string, `CORS_ORIGINS` and `FRONTEND_URL` to the frontend's origin (for example, `https://your-app.vercel.app`), and any SMTP values you use. Keep secrets such as `SECRET_KEY` and `MONGO_URI` in Render's environment settings; do not put them in frontend files. Check `https://<your-api>.onrender.com/api/health`. Free plans sleep when idle (the first request can take about a minute).
3. **Connect the static frontend:** deploy the `frontend` directory to Vercel with **Root Directory = frontend** and Framework Preset = Other. In the Vercel project settings, add the environment variable `API_URL` with the Render API base URL (for example, `https://<your-api>.onrender.com`, without a trailing slash) for the Production and Preview environments you use. The build generates `js/config.js` in the deployed output from this variable; do not commit Render credentials or secrets to the frontend. The checked-in `frontend/js/config.js` continues to point to `http://127.0.0.1:5000` for local development.
4. Copy the deployed frontend's exact origin (scheme and host only, no path or trailing slash) into the Render API service's `CORS_ORIGINS` and `FRONTEND_URL` values, then redeploy the backend. If you use a custom domain, use that domain as the origin. `CORS_ORIGINS` may contain multiple comma-separated origins.
5. Optional monthly email: set `CRON_SECRET` + SMTP on the API, then run the `ledgerly-monthly-report` cron job from `render.yaml` with `API_URL` set. Without SMTP, emails are only written to the server log.

## Try it
Register the first account (admin), import `backend/sample_data/expenses.csv` (Import page), set a few budgets, then register a second account to see the employee view and approval flow.

## Notes and limits
- JWTs are kept in `localStorage` so the two sites can live on different domains. For httpOnly cookies you would need a same-site proxy; that trade-off is documented here rather than hidden.
- Amounts are single-currency. Receipts live in MongoDB; for large volumes move them to S3/Cloudinary.
- Tests: `cd backend && pytest` (roles, approvals, scoping, budgets, receipts, exports, rate limit, password reset).
