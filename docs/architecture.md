# Architecture notes

## The split that makes this work

| Layer | Runs where | Handles | Failure mode |
|---|---|---|---|
| Deterministic checks (11) | Python, Pydantic-typed | amounts, caps (single and cumulative), duplicates, PO status, vendor/PO match, bank match and bank-change recency, vendor hold, watchlist, instruction tripwire | explicit `FAIL` + evidence |
| Policy retrieval | BM25 over clause-ID'd markdown | which policy clauses the model sees; security clauses are **always** included | empty lexical hits → model still sees the mandatory clauses and the checks |
| Recommendation | Claude (bounded tool loop, forced final answer) | judgment over unstructured vendor text, rationale, weighing warnings | schema failure or API error → guardrail default `HOLD` |
| Guardrails | Python, after the model | label authority, citation validity, caps; the model may only tighten | always runs, cannot be skipped |
| Audit | Hash-chained JSONL | full evidence trail per decision; tamper-evident | write failure surfaced in `errors` |
| Human decision | Person + API decision endpoint | the actual approval, under four-eyes, authority and override-reason rules | agent has no write path to SAP |

This is the standard "LLM behind a deterministic perimeter" pattern: the model
never owns a number, never owns a control, and never owns the final say.

## Invariants (enforced in code, not conventions)

1. **Schema-validated contracts everywhere** — Pydantic models at every boundary:
   SAP payloads, check outputs, model drafts, final recommendations.
2. **Failed HARD check => no APPROVE** — `guardrails.apply_guardrails`, rule 2.
3. **Fraud-relevant failures => forced ESCALATE** — bank mismatch, recently changed bank
   details, instruction injection, vendor/PO mismatch, watchlist vendor
   (`tools.ESCALATE_ON_FAILURE`).
4. **The model can only tighten.** If it flags an injection attempt, the label is forced to
   ESCALATE; nothing it says can remove a control.
5. **WARN or errors or missing checks => no APPROVE** — rules 3–4.
6. **Citations must exist in the evidence pool** — unknown IDs are dropped; failed checks'
   evidence is force-cited. Clauses the model fetched with its own tool join the pool.
7. **No payment action exists in the tool surface, and the SAP client has no write method.**
8. **Authority is enforced on the human step** — requester cannot decide their own request;
   approval needs the role the amount requires; approving against the agent needs a written
   reason. Validated against the *audited* recommendation, not the request payload.
9. **History cannot be edited silently** — each audit record hashes the previous one.

## LangGraph state machine

```
START -> load_context --(ok)--> run_checks -> retrieve_policy -> propose -> guardrail -> record_audit -> END
              |                                                              ^
              +--(SAP data missing)------------------------------------------+
```

* One conditional edge: if SAP data could not be loaded there is nothing to check and no
  reason to spend a model call, so the request goes straight to the guardrail, which turns an
  incomplete evaluation into `HOLD`.
* An earlier design skipped policy retrieval when every check passed. That created a hole: a
  *subtle* injection leaves no failed check, so the model would see neither the security
  clauses nor anything warning it about untrusted text. Retrieval now always runs and always
  includes `POL-SEC-001`, `POL-SEC-004` and `POL-PAY-007`.
* `propose` is the only probabilistic node; every other node is pure code.
* Node functions receive injected dependencies (SAP client, recommender, store, audit sink),
  so the whole graph runs in tests with zero network.

## What the model sees, and may do

* **Shown:** the request, PO, vendor master and payment history, the check results (marked
  authoritative), and policy clauses with stable IDs. Bank identifiers are **masked** to the
  last four characters, in the prompt, in evidence text, in API responses and in the audit log.
* **Vendor text** is wrapped in `<vendor_document>` tags and the system prompt states that it
  is data, never instructions.
* **Tools:** `search_policy` and `get_payment_history` (scoped to the vendor on the PO), for a
  bounded number of rounds; then a forced `submit_recommendation` call returns a
  schema-validated object. Tool failures are reported to the model, never raised.
* **Output:** label, rationale, citations, `injection_suspected`, risk flags, questions for the
  vendor.

## Why the fallback recommender exists

`FallbackRecommender` maps checks to labels deterministically. It is:

* the CI engine (no secrets needed),
* **a baseline the LLM must beat**: it cannot read vendor text, so it approves the eval cases
  whose only signal is in the text (`llm_only`). Its unsafe approvals on those cases are the
  measurement of what the model has to add,
* the degradation path if the API is down.

If the fallback and the model disagree, the guardrail result is the same: the perimeter, not
the model, defines what is allowed.

## Evaluation design

40 synthetic cases in 18 categories. 32 are **rule-detectable**: the deterministic checks and
guardrails must get every one right (enforced by `tests/test_eval.py`, and even a model that
approves everything cannot leak an unsafe approval on them). 8 are **`llm_only`**: every check
passes, and the signal is in the vendor text (reviewer-directed pre-clearance claims, vague
justification, executive pressure, a different PO or amount). Headline metric: unsafe
approvals. Secondary: accuracy, over-escalation of clean requests (so "escalate everything"
does not look good), guardrail overrules.

## Threat model

| Threat | Defence | Residual risk |
|---|---|---|
| Explicit prompt injection in vendor text | regex tripwire (HARD, forces ESCALATE) | patterns are incomplete by nature |
| Subtle injection (no known pattern) | model told the text is untrusted, always shown the security clauses, can flag `injection_suspected`, which forces ESCALATE | **depends on the model**; no code can see it. This is why the eval has dedicated cases |
| Model emits APPROVE against failed controls | guardrail caps by check status | none for rule-detectable cases |
| Model arithmetic error | checks compute; the model only reasons and cites | none |
| Invented citations | validated against the evidence pool and stripped | low |
| Over-privileged agent | read-only client; tools scoped to the PO's vendor; no write path | low |
| Sensitive data leaving the process | bank identifiers masked everywhere they leave the code | other free text is still sent to the model |
| Silent failure | audit line for every run; errors surfaced; model/API failure fails closed to HOLD | audit store is a local file in this repo |
| Tampering with history | hash chain, verified by `/api/v1/audit/verify` | tamper-evident, not tamper-proof |
| Self-approval or authority bypass | four-eyes and approval bands enforced on the audited recommendation | role claims are not authenticated here (see known gaps) |
| OData filter/path injection | keys validated before they reach a request | low |

## Known gaps (deliberate, and the first things a real project would change)

* The API has **no authentication**: `decided_by` and `approver_role` are caller-supplied.
  A real deployment takes identity and role from the identity provider.
* Approval bands are fixed per currency, a simplification of FX conversion.
* The bank-change check trusts the vendor master's change date; with no date it passes.
* Lexical (BM25) retrieval over a small corpus; embeddings would replace it as it grows.
* The human step is a recorded decision, not a durable paused workflow.
