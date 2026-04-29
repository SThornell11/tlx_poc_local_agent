# TrustLogix MCP Gateway POC

A self-contained chat application for evaluating the **TrustLogix MCP
gateway**. An LLM-powered chat UI talks to the gateway over the Model Context
Protocol; every tool call is authorized live by TrustLogix and forwarded to
whatever data sources sit behind the gateway. Policy enforcement happens at
TrustLogix and the data platform — never in the agent.

The kit works with **any MCP server you put behind your TrustLogix gateway**
(Snowflake, Databricks, Postgres, custom). For prospects who don't have a
backing data source ready yet, an optional bundled Snowflake setup spins up
a self-contained demo dataset.

## Architecture

```
                +----------------+
                |    Browser     |
                |  localhost:5000│
                +-------+--------+
                        │
                +-------▼--------+
                |    Frontend    |   FastAPI reverse proxy + chat UI
                +-------+--------+
                        │
                +-------▼--------+
                |  Agent Server  |   FastAPI; OAuth, MCP, LLM
                +-------+--------+
                        │  Bearer = per-user OAuth token
                +-------▼--------+
                | TrustLogix MCP |   policy enforcement (ALLOW / DENY)
                |    Gateway     |
                +-------+--------+
                        │
                +-------▼--------+
                | Your data MCP  |   Snowflake / Databricks / Postgres / …
                |  (any vendor)  |
                +----------------+
```

The agent never holds a credential for the underlying data store. It calls
the gateway with the end user's OAuth token; the gateway enforces TrustLogix
policies and authenticates to the backend data MCP server itself.

---

# Part 1 — Get the agent running

This is all most prospects need. Plan **~15 minutes**.

## What you need

| | |
|---|---|
| TrustLogix MCP gateway URL | Provided by your TrustLogix contact, with at least one MCP server already registered behind it. |
| Identity provider | Microsoft Entra ID **or** Okta. You'll create one app registration. |
| LLM | An Anthropic API key (recommended) **or** a machine that can run Ollama. |
| Tools | Docker + Docker Compose, and Python 3.10+ for the diagnostic scripts. |

## Step 1 — Set up your IdP

The redirect URI in **both** cases is `http://localhost:5000/auth/callback`.

- Microsoft Entra ID → follow `scripts/entraid_sso_setup.md`. You'll come
  away with a **Tenant ID**, **Client ID**, and **Client secret**.
- Okta → follow `scripts/okta_sso_setup.md`. You'll come away with a
  **Domain**, **Client ID**, and **Client secret**.

Make sure the user you sign in with also exists inside your TrustLogix
gateway and is mapped to a role — the gateway uses the user's identity to
evaluate ABAC policies.

## Step 2 — Fill in `.env`

```bash
cp .env.example .env
```

Walkthrough — fill these top to bottom:

