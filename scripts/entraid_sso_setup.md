# Microsoft Entra ID SSO Setup for the TrustLogix POC Agent

The chat UI authenticates the user via Entra ID OIDC. The same user identity
is then used to obtain a per-user OAuth token against the TrustLogix MCP
gateway (Phase 2). The agent never holds a Snowflake credential — the
gateway authenticates to Snowflake on behalf of the authorized user.

## Step 1 — App registration

1. In the Azure portal, go to **Microsoft Entra ID → App registrations →
   New registration**.
2. Settings:
   - **Name**: `TrustLogix POC Agent`
   - **Supported account types**: Single tenant (or whatever your tenant requires)
   - **Redirect URI** → **Web** → `http://localhost:5000/auth/callback`
3. Click **Register**.
4. From the **Overview** page, copy:
   - **Directory (tenant) ID** → `.env` `ENTRAID_TENANT_ID`
   - **Application (client) ID** → `.env` `ENTRAID_CLIENT_ID`

## Step 2 — Client secret

1. Go to **Certificates & secrets → Client secrets → New client secret**.
2. Description: `TrustLogix POC local agent`. Expiry: whatever fits the POC window.
3. Copy the **Value** (not the Secret ID) → `.env` `ENTRAID_CLIENT_SECRET`.

## Step 3 — Token configuration (optional, recommended)

To get the user's display name in the ID token:

1. Go to **Token configuration → Add optional claim → ID**.
2. Add: `email`, `family_name`, `given_name`, `upn`.
3. (Optional) **Add groups claim** → choose **Security groups** or **All groups**
   if you want to map Entra ID groups → roles inside TrustLogix.

## Step 4 — API permissions

Default OIDC scopes are sufficient. Verify under **API permissions**:
- Microsoft Graph → `openid`, `profile`, `email`, `offline_access`

## Step 5 — Map the user inside TrustLogix

The TrustLogix gateway uses the user's Entra ID identity (UPN / email) to
evaluate ABAC policies. Make sure each user who will sign in to the chat UI
also exists as a principal inside TrustLogix and is mapped to the role you
want their session to assume in Snowflake (e.g. `POC_TIER1_ROLE`,
`POC_TIER2_ROLE`, `POC_TIER3_ROLE`).

## Step 6 — Test

```bash
docker compose up --build
```

Open `http://localhost:5000`, click **Sign in with Microsoft**, complete the
Entra ID flow. After the Phase 1 callback, the agent immediately runs
Phase 2 against the TrustLogix gateway. After both phases, the chat header
shows your name and the gateway service identity.
