# Approval Governance

## POL-GOV-001 Approval authority

Approval authority depends on the request amount. For EUR and USD: up to 10,000 requires
a Finance Manager (L1); up to 50,000 a Finance Controller (L2); above 50,000 a CFO
delegate (L3). For INR the bands are 1,000,000 and 5,000,000. Any other currency requires
the CFO delegate. These bands are a simplification of currency conversion.

## POL-GOV-002 Four-eyes and overrides

The requester can never approve their own request. A human approver who approves against
the agent's recommendation must record a written override reason. The agent's
recommendation, the human decision and the reason are kept in the audit log.