| Variable | Where it comes from | Required? |
|---|---|---|
| `AUTH_IDP` | `entra` or `okta`. | yes |
| `ENTRAID_TENANT_ID` | Entra app registration → Overview → Directory (tenant) ID. | if Entra |
| `ENTRAID_CLIENT_ID` | Entra app → Overview → Application (client) ID. | if Entra |
| `ENTRAID_CLIENT_SECRET` | Entra app → Certificates & secrets → New client secret → copy the **Value**. | if Entra |
| `ENTRAID_REDIRECT_URI` | Leave as `http://localhost:5000/auth/callback`. | if Entra |
| `OKTA_DOMAIN` | Your Okta tenant (e.g. `dev-12345.okta.com`). | if Okta |
| `OKTA_CLIENT_ID` | Okta app → General → Client ID. | if Okta |
| `OKTA_CLIENT_SECRET` | Okta app → General → Client secret. | if Okta |
| `OKTA_AUTH_SERVER_ID` | Leave at `default` unless you use a custom Okta auth server. | if Okta |
| `TLX_MCP_URL` | Your gateway URL from your TrustLogix contact (e.g. `https://mcpgateway.<host>/mcp`). | yes |
| `TRUSTLOGIX_MCP_TIMEOUT` | Leave at `30` unless you see timeouts. | no |
| `TLX_GW_SERVICE_ROLE` | Display label for the gateway's backend role/account in the chat header. Defaults to `"TLX Gateway"` if unset. | no |
| `SESSION_SECRET` | Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Keep stable across restarts. | yes |
| `SECURE_COOKIES` | `false` for localhost. `true` if you front this with TLS. | no |
| `LLM_PROVIDER` | `anthropic` (recommended) or `ollama`. | yes |
| `LLM_MODEL` | E.g. `claude-haiku-4-5-20251001` for Anthropic, `qwen2.5:7b` for Ollama. | yes |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys. | if Anthropic |
| `OLLAMA_BASE_URL` | Leave as `http://ollama:11434`. | if Ollama |
| `CUSTOMER_LABEL` | Optional chip in the chat header. | no |
| `NVIDIA_API_KEY` | Optional. Enables NeMo guardrails. | no |
| `LANGCHAIN_TRACING_V2`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Optional LangSmith tracing. | no |

### Example filled `.env` (Entra + Anthropic)

```dotenv
AUTH_IDP=entra
ENTRAID_TENANT_ID=11111111-2222-3333-4444-555555555555
ENTRAID_CLIENT_ID=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
ENTRAID_CLIENT_SECRET=Abc1~XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
ENTRAID_REDIRECT_URI=http://localhost:5000/auth/callback

OKTA_DOMAIN=
OKTA_CLIENT_ID=
OKTA_CLIENT_SECRET=
OKTA_AUTH_SERVER_ID=default

TLX_MCP_URL=https://mcpgateway.example.trustlogix.com/mcp
TRUSTLOGIX_MCP_TIMEOUT=30
TLX_GW_SERVICE_ROLE=

SESSION_SECRET=2b9f1c3a8d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8090a1b2c3d4e5f6a7b8c9d0ef
SECURE_COOKIES=false

LLM_PROVIDER=anthropic
LLM_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OLLAMA_BASE_URL=http://ollama:11434

CUSTOMER_LABEL=
NVIDIA_API_KEY=
LANGCHAIN_TRACING_V2=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=tlx-poc
```

## Step 3 — Run

```bash
docker compose up --build
```

Three containers come up: `ollama`, `agent-server`, `frontend`. The first
build takes a few minutes; subsequent runs start in seconds. (If
`LLM_PROVIDER=ollama`, the first chat turn also pulls the model — add 1–5
minutes.)

| URL | What it is |
|---|---|
| http://localhost:5000 | Chat UI |
| http://localhost:5100/api/health | Agent server health probe |

If `agent-server` exits immediately with `TLX_MCP_URL is required` or
`SESSION_SECRET (...)` in the logs, fix the `.env` and recreate:

```bash
docker compose up -d --build agent-server
```

## Step 4 — Sanity check

Open http://localhost:5000, sign in via your IdP. After both OAuth phases
complete, the chat input becomes available. Ask:

```
What tools do you have available? List each tool name, what it does, and what parameters it accepts.
```

What you should see:
- The activity sidebar lists at least one **gateway** event with
  `decision: ALLOW`.
- The reply lists every tool the gateway is exposing (these depend on what
  MCP server(s) your gateway has registered behind it).
- The header badge updates from "identity unknown" to your email plus the
  gateway service identity.

If any of those don't appear, jump to **Troubleshooting** below.

**You now have a working agent.** Start asking questions about whatever
data sits behind your gateway. If you don't have a data source yet, keep
reading.

---

# Part 2 — Optional: bundled Snowflake demo data

Skip this section if your TrustLogix gateway is already in front of an MCP
server with real data. This part exists for prospects who want a turnkey
dataset to demo against.

