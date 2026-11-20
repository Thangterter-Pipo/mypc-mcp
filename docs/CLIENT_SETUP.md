# Connecting AI Clients to MyPC MCP

## 1. Enable Tailscale Funnel or Serve

1. Install [Tailscale](https://tailscale.com/download) and sign in.
2. In the Tailscale Admin Console, enable **MagicDNS** and **HTTPS Certificates** (and **Funnel** in ACLs if you need cloud web clients like ChatGPT/Claude/Gemini Web to connect).
3. Run:
   ```powershell
   .\scripts\tailscale_setup.ps1 -Mode funnel
   ```
   Copy the printed URL: `https://<machine>.<tailnet>.ts.net/mcp`.

## 2. ChatGPT Web (OAuth Connector)

1. Open **Settings → Apps → Connectors → Add Custom MCP**.
2. Name: `mypc`
3. Server URL: `https://<machine>.<tailnet>.ts.net/mcp`
4. Authentication: **OAuth** (Dynamic Client Registration is enabled automatically).
5. Click **Connect**. When your browser opens `/consent`, enter `MYPC_MCP_CONSENT_PIN` from `.env` and click **Allow Access**.
6. Start a new chat and test: *"List the drives on my computer"*.

## 3. Claude Code / Cursor (Bearer API Key)

Works with both **Tailscale Funnel** and private **Tailscale Serve** (when your client machine is on the same Tailnet):

```bash
claude mcp add --transport http mypc https://<machine>.<tailnet>.ts.net/mcp \
  --header "Authorization: Bearer <MYPC_MCP_API_KEY>"
```

## 4. Adding Other OAuth Clients

By default `security/oauth_policy.py` allowlists ChatGPT, Claude, Google/Gemini, and Meta AI OAuth redirects. To add another client redirect prefix, set `MYPC_MCP_OAUTH_REDIRECT_PREFIXES` in `.env` (comma-separated) and restart the server.
