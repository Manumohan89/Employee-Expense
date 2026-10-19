const API = window.APP_CONFIG.API_URL.replace(/\/$/, "");
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const role = () => localStorage.getItem("role") || "";
const isPriv = () => role() === "manager" || role() === "admin";
document.documentElement.dataset.theme = localStorage.getItem("theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

async function api(path, o = {}) {
  const h = { ...(o.headers || {}) };
  const t = localStorage.getItem("token");
  if (t) h.Authorization = "Bearer " + t;
  if (o.json) { h["Content-Type"] = "application/json"; o.body = JSON.stringify(o.json); }
  let r;
  try { r = await fetch(API + path, { ...o, headers: h }); }
  catch { throw new Error("Cannot reach the server. Check API_URL in js/config.js and that the backend is running."); }
  if (r.status === 401 && !o.noRedirect && t) { localStorage.clear(); location.href = "login.html"; throw new Error("Session expired."); }
  if (r.headers.get("content-type")?.includes("json")) {
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "Request failed");
    return d;
  }
  if (!r.ok) throw new Error("Request failed");
  return r;
}
function msg(text, type = "ok") {
  const el = document.createElement("div"); el.className = "msg " + type; el.textContent = text;
  const box = $("#msgs"); box.innerHTML = ""; box.appendChild(el); window.scrollTo(0, 0);
}
function nav() {
  const on = !!localStorage.getItem("token"), r = role();
  const current = location.pathname.split("/").pop() || "index.html";
  const link = (href, text, extra = "") => {
    const [target, anchor] = href.split("#");
    const active = current === target && (!anchor || location.hash === "#" + anchor);
    return `<a class="nav-link ${active ? "active" : ""}" href="${href}"${active ? ' aria-current="page"' : ""}${extra}>${text}</a>`;
  };
  const roleLabel = r ? r.charAt(0).toUpperCase() + r.slice(1) : "";
  const menuLinks = on
    ? link("dashboard.html", "Overview") +
      link("expenses.html", "Expenses") +
      (r !== "employee" ? link("upload.html", "Import") + link("exceptions.html", "Exceptions") : "") +
      link("budgets.html", "Budgets") +
      (r === "admin" ? link("users.html", "Users") + link("audit.html", "Audit log") : "")
    : link("index.html#features", "Features") + link("index.html#role-access", "Who it's for");
  const exports = on
    ? `<div class="nav-exports" aria-label="Export reports"><span>Export</span><button type="button" onclick="download('excel')">Excel</button><button type="button" onclick="download('pdf')">PDF</button></div>`
    : "";
  const accountAction = on
    ? `<a class="nav-account" href="#" onclick="logout(); return false;">Sign out <span aria-hidden="true">↗</span></a>`
    : `<a class="nav-account" href="login.html">Sign in</a><a class="btn nav-cta" href="register.html">Create account <span aria-hidden="true">→</span></a>`;
  $("#nav").innerHTML = `
    <header class="bar">
      <a class="logo" href="${on ? "dashboard.html" : "index.html"}" aria-label="Ledgerly home">
        <img src="img/logo.svg" alt="" width="36" height="36"><span>Ledgerly</span>
      </a>
      <button class="menu-toggle" type="button" aria-expanded="false" aria-controls="main-navigation" aria-label="Open navigation">
        <span></span><span></span><span></span>
      </button>
      <nav class="site-nav" id="main-navigation" aria-label="Main navigation">
        <div class="nav-links">${menuLinks}</div>
        <div class="nav-tools">
          ${exports}
          ${on ? `<span class="role-nav ${esc(r)}">${esc(roleLabel)}</span>` : ""}
          ${accountAction}
          <button class="theme theme-control" type="button" onclick="toggleTheme()" aria-label="Switch color theme"></button>
        </div>
      </nav>
    </header>`;
  syncThemeControls();
  const toggle = document.querySelector(".menu-toggle");
  const siteNav = document.querySelector(".site-nav");
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    toggle.setAttribute("aria-label", expanded ? "Open navigation" : "Close navigation");
    siteNav.classList.toggle("open", !expanded);
  });
  siteNav.addEventListener("click", (event) => {
    if (event.target.closest("a")) {
      siteNav.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation");
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#nav") && siteNav.classList.contains("open")) {
      siteNav.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation");
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && siteNav.classList.contains("open")) {
      siteNav.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation");
      toggle.focus();
    }
  });
}
function syncThemeControls() {
  const dark = document.documentElement.dataset.theme === "dark";
  document.querySelectorAll(".theme-control").forEach((button) => {
    button.innerHTML = `<span aria-hidden="true">${dark ? "☀" : "☾"}</span><span>${dark ? "Light" : "Dark"} mode</span>`;
    button.setAttribute("aria-pressed", String(dark));
  });
}
function footer() {
  const on = !!localStorage.getItem("token"), r = role();
  const link = (href, text) => `<a href="${href}">${text}</a>`;
  const roleLinks = on
    ? `${link("dashboard.html", "Dashboard")}${link("expenses.html", "Expenses")}${link("budgets.html", "Budgets")}`
    : `${link("index.html#features", "Features")}${link("index.html#role-access", "Roles & access")}${link("register.html", "Create account")}`;
  const workspaceLinks = on
    ? `${r !== "employee" ? link("upload.html", "Import expenses") : ""}${r === "admin" ? link("users.html", "Manage users") + link("audit.html", "Audit log") : ""}${link("docs.html", "API documentation")}`
    : `${link("login.html", "Sign in")}${link("docs.html", "API documentation")}`;
  document.querySelectorAll("footer").forEach((element) => {
    element.className = "site-footer";
    element.innerHTML = `
      <div class="footer-main">
        <div class="footer-brand">
          <a class="footer-logo" href="${on ? "dashboard.html" : "index.html"}"><img src="img/logo.svg" alt="" width="34" height="34">Ledgerly</a>
          <p>Clear expense workflows, from first receipt to month-end report.</p>
          ${on ? `<span class="footer-role"><span class="footer-role-dot ${esc(r)}"></span>Signed in as ${esc(r.charAt(0).toUpperCase() + r.slice(1))}</span>` : `<span class="footer-caption">Expense management for teams</span>`}
        </div>
        <div class="footer-column"><h2>Explore</h2>${roleLinks}</div>
        <div class="footer-column"><h2>Workspace</h2>${workspaceLinks}</div>
        <div class="footer-aside">
          <span class="footer-status"><span></span> ${on ? "Expense workspace" : "Built for clearer closeouts"}</span>
          <button class="back-to-top" type="button">Back to top <span aria-hidden="true">↑</span></button>
        </div>
      </div>
      <div class="footer-bottom"><span>© ${new Date().getFullYear()} Ledgerly</span><span>Expense management, made clearer.</span></div>`;
    element.querySelector(".back-to-top").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  });
}
function toggleTheme() { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; localStorage.setItem("theme", t); syncThemeControls(); }
function logout() { localStorage.clear(); location.href = "index.html"; }
function guard(roles) {
  if (!localStorage.getItem("token")) { location.href = "login.html"; return false; }
  if (roles && !roles.includes(role())) { location.href = "dashboard.html"; return false; }
  return true;
}
async function blobOf(path) { return (await api(path)).blob(); }
async function saveBlob(path, name) { const a = document.createElement("a"); a.href = URL.createObjectURL(await blobOf(path)); a.download = name; a.click(); }
async function download(kind) {
  try { const m = new URLSearchParams(location.search).get("month") || localStorage.getItem("month") || ""; await saveBlob("/api/export/" + kind + (m ? "?month=" + m : ""), "expense_report." + (kind === "excel" ? "xlsx" : "pdf")); }
  catch (e) { msg(e.message, "err"); }
}
document.addEventListener("DOMContentLoaded", () => { nav(); footer(); });
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) navigator.serviceWorker.register("sw.js").catch(() => {});