The bundled SQL provisions a self-contained `POC_DEMO` Snowflake database
with sample HR / Finance / Engineering data plus a Cortex Agent and a
Managed MCP Server (`POC_MCP_SERVER`) that your TrustLogix gateway can sit
in front of. Plan **~15–30 minutes**.

**Prereq:** a Snowflake account with Cortex Analyst + Cortex Search +
Managed MCP Server features enabled (Enterprise Edition or higher on most
AWS / Azure regions). If `SHOW MCP SERVERS` doesn't work in your region,
talk to your Snowflake account team before continuing.

**No masking or row-access policies are created by this script.** Those
are authored in TrustLogix TrustAccess and pushed down to Snowflake by
your TrustLogix admin. The script only sets up the substrate (tables,
roles, grants, MCP server). Adding policies in this script would fight
the ones TrustLogix manages.

## 2a — Replace the gateway public key

Near the top of `scripts/snowflake_setup.sql` (Step 5), find:

```sql
ALTER USER POC_GW_SVC SET RSA_PUBLIC_KEY = '<PASTE GATEWAY PUBLIC KEY HERE>';
```

Replace `<PASTE GATEWAY PUBLIC KEY HERE>` with the public-key body provided
by your TrustLogix contact (the base64 between `-----BEGIN PUBLIC KEY-----`
and `-----END PUBLIC KEY-----`, no header lines, no whitespace).

## 2b — Run sections 1 through 15

Open the script in a Snowflake worksheet, switch to `ACCOUNTADMIN`, and run
from the top down to the `▼▼▼ STOP HERE THE FIRST TIME ▼▼▼` banner (just
before "Section 16. Cortex Agent"). This builds:

- Warehouse `POC_DEMO_WH`
- Database `POC_DEMO` with `HR`, `FINANCE`, `ENGINEERING`, `CORTEX_OBJECTS` schemas
- Tier roles `POC_TIER1_ROLE` … `POC_TIER3_ROLE` + `POC_ADMIN_ROLE`
- Gateway service account `POC_GW_SVC` (with the public key you pasted)
- Sample data (employees, revenue, budgets, test results, documents)
- Cortex Search service `POC_DOC_SEARCH`
- The empty `SEMANTIC_MODELS` stage

## 2c — Upload the two YAMLs to `@SEMANTIC_MODELS`

Snowsight: *Data → POC_DEMO → CORTEX_OBJECTS → Stages → SEMANTIC_MODELS → + Files*
and drop in `scripts/finance_model.yaml` and `scripts/hr_model.yaml`.

Or via SnowSQL:

