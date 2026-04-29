# POC walkthrough

These prompts walk through three access tiers (`POC_TIER1_ROLE`,
`POC_TIER2_ROLE`, `POC_TIER3_ROLE`) and demonstrate masking, row access, and
object-level denial — all enforced live by Snowflake under the TrustLogix
MCP gateway.

To switch tiers, change the user's mapping in TrustLogix (or, for a quick
sanity check, change the default role in Snowflake):

```sql
ALTER USER <YOUR_USER> SET DEFAULT_ROLE = 'POC_TIER1_ROLE';   -- or TIER2 / TIER3
```

Then sign out of the chat UI and sign back in (the gateway picks up the new
role on the next session).

---

## 0. "Am I really connected to a live Snowflake MCP server?"

Use these to prove the agent isn't returning canned data. Each one triggers
a tool call you can see in the activity sidebar.

```
What tools do you have available? List each tool name, what it does, and what parameters it accepts.
```
> Should list two tools: `poc_cortex_agent` (Cortex Agent) and
> `poc_sql_executor` (SQL execution). The activity sidebar shows ALLOW
> entries with `mcp_server: POC_DEMO.CORTEX_OBJECTS.POC_MCP_SERVER`.

```
Who am I in Snowflake right now?
```
> The agent runs `SELECT CURRENT_USER(), CURRENT_ROLE()` through the gateway
> and returns the gateway service identity plus the role it assumed for
> your session.

---

## 1. Tier 1 — full visibility (data scientist / AI lead)

Set `DEFAULT_ROLE = POC_TIER1_ROLE`. Sign out + back in.

```
What was total revenue across all regions in 2025, broken down by business unit?
```

```
Show R&D budget variance for 2026 by business unit, highlighting any departments over 15% variance.
```

```
List 5 employees with name, email, SSN, phone, date of birth, and annual salary.
```
> **Expected:** All values in clear text. No masking. This is the baseline
> the other tiers compare against.

```
Find engineering guidelines from the internal knowledge base.
```

```
What does the CEO AI strategy memo say about the three-tier governance framework?
```

---

## 2. Tier 2 — partial masking, BU-restricted (engineer / Copilot Studio)

Set `DEFAULT_ROLE = POC_TIER2_ROLE`. Sign out + back in.

```
List 5 employees with name, email, SSN, phone, and annual salary.
```
> **Expected:** SSN looks like `XXX-XX-1234`, email partially redacted, names
> truncated, salary rounded. Snowflake masking policies fire automatically.

```
Show revenue by business unit for 2025.
```
> **Expected:** Only the user's mapped business unit returns rows (per the
> row-access policy). Other BUs are filtered out.

```
Show test result trends over the past four quarters by site.
```

---

## 3. Tier 3 — heavy masking, region-restricted, no engineering

Set `DEFAULT_ROLE = POC_TIER3_ROLE`. Sign out + back in.

```
List 5 employees with name, email, SSN, and salary.
```
> **Expected:** SSN = `***-**-****`, email = `****@****`, names = `****`,
> salary = NULL or rounded to nearest 100K.

```
Show me test results for product ID 000123.
```
> **Expected:** Snowflake denies the query (no schema-level USAGE on
> `POC_DEMO.ENGINEERING` for Tier 3). The agent surfaces the `[DENIED]`
> message.

```
Show revenue across all regions for Q1 2026.
```
> **Expected:** Only the user's mapped region returns rows.

```
What does the CEO AI strategy memo say about tiering?
```
> Tier 3 still has document access — the row-access policy does not apply
> to the document corpus.

---

## 4. Side-by-side validation

For any of the above, run the same query directly in a Snowflake worksheet
under the matching tier role and confirm the row count and masked values
match what the agent returned. This is the "yes, Snowflake is really
enforcing this" proof point — the agent is not filtering anything.

```sql
USE ROLE POC_TIER2_ROLE;
SELECT FIRST_NAME, LAST_NAME, EMAIL, SSN, ANNUAL_SALARY_USD
FROM POC_DEMO.HR.EMPLOYEES LIMIT 5;
```
