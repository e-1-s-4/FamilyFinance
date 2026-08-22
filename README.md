# FamilyFinance — Personal & Family Budget Tracker

FamilyFinance is a personal and family-oriented budget tracking application built with Python, Flask, and SQLite. It helps you track income, expenses, bills, payees, budgets, savings goals, recurring transactions, accounts, and financial reports in one place.

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/40d78bf5-2dfb-4014-accc-c7d4d92e18f0" />


This README documents the enhanced version of the project, including session authentication, CSRF protection, role support, improved money handling, budgets, goals, recurring rules, notifications, audit logs, reports, backups, and production-ready configuration options.

### What's New in This Pass

- **Correct delete semantics.** The five stragglers (`category_delete`, `payee_delete`, `bill_delete`, `expense_delete`, `income_delete`) now return `404` for unknown ids like every other delete endpoint, and `PUT /api/payees/<id>` gained the same existence check.
- **Partial updates no longer wipe transaction splits.** `expense_update`/`income_update` only replace splits when the client actually sends a `"splits"` key; editing just `notes` keeps every split allocation intact (an explicit `"splits": []` still means "clear them").
- **Payee references validated everywhere.** An invalid `payee_id` is rejected with a clean `400` even when a free-text `payee_name` is also supplied, and payee resolution (name/email/address backfill) is centralized in `parse_bill_payload` so recurring bill rules with only a `payee_id` generate bills with the real payee name instead of a blank one.
- **Row-isolated CSV imports with flexible dates.** Each imported row runs in its own `SAVEPOINT`: a bad row fails alone and is reported in a new `failed: [{row, error}]` array instead of rolling back the whole file. Dates accept `YYYY-MM-DD`, `MM/DD/YYYY`, `DD/MM/YYYY`, `YYYY/MM/DD`, `DD.MM.YYYY`, and `MM-DD-YYYY`. The frontend import dialog now includes manual column mapping with a live preview and an explicit date-format picker.
- **Split-aware category reporting.** Budget-vs-actual and dashboard top categories count split allocations under their split categories (no double counting of the parent expense), and the dashboard's top-category breakdown finally includes bill payments.
- **Weekly allowances measured against a monthly window correctly.** Weekly amounts are scaled by 52/12 before comparing to month-to-date spending, so a $20/week allowance shows ~$86.67/month of headroom.
- **Honest debt payoff simulation.** Leftover extra payments cascade across debts within a month; when a debt dies its freed-up minimum rolls into later months (real snowball/avalanche behavior); user-configured minimums are honored exactly (including intentional $0); plans that can't converge are flagged with `will_never_be_paid_off` instead of silently reporting "360 months".
- **Consistent reminder dates.** `/api/reminders` computes one local date in Python and passes it into SQL, so inclusion filters and day counts can no longer disagree by a day near midnight on non-UTC servers.
- **Reliability hardening.** Startup now requires SQLite ≥ 3.35 (for `INSERT ... RETURNING`) with a clear error on older builds; request-body limits are configurable via `FF_MAX_CONTENT_LENGTH_MB` (default 50 MB) so real-world database restores don't 413; restore responses recommend a process restart so other workers drop stale connections; login throttling adds a per-username limiter alongside the per-IP one.
- **Personal access tokens.** Create scoped, revocable `ffpat_...` bearer tokens (role-limited, stored hashed, plaintext shown once) for cron jobs, Home Assistant, or receipt webhooks — no cookies or CSRF needed.
- **Bill payment management.** New `GET/DELETE /api/bills/<id>/payments[/<payment_id>]` routes let you audit and reverse individual payments, automatically recomputing `paid_cents` and status. The UI exposes this via a Payments dialog on each bill.
- **New features.** Recurring-aware cash-flow forecast (blends active rule schedules and unpaid bill due dates into projections), sinking-fund auto-allocation (goals accept `allocation_pct`, funded from every income deposit), optional multi-currency fields on expenses/income (`original_currency`, `exchange_rate`, `original_amount`), and member references upgraded from free text to a validated `member_id` foreign key with best-effort name matching during the transition.

### What's New in the Previous Pass

- **Reliability fixes.** `GET /api/payees?q=...` no longer fails with a database error when bills exist (an ambiguous-column bug); `POST /api/bills/<id>/void` now returns `404` for unknown or deleted bills instead of silently succeeding; bill payments validate `account_id` and return a clean `400` instead of a database error.
- **Resilient recurring runs.** `POST /api/recurring/run` processes each rule inside its own savepoint, so one malformed rule can no longer abort the whole batch or roll back other rules' created items. The response now includes a `failed` array describing any rule that could not be processed, alongside the familiar `created` array, and each failure raises a notification.
- **Faster reports.** Dashboard trends, annual reports, cash-flow series, account balances/net worth, debt payoff, forecast, and allowance tracking were rewritten from per-row/per-month/per-account query loops into grouped aggregate queries — an order of magnitude fewer SQL round-trips on populated databases.
- **Member-aware budget report.** `GET /api/reports/budget` now compares member-specific budgets against only that member's expenses; household budgets continue to count all spending plus bill payments.
- **Safer restore.** `POST /api/admin/restore` verifies the uploaded file is a SQLite database containing the expected FamilyFinance tables before swapping it in, rejecting garbage or foreign databases with a clear `400`.
- **Housekeeping.** Voided bills are excluded from the paid total in bill CSV exports, the rate limiter prunes stale entries, and new indexes cover member/account lookups.

---

## Earlier Improvements

