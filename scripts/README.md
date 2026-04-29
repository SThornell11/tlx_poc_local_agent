# scripts/

| File | Purpose |
|---|---|
| `snowflake_setup.sql` | One-shot bootstrap. Run as `ACCOUNTADMIN` to provision the POC database, sample data, tier roles, gateway service account, Cortex Agent, and Managed MCP Server. Safe to re-run. |
| `finance_model.yaml` | Cortex Analyst semantic model for the Finance schema (revenue, budgets, employees). Upload to `@POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS` before running the `CREATE AGENT` statement in the SQL script. |
| `hr_model.yaml` | Cortex Analyst semantic model for the HR schema (employees, PII, compensation). Upload alongside `finance_model.yaml`. |
| `poc_walkthrough.md` | The tier-by-tier prompts to run through in the chat UI to demonstrate masking, row access, and object-level denial. |
| `entraid_sso_setup.md` | Step-by-step Microsoft Entra ID app registration for Phase 1 user login. |
| `okta_sso_setup.md` | Step-by-step Okta Web app setup for Phase 1 user login. |
| `diag_gateway.py` | Diagnostic — dumps the raw MCP / SSE traffic when the gateway is not behaving. Decrypts a session cookie or accepts a raw bearer token. |
| `diag_headers.py` | Diagnostic — probes whether the gateway requires `X-TrustLogix-*` headers on `initialize` / `tools/list`. |
