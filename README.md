# TrustLogix MCP Gateway POC

A self-contained chat application for evaluating the **TrustLogix MCP
gateway**. An LLM-powered chat UI talks to the gateway over the Model Context
Protocol; every tool call is authorized live by TrustLogix and forwarded to
your Snowflake account. Row-access policies, column masking, and agent
purpose restrictions are enforced by Snowflake under TrustLogix policy
control — never faked or filtered in the agent layer.

**Plan ~30–45 minutes for first-time setup**, plus ~10 minutes of Snowflake
build wait time. Subsequent runs start in seconds.

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
                        │  gateway service account (key-pair JWT)
                +-------▼--------+
                |   Snowflake    |   row access + masking policies
                +----------------+
```

The agent never holds a Snowflake credential. It calls the gateway with the
end user's OAuth token; the gateway enforces TrustLogix policies and then
authenticates to Snowflake itself.

## What you need before you start

| | |
|---|---|
| Snowflake account | Cortex Analyst + Cortex Search + Managed MCP Server features must be enabled (available on most AWS / Azure regions on Enterprise Edition or higher). ACCOUNTADMIN access for the one-time setup. |
| Identity provider | Microsoft Entra ID **or** Okta. You'll create one app registration. |
| TrustLogix gateway | A provisioned MCP gateway URL. Your TrustLogix contact gives you this plus the gateway service account public key. |
| LLM | An Anthropic API key (recommended) **or** a machine that can run Ollama (the bundled container will pull the model on first run). |
| Tools | Docker + Docker Compose, and Python 3.10+ for the diagnostic scripts. |

If your Snowflake account doesn't list "Cortex Agents" or "Managed MCP
Servers" in `SHOW PARAMETERS` / the docs for your region, talk to your
Snowflake account team before going further — the script in Step 1 will
fail without them.

---

## Step 1 — Snowflake setup

The bundled SQL provisions a self-contained `POC_DEMO` database with sample
data, four tier roles, a Cortex Agent, and a Managed MCP Server that the
TrustLogix gateway will sit in front of. Open `scripts/snowflake_setup.sql`
in a Snowflake worksheet and run it as `ACCOUNTADMIN`. The script is
idempotent — safe to re-run.

It runs in three phases with one **manual pause point** in the middle.

### 1a. Replace the gateway public key

Near the top of the script (Step 5), find:

```sql
ALTER USER POC_GW_SVC SET RSA_PUBLIC_KEY = '<PASTE GATEWAY PUBLIC KEY HERE>';
```

Replace `<PASTE GATEWAY PUBLIC KEY HERE>` with the public-key body provided
by your TrustLogix contact (just the base64 between `-----BEGIN PUBLIC
KEY-----` and `-----END PUBLIC KEY-----`, no whitespace, no header lines).

### 1b. Run sections 1 through 15

Run from the top of the file down to the `STOP HERE THE FIRST TIME` banner
(just before "Section 16. Cortex Agent"). This builds:
- Warehouse `POC_DEMO_WH`
- Database `POC_DEMO` with `HR`, `FINANCE`, `ENGINEERING`, `CORTEX_OBJECTS` schemas
- Tier roles `POC_TIER1_ROLE` … `POC_TIER3_ROLE` + `POC_ADMIN_ROLE`
- Gateway service account `POC_GW_SVC` (with the public key you pasted)
- Sample data (employees, revenue, budgets, test results, documents)
- Cortex Search service `POC_DOC_SEARCH`
- The empty `SEMANTIC_MODELS` stage

### 1c. Upload the two YAMLs to `@SEMANTIC_MODELS`

The Cortex Agent in section 16 references two semantic-model YAML files
that have to already exist in the stage. Upload them via Snowsight:

> *Data → POC_DEMO → CORTEX_OBJECTS → Stages → SEMANTIC_MODELS → + Files*

and drop in:
- `scripts/finance_model.yaml`
- `scripts/hr_model.yaml`

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

### 1d. Run sections 16 and 17

Continue executing the script from `CREATE OR REPLACE AGENT POC_CORTEX_AGENT`
to the bottom. This creates the Cortex Agent and the Managed MCP Server,
then runs verification queries.

### 1e. Verify

The verification block at the bottom of the script prints row counts and
lists `POC_MCP_SERVER` / `POC_CORTEX_AGENT` / `POC_DOC_SEARCH`. All three
must show up. The HR/Finance/Engineering row counts should be non-zero.

---

## Step 2 — Identity provider setup

Pick one of Entra ID or Okta. The redirect URI in **both** cases is:

```
http://localhost:5000/auth/callback
```

- Microsoft Entra ID → follow `scripts/entraid_sso_setup.md`. You'll come
  away with a **Tenant ID**, **Client ID**, and **Client secret**.
- Okta → follow `scripts/okta_sso_setup.md`. You'll come away with a
  **Domain**, **Client ID**, and **Client secret**.

Make sure the user you sign in with also exists inside your TrustLogix
gateway and is mapped to a role — the gateway uses the user's identity to
evaluate ABAC policies.

---

## Step 3 — Fill in `.env`

Copy the template and edit:

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
| `TLX_MCP_URL` | Provided by your TrustLogix contact (e.g. `https://mcpgateway.<host>/mcp`). | yes |
| `TRUSTLOGIX_MCP_TIMEOUT` | Leave at `30` unless you see timeouts. | no |
| `TLX_GW_SERVICE_ROLE` | Display label for the gateway's Snowflake role in the chat header. Defaults to `"TLX Gateway"` if unset. Set to `POC_GW_SVC` if you used the bundled SQL unchanged. | no |
| `SESSION_SECRET` | Generate on your machine with `python -c "import secrets; print(secrets.token_hex(32))"`. Keep stable across restarts. | yes |
| `SECURE_COOKIES` | `false` for localhost. `true` if you front this with TLS. | no |
| `LLM_PROVIDER` | `anthropic` (recommended) or `ollama`. | yes |
| `LLM_MODEL` | E.g. `claude-haiku-4-5-20251001` for Anthropic, `qwen2.5:7b` for Ollama. | yes |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys. | if Anthropic |
| `OLLAMA_BASE_URL` | Leave as `http://ollama:11434` — points at the bundled container. | if Ollama |
| `CUSTOMER_LABEL` | Optional chip in the chat header (e.g. your company name). | no |
| `NVIDIA_API_KEY` | Optional. Enables NeMo guardrails (input/output safety rails). | no |
| `LANGCHAIN_TRACING_V2`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Optional LangSmith tracing for debugging the agent's tool calls. | no |

