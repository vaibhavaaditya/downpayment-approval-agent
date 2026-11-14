# What changed in this merge

Base: the existing `downpayment-approval-agent` (typed Pydantic models, LangGraph pipeline, real
dependencies, CI). Ported in: the strongest ideas from the second implementation.

## Controls (src/dpa_agent/tools, guardrails, config)
- NEW checks: cumulative down-payment cap, PO status, vendor/PO match, bank-change recency,
  watchlist enforcement (policy POL-RSK-004 existed but was not enforced). 6 -> 11 checks.
- Bank identifiers are masked in evidence, prompts, API responses and the audit log.
- Injection tripwire widened (and scans the requester `purpose` as well).
- Guardrail: a model-raised `injection_suspected` flag forces ESCALATE (the model can only tighten).
- Evaluation is "as of" the request date, not `date.today()` (deterministic replays).

## LLM path (src/dpa_agent/llm, graph)
- Bounded tool loop (`search_policy`, `get_payment_history` scoped to the PO's vendor), then a
  forced schema-validated submission. Output gains `injection_suspected`, risk flags, vendor questions.
- System prompt now states that vendor text is untrusted; vendor text is delimited in the prompt.
  (The old doc claimed this; the old prompt did not do it.)
- Policy retrieval no longer skips when all checks pass: security clauses are always shown.
- Conditional edge now routes "SAP data missing" straight to the guardrail (no model call).
- Payment-history load failures fail closed instead of crashing.
- Default model id updated; override with ANTHROPIC_MODEL.

## Human step and audit (src/dpa_agent/decision.py, authority.py, audit.py, api)
- Four-eyes, approval bands per currency, written override reason; validated against the audited
  recommendation. Hash-chained audit log with `verify()` and `GET /api/v1/audit/verify`.

## SAP layer (src/dpa_agent/sap)
- The old HTTP client called routes the app never served. Replaced by `ODataSapClient` plus a real
  standard-library mock server (`python -m dpa_agent.sap.mock_odata`) with V2 envelope and
  `/Date(ms)/` quirks; tested for parity with the in-memory client. `HttpSapClient` kept as an alias.
- Three new demo requests (recent bank change, subtle injection, executive pressure).

## Evaluation (evals, scripts)
- 7 -> 40 cases, 8 of them `llm_only` (every check passes; only a model can catch them).
  Metrics: unsafe approvals, accuracy, over-escalation, guardrail overrules, per-category table.
- CI gate now pins the offline baseline (its 8 blind spots are expected and measured), plus a
  manual workflow to run the same suite with Claude.

## Tests
- 47 -> 100 tests. New: audit chain, decision rules, OData round trip, Claude tool loop (fake
  client, through the real graph), policy consistency, new controls, API (needs FastAPI).

## Docs
- README, architecture (new threat model with residual risks and known gaps), SAP doc fixes.
