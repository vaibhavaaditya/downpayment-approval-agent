# SAP integration: portfolio vs. production

The portfolio project mocks SAP on purpose. This document is the honest answer
to three questions: *where does the data come from?*, *what triggers the agent?*,
and *what changes in a real project?*

## 1. How the agent gets data from SAP

### In this repo

`src/dpa_agent/sap/client.py` defines one read-only interface (`SapClient`) with two
implementations: an in-memory mock and `ODataSapClient`, which speaks to an OData-style
HTTP service. The bundled mock server (`python -m dpa_agent.sap.mock_odata`) serves the same
data in an OData V2 dialect: `d` envelope, `/Date(ms)/` dates, decimals as strings, SAP-style
field names and error bodies. Swapping implementations is a config value (`SAP_ODATA_BASE_URL`).

What this does and does not prove: the client's mapping layer (the anti-corruption layer) is
exactly the work a real adapter does, and `tests/test_sap_odata.py` checks that the HTTP path
returns the same domain objects, and the same decisions, as the in-memory path. It does **not**
prove compatibility with real S/4HANA payloads: the entity shapes are simplified and only
loosely modelled on S/4HANA API entities.

SAP also offers a public API sandbox (free API key, read calls) that is useful for testing
payload shapes without owning a system; check its current availability before relying on it.

### In a real S/4HANA project

| Data the agent needs | Real source | Notes |
|---|---|---|
| Down-payment requests | **Custom CDS view / custom OData service** on special-GL (BSEG/BKPF) requests, or a wrapper around the Fiori "Down Payment Request" app | S/4HANA has no standard *queue* API for pending DP approvals — the request object is usually custom or workflow-backed |
| Purchase orders | Released API `A_PurchaseOrder` / `A_PurchaseOrderItem` (OData, API Business Hub) | standard, versioned |
| Vendor master + bank | `A_Supplier` plus the business-partner bank data in the Business Partner API (verify the exact entity names for your release in API Business Hub) | **bank data is the fraud-sensitive field** — read-only, access-logged. The bank-change **date** (change documents) feeds the `bank_recent_change` check |
| Payment history / prior DPs | CDS on BSEG (special GL indicator), or `A_AccountingDocument` | often needs a purpose-built CDS view for performance |
| Policy documents | SharePoint/ServiceNow/wiki → your RAG store | keep clause IDs stable across systems |

Transport path in production: **S/4HANA → SAP Integration Suite (Cloud
Integration) or API Management → your agent's HTTPS endpoint**, with OAuth2
client credentials (or principal propagation for user-context reads), mTLS at
the edge, and IP allowlists. On-prem S/4HANA reaches the cloud through the
**SAP Cloud Connector**. Your agent never talks to the SAP application server
directly.

## 2. What triggers the approval review

The trigger has to live in SAP's own workflow world, because that is where the
approver already is:

1. **SAP Build Process Automation / My Inbox** — the requester files the DP
   request in Fiori; a workflow item is created; the workflow's first step calls
   your agent's `POST /api/v1/evaluate` (technical user, OAuth2), receives the
   recommendation + citations, and renders them in the approver's inbox item.
   The approver acts *inside Fiori*; your agent never owns the decision UI.
2. **Event-driven** — publish `DownPaymentRequestCreated` via SAP Event Mesh /
   Integration Suite eventing → agent evaluates → posts the recommendation back
   as a workflow comment or custom field. Good for async/bulk reviews.
3. **Polling / job** — simplest, least real-time: scheduled pull of pending
   requests, results written back for the Fiori app to display.

Write-back is **advisory only**: a comment, a custom `Z`-field, or a workflow
attribute — never a posting. The actual release stays a human action in SAP
(the approver's "approve" drives the existing payment run / F-110 proposal).

## 3. Portfolio project vs. legitimate project

The **agent code is largely the same** — same checks, same guardrails, same
graph. What differs is the perimeter around it:

| Dimension | Portfolio (this repo) | Legitimate production project |
|---|---|---|
| Data | In-memory mock, synthetic cases | Released OData/CDS APIs via Integration Suite/API Management; Cloud Connector for on-prem; delta queries, pagination, retries |
| Auth | None | OAuth2 client credentials, principal propagation, service accounts with least privilege, secrets in BTP destination / vault |
| Trigger | curl / your own UI | SAP Build Process Automation, My Inbox, Event Mesh — approver never leaves Fiori |
| Write-back | Audit JSONL only | Workflow comment / Z-field via API; posting stays in SAP; four-eyes preserved |
| Security & compliance | Documented intent | SOX controls, evidence retention (7–10 yrs), vendor bank data redaction, zero-data-retention agreement with the model provider (or on-prem/VPC model), DPIA |
| Evaluation | ~7 synthetic cases + CI gate | Golden set built from anonymized historical decisions; **shadow mode** first (recommend in parallel, humans decide, compare); canary by company code; measured false-approve rate with sign-off |
| Reliability | Fallback recommender | SLAs, timeouts, circuit breakers, degraded mode (deterministic checks only → HOLD), cost budgets per 1k requests |
| Model governance | Model name in env | Approved model list, prompt versioning + review, drift monitoring, incident runbook, EU AI Act / internal AI governance sign-off |
| Landscape | `uvicorn` locally | DEV→QAS→PROD transports, SAP regression around the custom CDS view, monitoring via SAP Focused Run / alerting on agent errors |
| Scale | One process | Stateful audit store (not a local JSONL), idempotent evaluations, correlation IDs end-to-end with SAP message logs |

### The punchline

The interesting engineering — deterministic checks, guardrails, evidence
citations, HITL-by-construction — **transfers 1:1**. What a real project adds
is everything the mock abstracts away: integration auth, SAP-side workflow
embedding, compliance evidence, and a much more serious evaluation program
before a single recommendation influences real money.

## 4. Build order if you took this to a real system

1. Stand up the custom CDS view for DP requests + released PO/vendor APIs in a
   QAS landscape; implement the real adapter (the mapping layer of `ODataSapClient`) against them behind Integration Suite.
2. Wrap evaluation in a Build Process Automation step; render output in My Inbox.
3. Run **shadow mode** for a quarter: agent recommends, humans decide anyway,
   disagreements are reviewed by the control owner.
4. Gate on metrics: unsafe-approval rate (must be 0 against the golden set, including the cases only a model can catch),
   override rate, citation validity.
5. Only then let the recommendation pre-fill the approver's decision — still
   human, still in SAP, still logged.