```sql
PUT file://scripts/finance_model.yaml
    @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS
    AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

PUT file://scripts/hr_model.yaml
    @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS
    AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

Confirm both files appear with `LIST @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS;`.

## 2d — Run sections 16 and 17

Continue from `CREATE OR REPLACE AGENT POC_CORTEX_AGENT` to the bottom.
This creates the Cortex Agent and the Managed MCP Server, then runs the
verification queries. `POC_MCP_SERVER`, `POC_CORTEX_AGENT`, and
`POC_DOC_SEARCH` should all appear, and the row-count totals should be
non-zero.

## 2e — Hand off to your TrustLogix admin

The substrate is now in place. Your TrustLogix admin needs to:

1. Register the Snowflake account inside TrustLogix.
2. Discover/import `POC_MCP_SERVER` so the TLX gateway can proxy it.
3. Author masking and row-access policies in TrustAccess and push them
   down to `POC_TIER1_ROLE`, `POC_TIER2_ROLE`, `POC_TIER3_ROLE`.
4. Map your IdP users to those tier roles inside TrustLogix.

Until policies are pushed, all three tiers will return raw data — that's
expected.

## 2f — Set `TLX_GW_SERVICE_ROLE` and walk through it

Set `TLX_GW_SERVICE_ROLE=POC_GW_SVC` in `.env`, recreate the agent
container, sign in to the chat UI, and follow `scripts/poc_walkthrough.md`.
It runs tier-by-tier prompts that demonstrate masking, row access, and
object-level denial — all enforced live by Snowflake under TrustLogix
policy.

---

## LLM choice

| | Anthropic | Ollama |
|---|---|---|
| Setup | one API key | none — runs in the bundled container |
| Speed | fast | slow on CPU; OK with NVIDIA GPU |
| Cost | per-token | free after the model pull |
| Quality | best | varies by model |

To enable GPU for Ollama, uncomment the NVIDIA `deploy:` block in
`docker-compose.yml`.

---

## Troubleshooting

**"No tools discovered" / empty tool list.**
The gateway is not returning tools. Check, in order:
- `TLX_MCP_URL` is correct and reachable from the agent container.
- `docker compose logs agent-server --tail 100` for the gateway response.
- Run `python scripts/diag_gateway.py --cookie <tlx_srv_... cookie value>` to dump the raw MCP traffic. Get the cookie value from your browser's DevTools → Application → Cookies.
- Confirm the gateway has at least one MCP server registered behind it, and that your user identity has access to it.

**"Phase 2 incomplete" warning, or no Phase 2 redirect after sign-in.**
The user's gateway token cookie is missing. Sign out and back in to re-run
the two-phase OAuth flow. If it keeps failing, check the gateway's
`/.well-known/oauth-protected-resource` — Phase 2 requires the gateway to
advertise an authorization server.

**Container fails to start with `TLX_MCP_URL is required`.**
The agent server refuses to start without `TLX_MCP_URL`. Set it in `.env`
and `docker compose up -d --build agent-server`.

**Gateway returns `403` / `DENY` for every tool call.**
The user is authenticated but not authorized inside TrustLogix. Confirm
the user is mapped to a role with access to the MCP server in the
TrustLogix policy console.

**Snowflake script (Part 2) fails at `CREATE AGENT` or `CREATE MCP SERVER`.**
- "object does not exist" pointing at a YAML — the YAMLs aren't in `@SEMANTIC_MODELS`. Re-do step 2c.
- "feature is not enabled" — your Snowflake account/region doesn't have Cortex Agents or Managed MCP Servers. Talk to your Snowflake team.

**Anthropic 529 Overloaded.**
Switch to a different model: `LLM_MODEL=claude-haiku-4-5-20251001` (often
less loaded than Sonnet/Opus).

**Tool calls return malformed arguments.**
Smaller LLMs (< 7B parameters) often fail to construct proper tool
arguments. Use Anthropic, or at minimum a 7B Ollama model (`qwen2.5:7b`).

**Logs.**
```bash
docker compose logs -f agent-server
docker compose logs -f frontend
```

**Reset MCP session state** (after changing TrustLogix policies):
- Click "Reset" in the chat header, or
- `docker compose down && docker compose up -d`

---

## Teardown

### Stop the agent

```bash
docker compose down -v       # stops containers, removes the ollama volume
```

### Remove the IdP app
Delete the Entra ID app registration or Okta app if it was created only
for the POC.

### Remove the bundled Snowflake demo (only if you ran Part 2)

```sql
USE ROLE ACCOUNTADMIN;

DROP DATABASE IF EXISTS POC_DEMO CASCADE;
DROP WAREHOUSE IF EXISTS POC_DEMO_WH;
DROP USER IF EXISTS POC_GW_SVC;
DROP ROLE IF EXISTS POC_TIER1_ROLE;
DROP ROLE IF EXISTS POC_TIER2_ROLE;
DROP ROLE IF EXISTS POC_TIER3_ROLE;
DROP ROLE IF EXISTS POC_ADMIN_ROLE;
```

Have your TrustLogix admin remove the corresponding TrustAccess policies
and the registered Snowflake account.
