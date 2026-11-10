# Architecture notes

## The split that makes this work

| Layer | Runs where | Handles | Failure mode |
|---|---|---|---|
| Deterministic checks | Python, Pydantic-typed | amounts, caps, duplicates, bank matching, injection regexes | explicit `FAIL` + evidence |
| Policy RAG | BM25 over clause-ID'd markdown | which policy clauses are relevant | empty retrieval → model still sees checks |
| Recommendation | Claude (tool-forced JSON) | judgment, rationale, weighing warnings | schema failure → guardrail default `HOLD` |
| Guardrails | Python, after the model | label authority, citation validity, caps | always runs, cannot be skipped |
| Audit | Append-only JSONL | full evidence trail per decision | write failure surfaced in `errors` |
| HITL | Human + API decision endpoint | the actual approval | agent has no write path to SAP |

This is the standard "LLM behind a deterministic perimeter" pattern: the model
never owns a number, never owns a control, and never owns the final say.

## Invariants (enforced in code, not conventions)

1. **Schema-validated contracts everywhere** — Pydantic models at every boundary:
   SAP payloads, check outputs, model drafts, final recommendations.
2. **Failed HARD check ⇒ no APPROVE** — `guardrails.apply_guardrails`, rule 2.
3. **Bank mismatch / injection ⇒ forced ESCALATE** — same function, same rule.
4. **WARN or errors or missing checks ⇒ no APPROVE** — rules 3–4.
5. **Citations must exist in the evidence pool** — unknown IDs are dropped;
   failed checks' evidence is force-cited.
6. **No payment action exists in the tool surface** — the agent *cannot* post,
   release, or approve; it can only recommend.

## LangGraph state machine

```
START → load_context → run_checks → ─┬─ retrieve_policy ─┐
                                      └─ skip_policy ─────┴→ propose → guardrail → record_audit → END
```

* One conditional edge: policy retrieval only when a check flagged something
  (requests that pass everything cite the checks themselves).
* `propose` is the only probabilistic node; every other node is pure code.
* Node functions receive injected dependencies (SAP client, recommender, store,
  audit sink) so the whole graph runs in tests with zero network.

## Why the fallback recommender exists

`FallbackRecommender` maps checks → labels deterministically. It is:

* the CI engine (no secrets needed to gate on unsafe approvals),
* a baseline the LLM must beat in evals,
* the degradation path if the API is down (pipeline still produces a
  defensible `HOLD`/`ESCALATE` with evidence).

If the fallback and the model disagree, the guardrail result is the same —
the perimeter, not the model, defines what is allowed.

## Threat model (deliberately small)

* **Poisoned vendor text** → untrusted by design; regex injection scan (HARD),
  plus the model is told never to treat documents as instructions, plus any
  attempt to cite non-existent authorization is stripped by citation validation.
* **Model arithmetic error** → impossible to matter: checks compute, model only cites.
* **Jailbroken model emitting APPROVE** → guardrail caps by check status.
* **Silent failure** → audit line written for every run; `errors` surfaced.