### Example filled `.env` (Entra + Anthropic)

```dotenv
AUTH_IDP=entra
ENTRAID_TENANT_ID=11111111-2222-3333-4444-555555555555
ENTRAID_CLIENT_ID=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
ENTRAID_CLIENT_SECRET=Abc1~XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
ENTRAID_REDIRECT_URI=http://localhost:5000/auth/callback
ENTRAID_API_SCOPE=

OKTA_DOMAIN=
OKTA_CLIENT_ID=
OKTA_CLIENT_SECRET=
OKTA_AUTH_SERVER_ID=default

TLX_MCP_URL=https://mcpgateway.example.trustlogix.com/mcp
TRUSTLOGIX_MCP_TIMEOUT=30
TLX_GW_SERVICE_ROLE=POC_GW_SVC

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

---

## Step 4 — Run

```bash
docker compose up --build
```

Three containers come up: `ollama`, `agent-server`, `frontend`. The first
build takes a few minutes; subsequent runs start in seconds. (If
`LLM_PROVIDER=ollama`, the first chat turn will also pull the model — add
another 1–5 minutes.)

| URL | What it is |
|---|---|
| http://localhost:5000 | Chat UI |
| http://localhost:5100/api/health | Agent server health probe — should return `{"status":"ok"}` |

If `agent-server` exits immediately with `TLX_MCP_URL is required` or
`SESSION_SECRET (...)` in the logs, fix the `.env` and recreate the
container:

```bash
docker compose up -d --build agent-server
```

---

## Step 5 — Sanity check (run before the tier walkthrough)

Open http://localhost:5000, sign in via your IdP. After both OAuth phases
complete, the chat input becomes available. Ask:

```
What tools do you have available? List each tool name, what it does, and what parameters it accepts.
```

What you should see:
- The activity sidebar lists at least one **gateway** event with
  `decision: ALLOW` and `mcp_server: POC_DEMO.CORTEX_OBJECTS.POC_MCP_SERVER`.
- The chat reply lists two tools: **`poc_cortex_agent`** (Cortex Agent for
  finance/HR/engineering) and **`poc_sql_executor`** (raw SQL).
- The header badge updates from "identity unknown" to something like
  `you@yourdomain · via TLX Gateway → POC_GW_SVC`.

If any of those don't appear, jump to **Troubleshooting** below before
running the tier walkthrough.

---

## Step 6 — Walk through the POC

Follow `scripts/poc_walkthrough.md`. It runs the same prompts under three
different access tiers (`POC_TIER1_ROLE` → `POC_TIER2_ROLE` → `POC_TIER3_ROLE`)
and shows masking, row access, and object-level denial all enforced by
Snowflake under TrustLogix policy. Run the same SQL side-by-side in a
Snowflake worksheet to prove the agent isn't filtering anything in code.

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
- Confirm the gateway has the user's identity mapped to a role that can see `POC_MCP_SERVER`.

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
the user is mapped to a role with access to `POC_MCP_SERVER` in the
TrustLogix policy console.

**Snowflake script fails at `CREATE AGENT` or `CREATE MCP SERVER`.**
- "object does not exist" pointing at a YAML — the YAMLs aren't in `@SEMANTIC_MODELS`. Re-do step 1c.
- "feature is not enabled" — your Snowflake account/region doesn't have Cortex Agents or Managed MCP Servers yet. Talk to your Snowflake team.

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

When the POC is complete, drop everything from your Snowflake account:

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

On the host:

```bash
docker compose down -v       # stops containers, removes the ollama volume
```

Remove your IdP app registration (Entra ID app or Okta app) if it was
created only for the POC.
