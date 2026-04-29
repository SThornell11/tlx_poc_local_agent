# scripts/

The agent (`docker compose up`) only needs `entraid_sso_setup.md` or
`okta_sso_setup.md` for setup. Everything else is **optional** — used only
if you want the bundled Snowflake demo data, walkthrough, or diagnostic
helpers.

## Required for setup

| File | Purpose |
|---|---|
| `entraid_sso_setup.md` | Step-by-step Microsoft Entra ID app registration for Phase 1 user login. |
| `okta_sso_setup.md` | Step-by-step Okta Web app setup for Phase 1 user login. |

## Optional — bundled Snowflake demo data (Part 2 in the main README)

| File | Purpose |
|---|---|
| `snowflake_setup.sql` | One-shot bootstrap. Provisions the `POC_DEMO` database, sample data, tier roles, gateway service account, Cortex Agent, and Managed MCP Server. **Does not create masking or row-access policies — those are pushed from TrustLogix TrustAccess.** Safe to re-run. |
| `finance_model.yaml` | Cortex Analyst semantic model for the Finance schema. Upload to `@POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS` before running the `CREATE AGENT` statement in the SQL script. |
| `hr_model.yaml` | Cortex Analyst semantic model for the HR schema. Upload alongside `finance_model.yaml`. |
| `poc_walkthrough.md` | Tier-by-tier prompts to run in the chat UI once TrustLogix has pushed masking and row-access policies down to the bundled tier roles. |

## Optional — diagnostics

| File | Purpose |
|---|---|
| `diag_gateway.py` | Dumps the raw MCP / SSE traffic when the gateway is not behaving. Decrypts a session cookie or accepts a raw bearer token. |
| `diag_headers.py` | Probes whether the gateway requires `X-TrustLogix-*` headers on `initialize` / `tools/list`. |
