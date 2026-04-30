"""
Odoo + Payment Mock Service
===========================
Simulates Odoo ERP and a payment gateway for the WatsonX Orchestrate demo.
Use Case 3: Automated Data Transfer from PDF Disbursement Letters.

Run:
    uvicorn main:app --reload --port 8000

Endpoints:
    GET  /dashboard                          — live entry dashboard (open in browser)
    POST /pdf/extract                        — extract fields from uploaded PDF
    POST /odoo/entries                       — create draft entry
    GET  /odoo/entries                       — list entries
    GET  /odoo/entries/{id}                  — get single entry
    POST /odoo/entries/{id}/approve          — approve entry
    POST /odoo/entries/{id}/reject           — reject entry
    POST /payments/initiate?entry_id={id}    — initiate payment

OpenAPI spec:
    http://localhost:8000/openapi.json       — import into WatsonX Orchestrate
"""

import io
import re
import uuid
import datetime
from typing import Optional, List

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Odoo Mock Service",
    description=(
        "Mock service simulating Odoo ERP and a payment gateway "
        "for the WatsonX Orchestrate Use Case 3 demo."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory store ─────────────────────────────────────────────────────────

_entries: dict[str, dict] = {}
_payments: dict[str, dict] = {}
_activity_log: list[dict] = []  # last 50 events shown on dashboard


def _log(event: str, detail: str) -> None:
    _activity_log.append(
        {
            "time": datetime.datetime.utcnow().strftime("%H:%M:%S"),
            "event": event,
            "detail": detail,
        }
    )
    if len(_activity_log) > 50:
        _activity_log.pop(0)


# ─── Models ──────────────────────────────────────────────────────────────────


class ExtractedData(BaseModel):
    landowner_name: str = Field(..., description="Full name of the landowner")
    landowner_address: str = Field(..., description="Full address of the landowner")
    contract_type: str = Field(..., description="e.g. Leitungsrecht, Kabelrecht")
    amount: float = Field(..., description="Payment amount in EUR")
    iban: str = Field(..., description="Landowner IBAN (no spaces)")
    bic: str = Field(..., description="Landowner bank BIC")
    reference_number: str = Field(..., description="Internal reference number")
    due_date: str = Field(..., description="Payment due date (YYYY-MM-DD)")


class CreateEntryRequest(BaseModel):
    landowner_name: str
    landowner_address: str
    contract_type: str
    amount: float = Field(..., gt=0)
    iban: str
    bic: str
    reference_number: str
    due_date: str


class DisbursementEntry(BaseModel):
    id: str
    landowner_name: str
    landowner_address: str
    contract_type: str
    amount: float
    iban: str
    bic: str
    reference_number: str
    due_date: str
    status: str = Field(..., description="draft | approved | rejected | paid")
    created_at: str
    rejection_reason: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str = Field(..., description="Reason for rejection")


class PaymentConfirmation(BaseModel):
    transaction_id: str
    entry_id: str
    amount: float
    iban: str
    status: str
    initiated_at: str
    message: str


# ─── PDF helpers ─────────────────────────────────────────────────────────────


def _find(text: str, *patterns: str) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def _parse_amount(raw: str) -> float:
    raw = raw.replace("EUR", "").replace("€", "").strip()
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _parse_date(raw: str) -> str:
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", raw.strip())
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return raw.strip()


# ─── Dashboard ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Auszahlungsverwaltung - ODOO</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f0f4f8; color: #1a202c; }
  header { background: #1d4ed8; color: white; padding: 18px 32px;
           display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 20px; font-weight: 600; }
  header .sub { font-size: 13px; opacity: 0.75; }
  .pulse { width: 10px; height: 10px; border-radius: 50%; background: #4ade80;
           animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  main { padding: 28px 32px; display: grid; grid-template-columns: 1fr 340px; gap: 24px; }

  /* entries table */
  .card { background: white; border-radius: 10px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); overflow: hidden; }
  .card-header { padding: 14px 20px; border-bottom: 1px solid #e2e8f0;
                 font-weight: 600; font-size: 14px; color: #374151; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { background: #f8fafc; padding: 10px 14px; text-align: left;
       font-weight: 600; color: #6b7280; border-bottom: 1px solid #e2e8f0; }
  td { padding: 11px 14px; border-bottom: 1px solid #f1f5f9; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f8fafc; }
  .empty { text-align: center; color: #9ca3af; padding: 40px 0; font-size: 14px; }
  code { font-family: 'SF Mono', monospace; font-size: 12px;
         background: #f1f5f9; padding: 2px 6px; border-radius: 4px; }

  /* badges */
  .badge { padding: 2px 10px; border-radius: 10px; font-size: 11px;
           font-weight: 700; text-transform: uppercase; letter-spacing: .4px; }
  .draft    { background: #fef3c7; color: #92400e; }
  .approved { background: #d1fae5; color: #065f46; }
  .rejected { background: #fee2e2; color: #991b1b; }
  .paid     { background: #dbeafe; color: #1e40af; }

  /* activity log */
  .log { display: flex; flex-direction: column; gap: 0; }
  .log-entry { padding: 10px 14px; border-bottom: 1px solid #f1f5f9;
               font-size: 12px; line-height: 1.5; }
  .log-entry:last-child { border-bottom: none; }
  .log-time { color: #9ca3af; font-family: monospace; margin-right: 8px; }
  .log-event { font-weight: 600; color: #1d4ed8; }
  .log-detail { color: #4b5563; }
  .log-empty { text-align: center; color: #9ca3af; padding: 30px 0; font-size: 13px; }

  footer { text-align: center; padding: 12px; font-size: 11px; color: #9ca3af; }
</style>
</head>
<body>
<header>
  <div class="pulse"></div>
  <div>
    <h1>Odoo Mock — Auszahlungs-Dashboard</h1>
    <div class="sub">Automatische Aktualisierung alle 3s &nbsp;·&nbsp; Letzte Aktualisierung: <span id="ts">—</span></div>
  </div>
</header>
<main>
  <div class="card">
    <div class="card-header">Auszahlungseinträge</div>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>Grundeigentümer</th><th>Vertrag</th>
          <th>Betrag</th><th>Fälligkeitsdatum</th><th>Status</th>
        </tr>
      </thead>
      <tbody id="entries"></tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-header">Aktivitätsprotokoll</div>
    <div class="log" id="log"></div>
  </div>
</main>
<footer>WatsonX Orchestrate Demo</footer>

<script>
async function refresh() {
  const [entriesRes, logRes] = await Promise.all([
    fetch('/odoo/entries'),
    fetch('/log'),
  ]);
  const entries = await entriesRes.json();
  const log     = await logRes.json();

  const tbody = document.getElementById('entries');
  tbody.innerHTML = entries.length === 0
    ? '<tr><td colspan="6" class="empty">Noch keine Einträge — warte auf den Agenten...</td></tr>'
    : entries.slice().reverse().map(e => `
        <tr>
          <td><code>${e.id}</code></td>
          <td>${e.landowner_name}</td>
          <td>${e.contract_type}</td>
          <td>€${e.amount.toFixed(2)}</td>
          <td>${e.due_date}</td>
          <td><span class="badge ${e.status}">${e.status}</span></td>
        </tr>`).join('');

  const logEl = document.getElementById('log');
  logEl.innerHTML = log.length === 0
    ? '<div class="log-empty">Noch keine Aktivität.</div>'
    : log.slice().reverse().map(l => `
        <div class="log-entry">
          <span class="log-time">${l.time}</span>
          <span class="log-event">${l.event}</span>
          <span class="log-detail"> — ${l.detail}</span>
        </div>`).join('');

  document.getElementById('ts').textContent = new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    """Live dashboard showing all disbursement entries and activity log."""
    return DASHBOARD_HTML


@app.get("/log", include_in_schema=False)
def get_log():
    """Internal endpoint polled by the dashboard."""
    return _activity_log



@app.post(
    "/odoo/entries",
    response_model=DisbursementEntry,
    status_code=201,
    summary="Create a draft disbursement entry",
    tags=["Odoo"],
)
def create_entry(data: CreateEntryRequest):
    """
    Create a new disbursement entry in Odoo with status 'draft'.
    Returns the created entry including its generated ID.
    """
    entry_id = f"DR-{uuid.uuid4().hex[:6].upper()}"
    entry = {
        "id": entry_id,
        "status": "draft",
        "created_at": datetime.datetime.utcnow().isoformat(),
        "rejection_reason": None,
        **data.model_dump(),
    }
    _entries[entry_id] = entry
    _log("Entry created", f"{entry_id} · {data.landowner_name} · €{data.amount:.2f}")
    return entry


@app.get(
    "/odoo/entries",
    response_model=List[DisbursementEntry],
    summary="List disbursement entries",
    tags=["Odoo"],
)
def list_entries(
    status: Optional[str] = Query(
        None, description="Filter by status: draft | approved | rejected | paid"
    ),
):
    """Return all disbursement entries, optionally filtered by status."""
    result = list(_entries.values())
    if status:
        result = [e for e in result if e["status"] == status]
    return result


@app.get(
    "/odoo/entries/{entry_id}",
    response_model=DisbursementEntry,
    summary="Get a disbursement entry by ID",
    tags=["Odoo"],
)
def get_entry(entry_id: str):
    """Retrieve a single disbursement entry by its ID."""
    if entry_id not in _entries:
        raise HTTPException(status_code=404, detail=f"Entry '{entry_id}' not found.")
    return _entries[entry_id]


@app.post(
    "/odoo/entries/{entry_id}/approve",
    response_model=DisbursementEntry,
    summary="Approve a disbursement entry",
    tags=["Odoo"],
)
def approve_entry(entry_id: str):
    """
    Approve a draft disbursement entry.
    Only entries in 'draft' status can be approved.
    """
    if entry_id not in _entries:
        raise HTTPException(status_code=404, detail=f"Entry '{entry_id}' not found.")
    entry = _entries[entry_id]
    if entry["status"] != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve: entry is '{entry['status']}', expected 'draft'.",
        )
    entry["status"] = "approved"
    _log("Entry approved", f"{entry_id} · {entry['landowner_name']}")
    return entry


@app.post(
    "/odoo/entries/{entry_id}/reject",
    response_model=DisbursementEntry,
    summary="Reject a disbursement entry",
    tags=["Odoo"],
)
def reject_entry(entry_id: str, body: RejectRequest):
    """
    Reject a draft disbursement entry and record the reason.
    Only entries in 'draft' status can be rejected.
    """
    if entry_id not in _entries:
        raise HTTPException(status_code=404, detail=f"Entry '{entry_id}' not found.")
    entry = _entries[entry_id]
    if entry["status"] != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject: entry is '{entry['status']}', expected 'draft'.",
        )
    entry["status"] = "rejected"
    entry["rejection_reason"] = body.reason
    _log("Entry rejected", f"{entry_id} · {entry['landowner_name']} · {body.reason}")
    return entry


# ─── Routes: Payments ─────────────────────────────────────────────────────────


@app.post(
    "/payments/initiate",
    response_model=PaymentConfirmation,
    summary="Initiate a bank transfer for an approved entry",
    tags=["Payments"],
)
def initiate_payment(
    entry_id: str = Query(..., description="ID of the approved disbursement entry"),
):
    """
    Initiate a bank transfer for an approved disbursement entry.
    Entry must have status 'approved'. On success, entry status is set to 'paid'.
    Returns a transaction ID and confirmation message.
    """
    if entry_id not in _entries:
        raise HTTPException(status_code=404, detail=f"Entry '{entry_id}' not found.")
    entry = _entries[entry_id]
    if entry["status"] != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Entry must be 'approved' to initiate payment. Current: '{entry['status']}'.",
        )

    txn_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    now    = datetime.datetime.utcnow().isoformat()

    payment = {
        "transaction_id": txn_id,
        "entry_id":       entry_id,
        "amount":         entry["amount"],
        "iban":           entry["iban"],
        "status":         "initiated",
        "initiated_at":   now,
        "message": (
            f"Payment of €{entry['amount']:.2f} to {entry['landowner_name']} "
            f"(IBAN: {entry['iban']}) successfully initiated. "
            f"Reference: {entry['reference_number']}."
        ),
    }
    _payments[txn_id] = payment
    entry["status"] = "paid"
    _log("Payment initiated", f"{txn_id} · {entry['landowner_name']} · €{entry['amount']:.2f}")
    return payment