- **A complete frontend.** `templates/index.html` was rebuilt from the ground up as a full single-page app that covers every backend feature — previously the shipped frontend only exposed a handful of dashboard/expense/income/bill/budget/goal screens and used a small fraction of the API. It now includes Accounts, Payees, Members, Categories, Recurring Rules, the full Reports suite, Notifications, and Admin, plus edit/delete everywhere, filtering, search, pagination, and CSV export. See [Frontend](#frontend) for the full list.
- **Household user management.** The API and schema already supported `Admin` / `Editor` / `Viewer` roles, but there was previously no way to create a second login — only the bootstrap `Admin` account could ever exist. Added `GET/POST/PUT/DELETE /api/users` (Admin-only, with guardrails against locking yourself out) so a family can actually give each member their own login. See [Managing Household Users](#managing-household-users).
- **Financial health insights.** The dashboard now includes a `financial_health` score with actionable recommendations, and `GET /api/insights` exposes a compact snapshot for widgets, automations, or mobile clients.
- **Safer linked records.** Transactions, bills, and allowances now validate referenced accounts or family members before writing, returning clear 400-level errors instead of relying on database constraint failures.
- **Small correctness fixes:** `GET /api/notifications` now returns an `unread_count`; `PUT /api/categories/<id>` no longer requires re-sending the category name just to toggle `is_active`.

---

## Table of Contents

- [Features](#features)
- [Technology Stack](#technology-stack)
- [Project Overview](#project-overview)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [First-Time Login](#first-time-login)
- [Frontend](#frontend)
- [API Overview](#api-overview)
- [Authentication and CSRF](#authentication-and-csrf)
- [API Examples](#api-examples)
- [Core Concepts](#core-concepts)
- [Money Handling](#money-handling)
- [Date Handling](#date-handling)
- [Roles and Permissions](#roles-and-permissions)
- [API Reference](#api-reference)
- [Reports](#reports)
- [Recurring Transactions](#recurring-transactions)
- [Backups and Restore](#backups-and-restore)
- [Security](#security)
- [Testing](#testing)
- [Production Deployment](#production-deployment)
- [Docker](#docker)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Features

### Financial Tracking

- Track expenses by category, date, store/vendor, family member, notes, and tags.
- Track income by category, date, source, family member, notes, and tags.
- Track bills with:
  - Bill number
  - Payee
  - Category
  - Bill date
  - Due date
  - Line items
  - Discounts
  - Tax
  - Partial payments
  - Payment history
  - Paid / Pending / Partially Paid / Void statuses

### Family Budgeting

- Create monthly and yearly budgets by category.
- Compare budgeted amounts against actual spending.
- Track savings goals.
- Add contributions toward goals.
- Track family members.
- Assign transactions to family members.

### Accounts and Net Worth

- Create financial accounts such as:
  - Checking
  - Savings
  - Cash
  - Investment
  - Credit Card
  - Loan
  - Other
- Track opening balances.
- Associate income, expenses, and bill payments with accounts.
- View estimated account balances.
- View a simple net worth report.

### Automation

- Create recurring rules for:
  - Expenses
  - Income
  - Bills
- Run recurring rules manually via API.
- Generate upcoming items from recurring schedules.

### Reporting

- Dashboard with monthly and annual summaries.
- Financial health score and recommendations for savings, overdue bills, and budget pressure.
- Monthly cash flow trend.
- Expense categories.
- Income categories.
- Bill categories.
- Budget vs actual report.
- Cash flow report.
- Payee report.
- Subscription report.
- Net worth report.
- Bill reminders.
- CSV exports for expenses, income, and bills.

### Security and Administration

- Session-based authentication.
- CSRF-protected mutating API requests.
- Password change endpoint.
- Role-based access control.
- Audit logs.
- Admin database backup endpoint.
- Health check endpoints.
- Security headers.
- Basic rate limiting.

---

## Technology Stack

- **Python 3.10+**
- **Flask**
- **SQLite 3.35+** (March 2021) — the bill numbering sequence uses `INSERT ... ON CONFLICT ... RETURNING`. The app checks the version at startup and fails with a clear message on older builds (e.g. RHEL/CentOS 8-era system SQLite).
- **Werkzeug password hashing**
- **Standard library modules**:
  - `sqlite3`
  - `decimal`
  - `json`
  - `csv`
  - `datetime`
  - `secrets`
  - `logging`

The project is intentionally lightweight and can run on a small VPS, home server, Raspberry Pi, or container.

---

## Project Overview

FamilyFinance is primarily a backend JSON API. It can be used with:

- A custom web frontend
- A mobile app
- Scripts
- curl / Postman
- Automated financial workflows

---

## Quick Start

### 1. Clone or download the project

```bash
cd familyfinance
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

#### Linux/macOS

```bash
source .venv/bin/activate
```

#### Windows PowerShell

```powershell
.venv\Scripts\Activate.ps1
```

#### Windows CMD

```cmd
.venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install flask
```

For testing:

```bash
pip install pytest
```

### 4. Run the application

```bash
python app.py
```

By default, the app starts at:

```text
http://127.0.0.1:5000
```

You can change this using environment variables:

```bash
FF_HOST=0.0.0.0
FF_PORT=8000
python app.py
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---:|---|---|
| `FF_SECRET_KEY` | Recommended | Generated in development if absent | Flask secret key used for session signing. Must be set in production. |
| `FF_DB_PATH` | No | `./familyfinance.db` | Path to the SQLite database file. |
| `FF_ADMIN_USER` | No | `admin` | Initial admin username. |
| `FF_ADMIN_PASS` | Recommended | Generated if absent | Initial admin password. If not set, a random password is generated and printed to the console on first startup. |
| `FF_SESSION_HOURS` | No | `8` | Session lifetime in hours. |
| `FF_SECURE_COOKIES` | No | `0` | Set to `1` when serving over HTTPS. Makes session cookies secure. |
| `FF_HSTS` | No | `0` | Set to `1` to send HTTP Strict Transport Security header. Use only with HTTPS. |
| `FF_ALLOW_DEV_SECRET` | No | `1` | If `0`, the app refuses to start without `FF_SECRET_KEY`. |
| `FF_MAX_CONTENT_LENGTH_MB` | No | `50` | Maximum request body size in MB. Applies to CSV imports and database restores; raise it if your backups grow past the default. |
| `FF_HOST` | No | `127.0.0.1` | Host for the built-in development server. |
| `FF_PORT` | No | `5000` | Port for the built-in development server. |
| `FF_DEBUG` | No | `0` | Set to `1` to enable Flask debug mode. Do not use in production. |

### Example local environment

```bash
export FF_SECRET_KEY="change-me"
export FF_ADMIN_USER="admin"
export FF_ADMIN_PASS="strong-password"
export FF_DB_PATH="./familyfinance.db"
export FF_DEBUG="1"
```

### Example production environment

```bash
export FF_SECRET_KEY="long-random-secret"
export FF_ADMIN_USER="admin"
export FF_ADMIN_PASS="strong-admin-password"
export FF_DB_PATH="/var/lib/familyfinance/familyfinance.db"
export FF_SESSION_HOURS="12"
export FF_SECURE_COOKIES="1"
export FF_HSTS="1"
export FF_ALLOW_DEV_SECRET="0"
```

---

## First-Time Login

On first startup, the application creates an admin user.

If you set:

```bash
FF_ADMIN_PASS="my-password"
```

then you can log in with:

```text
Username: admin
Password: my-password
```

If you do not set `FF_ADMIN_PASS`, the app generates a random password and prints it to the console when the admin user is first created.

Example log output:

```text
**********************************************************************
Created admin user 'admin' with generated password:
AbC123xYz...
Set FF_ADMIN_PASS to define your own admin password.
**********************************************************************
```

Change the password after first login using:

```http
POST /api/auth/password
```

---

## Frontend

The backend is API-first, and ships with a complete, dependency-light single-page frontend at:

```text
templates/index.html
```

The route `/` renders it via `render_template("index.html")`. If the template is ever removed, a minimal API landing page is shown instead.

The bundled frontend is a single self-contained HTML file (inline CSS + vanilla JavaScript, no build step, no npm) that covers the entire API surface:

- **Dashboard** — monthly income/spending/savings stats, a 6-month cash-flow chart, top spending categories, budget progress, goal progress, and recent activity.
- **Expenses / Income** — full create, edit, delete, search, category/member/date filters, and CSV export.
- **Bills** — a line-item editor (add/remove items, live subtotal/discount/tax/total preview), partial or full payments, void, print-friendly bill view, status filters, and CSV export.
- **Budgets** — monthly/yearly budgets per category with live spent-vs-remaining progress bars.
- **Goals** — savings goals with contribution tracking and progress bars.
- **Recurring Rules** — automate expenses, income, or bills on a schedule, with a "run due rules now" action.
- **Reports** — year-over-year overview, budget vs. actual, 12-month cash flow, net worth by account, detected subscriptions, and payee totals, all with lightweight inline SVG charts (no external charting library).
- **Accounts, Payees, Members, Categories** — full management screens for every supporting entity.
- **Notifications** — a bell menu with unread counts, generated automatically when recurring rules run.
- **Settings** — family details, currency, and bill numbering (Admin only), plus self-service password change for any signed-in user.
- **Admin** — household user management (add/edit/remove Admin, Editor, and Viewer logins), the audit log, and one-click database backup download.

It authenticates against `POST /api/auth/login`, relies on the `HttpOnly` session cookie for subsequent requests, and attaches the CSRF token returned at login (and refreshed via `GET /api/me`) as `X-CSRF-Token` on every `POST` / `PUT` / `DELETE` call. UI actions are gated by the signed-in user's role (see [Roles and Permissions](#roles-and-permissions)) so Viewers never see edit controls they aren't allowed to use — though the server enforces the same rules independently, since client-side gating is a convenience, not a security boundary.

The visual design uses a warm "household ledger" theme (deep green + brass accents, a serif display face for headings, and tabular monospace figures for all money amounts) with a built-in light/dark toggle and a responsive layout that collapses to an off-canvas menu on small screens.

If you'd rather build your own frontend against the API, the same rules apply:

1. Call `POST /api/auth/login`.
2. Store the session cookie.
3. Store the returned CSRF token.
4. Send the CSRF token as `X-CSRF-Token` on all mutating requests:
   - `POST`
   - `PUT`
   - `PATCH`
   - `DELETE`

---

## API Overview

All API routes are mounted under:

```text
/api/
```

Most endpoints return JSON.

Mutating endpoints require:

- An authenticated session
- A valid CSRF token header

Read-only endpoints require authentication but usually do not require a CSRF token.

---

## Authentication and CSRF

### Login

```http
POST /api/auth/login
```

Body:

```json
{
  "username": "admin",
  "password": "your-password"
}
```

Response:

```json
{
  "success": true,
  "user": {
    "username": "admin",
    "role": "Admin"
  },
  "csrf_token": "some-csrf-token"
}
```

The session cookie is set automatically.

### CSRF Token

Use the returned `csrf_token` in subsequent mutating requests:

```http
X-CSRF-Token: some-csrf-token
```

Alternatively, you can send it in the JSON body:

```json
{
  "_csrf": "some-csrf-token"
}
```

### Logout

```http
POST /api/auth/logout
```

This clears the session.

### Personal Access Tokens (Bearer auth)

For cron jobs, Home Assistant, receipt-scanning webhooks, or any other
headless automation, create a personal access token instead of sharing a
session:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/auth/tokens` | List your tokens (Admins see all). Metadata only — hashes are never returned. |
| `POST` | `/api/auth/tokens` | Create a token: `{"name": "cron job", "role": "Editor"}`. Returns the plaintext token **once**. |
| `DELETE` | `/api/auth/tokens/<id>` | Revoke a token. Owners and Admins only. |

Tokens are stored as SHA-256 hashes, are prefixed `ffpat_`, can never grant a
stronger role than their owner currently has, and stop working the moment the
owner's role is demoted below the token's scope or the token is revoked.

Authenticate with:

```http
Authorization: Bearer ffpat_your-token-here
```

Bearer requests do not need the CSRF header (CSRF defends cookie sessions,
which tokens don't use):

```bash
curl -H "Authorization: Bearer ffpat_..." \
  -H "Content-Type: application/json" \
  -d '{"title":"Imported","category":"Other","amount":"5.00","expense_date":"2026-07-01"}' \
  http://localhost:5000/api/expenses
```

---

## API Examples

The examples below use `curl`.

### Login

```bash
curl -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"strong-password"}' \
  http://localhost:5000/api/auth/login
```

Save the CSRF token from the response.

### Get dashboard

```bash
curl -b cookies.txt \
  http://localhost:5000/api/dashboard
```

### Create an expense

```bash
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: YOUR_CSRF_TOKEN" \
  -d '{
    "title": "Supermarket",
    "category": "Groceries & Food",
    "amount": "42.50",
    "expense_date": "2026-06-20",
    "store": "Local Market",
    "member": "Alice",
    "notes": "Weekly shopping",
    "tags": "groceries,food"
  }' \
  http://localhost:5000/api/expenses
```

### Create income

```bash
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: YOUR_CSRF_TOKEN" \
  -d '{
    "title": "Salary",
    "category": "Salary / Wages",
    "amount": "2500.00",
    "income_date": "2026-06-01",
    "source": "Employer",
    "member": "Alice"
  }' \
  http://localhost:5000/api/income
```

### Create a bill

```bash
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: YOUR_CSRF_TOKEN" \
  -d '{
    "payee_name": "Electric Company",
    "bill_category": "Electricity",
    "bill_date": "2026-06-01",
    "due_date": "2026-06-15",
    "tax_rate": "0",
    "discount_pct": "0",
    "items": [
      {
        "item_name": "Monthly electricity usage",
        "quantity": 1,
        "unit_price": "86.20"
      }
    ]
  }' \
  http://localhost:5000/api/bills
```

### Pay part of a bill

```bash
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: YOUR_CSRF_TOKEN" \
  -d '{
    "amount": "20.00",
    "payment_date": "2026-06-05",
    "notes": "Partial payment"
  }' \
  http://localhost:5000/api/bills/1/pay
```

### Create a monthly budget

```bash
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: YOUR_CSRF_TOKEN" \
  -d '{
    "category": "Groceries & Food",
    "period": "monthly",
    "amount": "600.00"
  }' \
  http://localhost:5000/api/budgets
```

### Create a savings goal

```bash
curl -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: YOUR_CSRF_TOKEN" \
  -d '{
    "name": "Emergency Fund",
    "target": "10000.00",
    "current": "1500.00",
    "target_date": "2027-12-31",
    "notes": "Six months of expenses"
  }' \
  http://localhost:5000/api/goals
```

### Run recurring rules

```bash
curl -b cookies.txt \
  -H "X-CSRF-Token: YOUR_CSRF_TOKEN" \
  -X POST \
  http://localhost:5000/api/recurring/run
```

### Download database backup

```bash
curl -b cookies.txt \
  -o familyfinance-backup.db \
  http://localhost:5000/api/admin/backup
```

---

## Core Concepts

### Expenses

Expenses represent money spent outside of formal bills.

Examples:

- Groceries
- Dining out
- Fuel
- Pharmacy purchases
- Clothing
- Entertainment

### Income

Income represents money received.

Examples:

- Salary
- Freelance income
- Dividends
- Rental income
- Government benefits

### Bills

Bills represent formal payment obligations.

Examples:

- Rent
- Mortgage
- Electricity
- Internet
- Insurance
- Loan repayment
- Subscription services

Bills support:

- Line items
- Discounts
- Taxes
- Partial payments
- Payment history
- Status tracking

### Payees

Payees are the people or organizations you pay.

Examples:

- Landlord
- Electric company
- Internet provider
- School
- Insurance company

### Accounts

Accounts represent financial containers.

Examples:

- Checking account
- Savings account
- Cash wallet
- Credit card
- Loan account

### Budgets

Budgets define planned spending limits by category.

Budgets can be:

- Monthly
- Yearly
- Category-based
- Optionally member-specific

### Goals

Goals represent savings targets.

Examples:

- Emergency fund
- Vacation fund
- New car
- Home down payment
- Debt payoff

### Recurring Rules

Recurring rules automatically create expenses, income, or bills based on a schedule.

Examples:

- Monthly rent bill
- Weekly allowance expense
- Annual insurance bill
- Monthly salary income

---

## Money Handling

The enhanced application stores monetary values as integer cents internally.

For example:

```text
$12.34 = 1234 cents
```

API endpoints generally accept amounts in one of two ways:

### Decimal amount in major currency units

```json
{
  "amount": "12.34"
}
```

### Integer cents

```json
{
  "amount_cents": 1234
}
```

Most responses include both representations where useful:

```json
{
  "amount_cents": 4250,
  "amount": 42.50
}
```

This avoids common floating-point rounding problems.

---

## Date Handling

Dates should be provided in ISO format:

```text
YYYY-MM-DD
```

Examples:

```text
2026-06-01
2026-12-31
```

Some timestamps may include time and timezone information.

---

## Roles and Permissions

The application supports three roles:

| Role | Description |
|---|---|
| `Admin` | Full access, including settings, backups, audit logs, and user-facing administrative operations. |
| `Editor` | Can create and modify most financial records. |
| `Viewer` | Can read data but cannot modify records. |

Typical permission behavior:

- Read-only API endpoints require authentication.
- Mutating endpoints generally require `Admin` or `Editor`.
- Settings and administrative endpoints require `Admin`.

### Managing Household Users

A single `Admin` account is created automatically on first run (see [First-Time Login](#first-time-login)). To add logins for other family members, use the user-management endpoints (all `Admin`-only):

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/users` | List every household login and role. |
| `POST` | `/api/users` | Create a user: `{"username", "password", "role"}`. Password must be at least 8 characters. |
| `PUT` | `/api/users/<id>` | Update a user's role and/or reset their password (`password` is optional; omit to leave it unchanged). |
| `DELETE` | `/api/users/<id>` | Remove a user. |

Two safety rails are enforced server-side: you can't delete or demote the last remaining `Admin` account, and you can't delete the account you're currently signed in as. The bundled frontend exposes all of this from **Admin → Household Users**.

---

## API Reference

### Health Checks

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/healthz` | Basic liveness check. |
| `GET` | `/readyz` | Checks database readiness. |

---

### Authentication

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Log in with username and password. |
| `POST` | `/api/auth/logout` | Log out and clear session. |
| `GET` | `/api/me` | Get current user and CSRF token. |
| `GET` | `/api/csrf` | Get a CSRF token. |
| `POST` | `/api/auth/password` | Change current user password. |

#### Password Change Payload

```json
{
  "current_password": "old-password",
  "new_password": "new-password"
}
```

---

### Settings

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/settings` | Get application settings and category lists. |
| `POST` | `/api/settings` | Update application settings. Admin only. |

Settings include:

```json
{
  "family_name": "Our Family",
  "family_address": "123 Home Street\nCity, State 00000",
  "primary_email": "family@example.com",
  "primary_phone": "",
  "currency_symbol": "$",
  "currency_code": "USD",
  "monthly_income_goal": 5000.0,
  "savings_target_pct": 20.0,
  "bill_prefix": "BILL",
  "family_notes": "Track your family finances with ease!"
}
```

---

### Categories

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/categories` | List categories. |
| `POST` | `/api/categories` | Create category. |
| `PUT` | `/api/categories/<id>` | Update category. |
| `DELETE` | `/api/categories/<id>` | Deactivate category. |

Query parameters:

| Parameter | Description |
|---|---|
| `type` | `expense`, `income`, or `bill`. |
| `active_only` | Defaults to `1`. |

Example:

```text
GET /api/categories?type=expense
```

---

### Members

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/members` | List active members. |
| `POST` | `/api/members` | Create member. |
| `PUT` | `/api/members/<id>` | Update member. |
| `DELETE` | `/api/members/<id>` | Deactivate member. |

---

### Accounts

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/accounts` | List accounts with computed balances. |
| `POST` | `/api/accounts` | Create account. |
| `PUT` | `/api/accounts/<id>` | Update account. |
| `DELETE` | `/api/accounts/<id>` | Deactivate account. |

Supported account types:

```text
Checking
Savings
Cash
Investment
Credit Card
Loan
Other
```

---

### Payees

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/payees` | List payees. |
| `POST` | `/api/payees` | Create payee. |
| `GET` | `/api/payees/<id>` | Get payee details. |
| `PUT` | `/api/payees/<id>` | Update payee. |
| `DELETE` | `/api/payees/<id>` | Delete payee. |

Query parameters:

| Parameter | Description |
|---|---|
| `q` | Search name, email, phone, and notes. |
| `category` | Filter by payee category. |
| `limit` | Page size. |
| `offset` | Pagination offset. |

---

### Bills

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/bills` | List bills. |
| `POST` | `/api/bills` | Create bill. |
| `GET` | `/api/bills/<id>` | Get bill details. |
| `PUT` | `/api/bills/<id>` | Update bill or change status. |
| `DELETE` | `/api/bills/<id>` | Soft-delete bill. |
| `POST` | `/api/bills/<id>/pay` | Record payment. |
| `GET` | `/api/bills/<id>/payments` | List payments for a bill. |
| `DELETE` | `/api/bills/<id>/payments/<payment_id>` | Reverse one payment; `paid_cents` and status are recomputed automatically. |
| `POST` | `/api/bills/<id>/void` | Void bill. |
| `GET` | `/api/bills/<id>/print` | Printable bill HTML. |
| `GET` | `/api/bills/export` | Export bills as CSV. |

#### Bill Query Parameters

| Parameter | Description |
|---|---|
| `q` | Search bill number, payee, and notes. |
| `status` | `Pending`, `Partially Paid`, `Paid`, `Void`, or `Overdue`. |
| `payee_id` | Filter by payee. |
| `category` | Filter by bill category. |
| `from` | Start date. |
| `to` | End date. |
| `limit` | Page size. |
| `offset` | Pagination offset. |

#### Create Bill Payload

```json
{
  "payee_name": "Electric Company",
  "bill_category": "Electricity",
  "bill_date": "2026-06-01",
  "due_date": "2026-06-15",
  "discount_pct": "0",
  "tax_rate": "0",
  "notes": "Monthly electricity bill",
  "items": [
    {
      "item_name": "Electricity usage",
      "description": "June billing period",
      "quantity": 1,
      "unit_price": "86.20"
    }
  ]
}
```

#### Bill Status Values

```text
Pending
Partially Paid
Paid
Void
```

The API may also return a computed field:

```json
{
  "display_status": "Overdue"
}
```

This means the stored status is pending or partially paid, but the due date has passed.

---

### Expenses

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/expenses` | List expenses. |
| `POST` | `/api/expenses` | Create expense. |
| `GET` | `/api/expenses/<id>` | Get expense. |
| `PUT` | `/api/expenses/<id>` | Update expense. |
| `DELETE` | `/api/expenses/<id>` | Soft-delete expense. |
| `GET` | `/api/expenses/export` | Export expenses as CSV. |

#### Expense Query Parameters

| Parameter | Description |
|---|---|
| `q` | Search title, store, notes, member, and tags. |
| `category` | Filter by category. |
| `member` | Filter by family member. |
| `from` | Start date. |
| `to` | End date. |
| `limit` | Page size. |
| `offset` | Pagination offset. |

#### Create Expense Payload

```json
{
  "title": "Supermarket",
  "category": "Groceries & Food",
  "amount": "42.50",
  "expense_date": "2026-06-20",
  "store": "Local Market",
  "member": "Alice",
  "tags": "groceries,weekly",
  "notes": "Weekly shopping"
}
```

---

### Income

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/income` | List income records. |
| `POST` | `/api/income` | Create income record. |
| `GET` | `/api/income/<id>` | Get income record. |
| `PUT` | `/api/income/<id>` | Update income record. |
| `DELETE` | `/api/income/<id>` | Soft-delete income record. |
| `GET` | `/api/income/export` | Export income as CSV. |

#### Income Query Parameters

| Parameter | Description |
|---|---|
| `q` | Search title, source, notes, member, and tags. |
| `category` | Filter by category. |
| `member` | Filter by family member. |
| `from` | Start date. |
| `to` | End date. |
| `limit` | Page size. |
| `offset` | Pagination offset. |

#### Create Income Payload

```json
{
  "title": "Salary",
  "category": "Salary / Wages",
  "amount": "2500.00",
  "income_date": "2026-06-01",
  "source": "Employer",
  "member": "Alice"
}
```

---

### Budgets

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/budgets` | List budgets. |
| `POST` | `/api/budgets` | Create budget. |
| `PUT` | `/api/budgets/<id>` | Update budget. |
| `DELETE` | `/api/budgets/<id>` | Delete budget. |

#### Budget Query Parameters

| Parameter | Description |
|---|---|
| `period` | `monthly` or `yearly`. |
| `category` | Filter by category. |

#### Create Budget Payload

```json
{
  "category": "Groceries & Food",
  "member": "",
  "period": "monthly",
  "amount": "600.00",
  "notes": "Monthly grocery limit"
}
```

---

### Goals

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/goals` | List active goals. |
| `POST` | `/api/goals` | Create goal. |
| `PUT` | `/api/goals/<id>` | Update goal. |
| `POST` | `/api/goals/<id>/contribute` | Add money toward goal. |
| `DELETE` | `/api/goals/<id>` | Deactivate goal. |

#### Create Goal Payload

```json
{
  "name": "Emergency Fund",
  "target": "10000.00",
  "current": "1500.00",
  "target_date": "2027-12-31",
  "allocation_pct": 10,
  "notes": "Six months of expenses"
}
```

`allocation_pct` (0–100) turns the goal into a sinking fund: that percentage
of every recorded income deposit is automatically added to `current_cents`,
with a notification per allocation.

#### Contribute Payload

```json
{
  "amount": "250.00"
}
```

---

### Recurring Rules

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/recurring` | List recurring rules. |
| `POST` | `/api/recurring` | Create recurring rule. |
| `PUT` | `/api/recurring/<id>` | Update recurring rule. |
| `DELETE` | `/api/recurring/<id>` | Delete recurring rule. |
| `POST` | `/api/recurring/run` | Run due recurring rules. |

#### Supported Frequencies

```text
daily
weekly
monthly
yearly
```

#### Example Expense Rule Payload

```json
{
  "name": "Weekly allowance",
  "entity_type": "expense",
  "frequency": "weekly",
  "interval_value": 1,
  "next_run_date": "2026-07-01",
  "payload": {
    "title": "Allowance",
    "category": "Kids & Family",
    "amount": "20.00",
    "member": "Child"
  }
}
```

#### Example Bill Rule Payload

```json
{
  "name": "Monthly rent",
  "entity_type": "bill",
  "frequency": "monthly",
  "interval_value": 1,
  "next_run_date": "2026-07-01",
  "payload": {
    "payee_name": "Landlord",
    "bill_category": "Mortgage / Rent",
    "bill_date": "2026-07-01",
    "due_date": "2026-07-05",
    "items": [
      {
        "item_name": "Rent",
        "quantity": 1,
        "unit_price": "1200.00"
      }
    ]
  }
}
```

---

### Notifications

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/notifications` | List notifications. |
| `POST` | `/api/notifications/<id>/read` | Mark notification as read. |
| `POST` | `/api/notifications/read-all` | Mark all notifications as read. |

---

### Reports

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/reports` | Main annual report. |
| `GET` | `/api/reports/budget` | Budget vs actual report. |
| `GET` | `/api/reports/cash-flow` | Monthly cash flow report. |
| `GET` | `/api/reports/payees` | Payee payment report. |
| `GET` | `/api/reports/subscriptions` | Subscription-like bill report. |
| `GET` | `/api/reports/net-worth` | Account balances and net worth. |

---

### Reminders

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/reminders` | Overdue and upcoming bills. |

---

### Admin

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/backup` | Download SQLite backup. Admin only. |
| `GET` | `/api/admin/audit` | List audit logs. Admin only. |

---

## Reports

### Main Report

```text
GET /api/reports?year=2026
```

Returns:

- Monthly income
- Monthly expenses
- Monthly savings
- Savings rate
- Expense categories
- Income categories
- Bill categories
- Bill status breakdown
- Annual totals
- Available years

### Budget Report

```text
GET /api/reports/budget?period=monthly&month=2026-06
```

or:

```text
GET /api/reports/budget?period=yearly&year=2026
```

Returns budgeted amounts, actual amounts, remaining amounts, and usage percentages. Budgets that specify a `member` are compared against only that member's expenses for the period; household budgets (no member) count all expenses plus bill payments.

### Cash Flow Report

```text
GET /api/reports/cash-flow?months=6
```

Returns monthly:

- Income
- Expenses
- Bill payments
- Total outflow
- Savings
- Savings rate

### Forecast Report

```text
GET /api/reports/forecast?months=12
```

Forward-looking net-worth projection. When active recurring rules exist, the
projection blends the backward-looking 90-day averages with the normalized
monthly impact of those rules (50/50), and adds the outstanding balances of
already-issued unpaid bills to their due month as `scheduled_bills`. The
response includes `recurring_monthly_income`, `recurring_monthly_outflow`,
`recurring_rule_count`, and per-row `scheduled_bills` alongside the historical
baseline fields.

### Payee Report

```text
GET /api/reports/payees?year=2026
```

Returns payees grouped by bill payments.

### Subscription Report

```text
GET /api/reports/subscriptions
```

Returns recurring-like bills where category matches subscription, internet, or phone-related categories.

### Net Worth Report

```text
GET /api/reports/net-worth
```

Returns account balances and estimated net worth.

---

## Recurring Transactions

The recurring system is pull-based.

That means rules are processed when:

```http
POST /api/recurring/run
```

is called.

Each due rule is processed inside its own savepoint. If a rule's payload is invalid, that rule is skipped and reported in the response's `failed` array (with a notification raised), while all other rules continue to be processed normally. A rule that fails remains due, so the failure stays visible on every subsequent run until it is fixed or removed.

Example response:

```json
{
  "success": true,
  "created": [
    { "rule": "Monthly rent", "entity_type": "bill", "id": 12 }
  ],
  "failed": [
    { "rule": "Broken rule", "error": "title is required" }
  ]
}
```

You can automate this with:

- cron
- systemd timer
- a scheduled task
- an internal job runner
- a button in the UI

Example cron job:

```bash
0 6 * * * curl -b /path/to/cookies.txt -X POST http://localhost:5000/api/recurring/run
```

For a real production system, consider replacing this with an internal scheduler or authenticated service token.

---

## CSV Import

Bank statement CSVs can be imported into expenses or income from the
frontend (Expenses/Income → **Import CSV**) or via the API.

### Preview

```http
POST /api/import/csv/preview
Content-Type: multipart/form-data
Field: file (a .csv file)
```

Returns the detected `headers`, `sample_rows`, and `total_rows`.

### Commit

```http
POST /api/import/csv/commit
```

```json
{
  "target_type": "expense",
  "items": [
    {"title": "Grocery run", "amount": 42.5, "date": "06/22/2026", "category": "Other"}
  ]
}
```

Behavior:

- **Row isolation.** Every row is committed inside its own `SAVEPOINT`. A bad
  row fails alone and is reported in the response; it never aborts the rest
  of the file:

  ```json
  {
    "success": true,
    "imported_count": 48,
    "failed_rows": 1,
    "failed": [{"row": 17, "error": "date must be a valid date (YYYY-MM-DD or MM/DD/YYYY)"}],
    "total_rows": 49
  }
  ```

- **Flexible dates.** `YYYY-MM-DD`, `MM/DD/YYYY` (US), `DD/MM/YYYY`,
  `YYYY/MM/DD`, `DD.MM.YYYY`, and `MM-DD-YYYY` are accepted. Ambiguous values
  resolve US-first (`03/04/2026` → March 4); pick an explicit date format in
  the import dialog to remove any doubt.
- **Manual column mapping.** The frontend dialog shows your file's real
  headers with dropdowns to assign each target field (Date, Description,
  Amount, Category, Notes), a date-format picker, an optional account
  assignment, and a live preview of the first mapped rows before anything is
  committed.
- Amounts are imported as positive values; signs are ignored (bank exports
  mark debits either way).

---

## Data Model Notes

### Member references (`member_id`)

Expenses, income, and splits support both the legacy free-text `member` field
and a proper `member_id INTEGER REFERENCES members(id)`. Precedence:

- An explicit valid `member_id` wins and backfills the canonical member name.
- Otherwise a non-empty `member` string is matched **case-insensitively**
  against active members and linked when found — so "alice" links to "Alice".
- Unmatched strings are stored as display-only text (legacy behavior), which
  is why the column remains during the transition.

New records may only reference *active* members and accounts: referencing a
deactivated account/member returns a `400` ("…is inactive…"). Existing records
that point at since-deactivated rows keep rendering — that's why deletes are
soft.

### Duplicate prevention

Only one active rule per combination is accepted:

- One allowance per `(member_id, frequency)`.
- One active budget per `(category, member, period)`.

Attempts return a clean `400`; where the existing data allows, matching
partial UNIQUE indexes enforce this at the database level too.

### Multi-currency fields (informational)

Expenses/income accept optional `original_currency` (3-letter code),
`exchange_rate` (> 0, required when a currency is set), and `original_amount`
or `original_amount_cents`. The settled `amount_cents` always remains in the
household's primary currency; these fields preserve what actually happened in
the foreign currency for reference and future conversion features.

---

## Backups and Restore

### Backup via API

An admin user can download a backup:

```text
GET /api/admin/backup
```

This returns a SQLite backup file.

### Backup via filesystem

For best results, stop writes during backup or use SQLite backup tooling.

Example:

```bash
sqlite3 familyfinance.db ".backup /backups/familyfinance-$(date +%F-%H%M).db"
```

### Restore

Restore by replacing the database file or using SQLite restore:

```bash
sqlite3 familyfinance.db ".restore /backups/familyfinance-backup.db"
```

The `POST /api/admin/restore` endpoint (Admin only, multipart form field `file`) accepts a previously downloaded `.db` backup and restores it over the live database. Before swapping anything it verifies that the upload is a valid SQLite file containing the expected FamilyFinance tables (`users`, `settings`, `bills`, `expenses`), returning a `400` with a descriptive error otherwise.

If WAL mode is active, you may also see these files:

```text
familyfinance.db-wal
familyfinance.db-shm
```

Do not manually corrupt or partially copy them. Use proper SQLite backup methods.

---

## Security

### Password Hashing

Passwords are hashed using Werkzeug's password hashing utilities.

Never store plain-text passwords.

### Session Authentication

The enhanced version uses Flask sessions instead of sending credentials on every request.

Session cookies are configured with:

- `HttpOnly`
- `SameSite=Lax`
- Optional `Secure` when `FF_SECURE_COOKIES=1`

### CSRF Protection

Mutating API requests require a valid CSRF token.

Send it using:

```http
X-CSRF-Token: YOUR_CSRF_TOKEN
```

### Rate Limiting

The application includes simple in-memory rate limiting: a global per-IP API
guard, a strict per-IP login limiter, and a looser per-username login limiter
(so credential stuffing against one known account from many IPs is still
capped).

**Important:** the limiter state lives in one process. If you run multiple
worker processes (`gunicorn -w 4`, for example), each worker enforces its own
table and the effective limits are multiplied by the worker count — silently.
For production either:

- run a **single worker** (SQLite is the storage engine anyway; threads within
  one process scale fine for a household app),
- put rate limiting in your reverse proxy (Nginx `limit_req`, Cloudflare,
  Fail2ban), or
- replace the `RateLimiter` class with a shared-store implementation (Redis).

### Security Headers

The application sets:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy` (`default-src 'self'`, inline script/style allowed
  as a first step since the SPA uses inline handlers; Google Fonts origins are
  whitelisted for styles/fonts)
- Optional HSTS when enabled

### Backups Contain Credentials

`.db` backup files include the full database — including the
`users.password_hash` column. Treat them exactly like credentials:

- Download only over HTTPS.
- Encrypt at rest if stored off-server.
- Never commit them to version control (`.gitignore` already excludes them).
- After `POST /api/admin/restore`, **restart the FamilyFinance process** so
  every worker drops stale connections to pre-restore data; the endpoint's
  response includes `"restart_recommended": true` as a reminder.

### Production Security Checklist

- [ ] Set a strong `FF_SECRET_KEY`.
- [ ] Set a strong `FF_ADMIN_PASS`.
- [ ] Use HTTPS.
- [ ] Set `FF_SECURE_COOKIES=1`.
- [ ] Set `FF_HSTS=1` after HTTPS is verified.
- [ ] Do not run with `FF_DEBUG=1`.
- [ ] Use a production WSGI server.
- [ ] Restrict access to `/api/admin/backup`.
- [ ] Schedule regular backups.
- [ ] Monitor logs.
- [ ] Keep Python and dependencies updated.

---

## Testing

The test suite lives at:

```text
tests/test_app.py
```

install pytest:

```bash
pip install pytest
```

Run:

```bash
pytest -q
```

Coverage includes:

- Health, login, CSRF enforcement, roles, and user management
- Full expense/income/bill/budget/goal lifecycles including splits
- Delete-endpoint 404 semantics for all soft/hard delete routes
- Partial updates preserving transaction splits
- Payee validation (invalid `payee_id` with `payee_name` → 400) and recurring payee resolution
- CSV import row isolation and flexible date formats
- Split-aware budget reports and dashboard categories
- Weekly allowance monthly-equivalent math
- Debt payoff cascade, rollover, zero-minimum handling, and non-payoff flagging
- Concurrent bill payments cannot overpay a bill (threaded race test)
- Personal access token create/use/revoke flow
- Bill payment list & reversal with automatic status recomputation
- Duplicate allowance/budget rejection and inactive-reference validation
- Goal auto-allocation, multi-currency fields, and the recurring-aware forecast

---

## Production Deployment

Do not use the Flask development server in production.

Use a WSGI server such as:

- Gunicorn
- uWSGI
- Waitress

### Example with Gunicorn

Install:

```bash
pip install gunicorn
```

Run:

```bash
gunicorn -b 0.0.0.0:5000 "app:create_app()"
```

> **Worker count:** keep one worker unless you move rate limiting to shared
> storage — the built-in limiter is per-process, so `-w 4` silently weakens
> it 4×. SQLite handles this app's concurrency fine through threads within a
> single worker.

With environment variables:

```bash
FF_SECRET_KEY="long-random-secret" \
FF_ADMIN_PASS="strong-password" \
FF_SECURE_COOKIES="1" \
FF_HSTS="1" \
gunicorn -b 0.0.0.0:5000 "app:create_app()"
```

### systemd Example

Create:

```text
/etc/systemd/system/familyfinance.service
```

```ini
[Unit]
Description=FamilyFinance
After=network.target

[Service]
User=familyfinance
WorkingDirectory=/opt/familyfinance
Environment="FF_SECRET_KEY=long-random-secret"
Environment="FF_ADMIN_PASS=strong-password"
Environment="FF_DB_PATH=/var/lib/familyfinance/familyfinance.db"
Environment="FF_SECURE_COOKIES=1"
Environment="FF_HSTS=1"
ExecStart=/opt/familyfinance/.venv/bin/gunicorn -b 127.0.0.1:5000 "app:create_app()"
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable familyfinance
sudo systemctl start familyfinance
```

### Reverse Proxy

Use Nginx, Caddy, Apache, or another reverse proxy in front of the application.

Example Nginx location block:

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Always terminate TLS at the reverse proxy when using `FF_SECURE_COOKIES=1`.

---

## Docker

You can containerize FamilyFinance.

Example `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:create_app()"]
```

Example `requirements.txt`:

```text
flask
gunicorn
```

Build:

```bash
docker build -t familyfinance .
```

Run:

```bash
docker run --rm -it \
  -p 5000:5000 \
  -e FF_SECRET_KEY="change-me" \
  -e FF_ADMIN_PASS="strong-password" \
  -v familyfinance-data:/app/data \
  familyfinance
```

If using a custom database path, ensure the container volume matches `FF_DB_PATH`.

---

## Troubleshooting

### The app asks for CSRF token

All mutating API calls require a CSRF token.

Log in first:

```http
POST /api/auth/login
```

Then send:

```http
X-CSRF-Token: token-from-login-response
```

---

### I get `401 Authentication required`

Your session is missing or expired.

Log in again:

```http
POST /api/auth/login
```

Make sure your HTTP client stores cookies.

---

### I get `403 CSRF validation failed`

You are authenticated, but the CSRF token is missing or incorrect.

Use the token from:

- login response
- `/api/me`
- `/api/csrf`

Send it as:

```http
X-CSRF-Token: YOUR_CSRF_TOKEN
```

---

### I lost the admin password

If you have not saved it, reset it by deleting or modifying the database user manually.

For development, you can delete the database file and restart:

```bash
rm familyfinance.db
python app.py
```

For production, use database administration carefully:

```sql
DELETE FROM users WHERE username='admin';
```

Then restart the app and allow it to recreate the admin user.

---

### The database is locked

SQLite may return lock errors under heavy concurrent write load.

Mitigations:

- Use WAL mode, already enabled.
- Avoid multiple processes writing heavily.
- Use a Postgres-compatible database layer for heavier multi-user deployments.
- Keep transactions short.

---

### I do not see my frontend

The app tries to render:

```text
templates/index.html
```

If that file does not exist, the minimal API homepage is shown.

---

### The app generates a new admin password on every startup

This only happens if:

- The database is new
- The admin user does not already exist
- `FF_ADMIN_PASS` is not set

To avoid this, set:

```bash
FF_ADMIN_PASS="your-strong-password"
```

before first startup.

---

### Money values look different from the original version

The enhanced version stores money as integer cents internally.

Example:

```json
{
  "amount": 42.50,
  "amount_cents": 4250
}
```

This improves rounding accuracy.

---

## Project Structure

A typical project layout:

```text
familyfinance/
├── app.py
├── README.md
├── requirements.txt
├── familyfinance.db
├── templates/
│   └── index.html
├── static/
│   ├── css/
│   ├── js/
│   └── images/
└── tests/
    └── test_app.py
```

If you expand the project further, a more modular structure is recommended:

```text
familyfinance/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── models/
│   ├── services/
│   ├── repositories/
│   ├── api/
│   ├── templates/
│   └── static/
├── migrations/
├── tests/
├── requirements.txt
└── README.md
```

---

## Roadmap

Potential future enhancements:

- [x] Full frontend dashboard
- [x] Bank statement CSV import (row-isolated, with column mapping)
- [x] Split transactions
- [x] Debt payoff planner
- [x] Cash flow forecasting (recurring-rule aware)
- [x] Allowance tracking for children
- [x] API tokens
- [x] Backup restore UI
- [ ] OFX/QIF import
- [ ] Receipt image upload
- [ ] OCR receipt extraction
- [ ] Email notifications
- [ ] Browser push notifications
- [ ] Multi-currency exchange-rate lookup (fields are stored; automatic rate fetching is not)
- [ ] Mobile-first UI
- [ ] Progressive Web App support
- [ ] PostgreSQL support
- [ ] Alembic migrations
- [ ] OpenAPI documentation
- [ ] Two-factor authentication
- [ ] Family invitations and approvals
- [ ] Scheduled automatic recurring execution
- [ ] Full audit log viewer UI

---

## Contributing

Contributions are welcome.

When contributing:

1. Keep changes focused.
2. Preserve backward compatibility where practical.
3. Add tests for new behavior.
4. Update documentation.
5. Avoid exposing secrets or personal data.
6. Use clear commit messages.

Suggested development workflow:

```bash
git checkout -b feature/my-improvement
# make changes
pytest -q
git commit -m "Add feature"
git push origin feature/my-improvement
```

---

## License

This project is provided as-is for personal and internal use unless a separate license is included in the repository.

If you plan to distribute it, add a license file such as:

- MIT
- Apache 2.0
- GPL 3.0
- Proprietary / All Rights Reserved

---

## Disclaimer

FamilyFinance is a budgeting and record-keeping tool. It does not provide professional financial advice, tax advice, or investment advice. Always verify important financial decisions with a qualified professional.
```
