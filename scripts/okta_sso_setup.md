# Okta SSO Setup for the TrustLogix POC Agent

The chat UI authenticates the user via Okta OIDC. The same user identity is
then used to obtain a per-user OAuth token against the TrustLogix MCP
gateway (Phase 2). The agent never holds a Snowflake credential — the
gateway authenticates to Snowflake on behalf of the authorized user.

## Step 1 — Create a Web app

1. In the Okta admin console (`https://<your-org>-admin.okta.com`), go to
   **Applications → Applications → Create App Integration**.
2. Choose **OIDC - OpenID Connect** as the sign-in method.
3. Choose **Web Application** as the application type. Click **Next**.
4. Settings:
   - **App integration name**: `TrustLogix POC Agent`
   - **Grant type**: **Authorization Code** (default; leave **Refresh Token** unchecked unless your policy requires it)
   - **Sign-in redirect URIs**: `http://localhost:5000/auth/callback`
   - **Sign-out redirect URIs**: `http://localhost:5000/`
   - **Controlled access**: pick whichever group(s) of users will run the POC
5. Click **Save**.

## Step 2 — Copy the credentials

On the **General** tab of the new app:
- **Client ID** → `.env` `OKTA_CLIENT_ID`
- **Client secret** (Client Credentials section, click *Show* / *Generate new secret* if hidden) → `.env` `OKTA_CLIENT_SECRET`

Your **Okta domain** is the part of the admin URL before `-admin.okta.com`,
appended with `.okta.com` — e.g. `dev-12345.okta.com`. This goes into
`.env` as `OKTA_DOMAIN`.

## Step 3 — Authorization server

`OKTA_AUTH_SERVER_ID=default` works for most tenants and is what the
template ships with. If your org uses a custom authorization server,
substitute its ID.

Verify by visiting:

```
https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>/.well-known/openid-configuration
```

You should see a JSON document with `authorization_endpoint`,
`token_endpoint`, etc.

## Step 4 — Map the user inside TrustLogix

The TrustLogix gateway uses the user's identity (email / `preferred_username`)
to evaluate ABAC policies. Make sure each user who will sign in to the chat
UI also exists as a principal inside TrustLogix and is mapped to the role
you want their session to assume in Snowflake (e.g. `POC_TIER1_ROLE`,
`POC_TIER2_ROLE`, `POC_TIER3_ROLE`).

## Step 5 — Test

```bash
docker compose up --build
```

Open `http://localhost:5000`, click **Sign in with Okta**, complete the
Okta flow. After the Phase 1 callback, the agent immediately runs Phase 2
against the TrustLogix gateway. After both phases, the chat header shows
your name and the gateway service identity.
