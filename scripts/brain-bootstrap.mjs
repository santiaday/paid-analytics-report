// brain-bootstrap.mjs — ONE-TIME headless-credential bootstrap for the DoorLoop Brain.
//
// Runs the interactive OAuth flow ONCE (you sign in with your doorloop.com Google account)
// and prints the three secrets the pull job needs thereafter, with no further interaction:
//     BRAIN_CLIENT_ID, BRAIN_CLIENT_SECRET, BRAIN_REFRESH_TOKEN
// Store those in AWS Secrets Manager (or the pull job's env). The refresh token rotates on
// use, so the running job persists the rotated value itself — this script just mints the first.
//
// Flow: dynamic client registration (RFC 7591) → /authorize with PKCE (opens your browser) →
// local loopback catches the code → /token (authorization_code) → refresh_token.
//
//   node scripts/brain-bootstrap.mjs            # issuer defaults to https://brain.doorloop.com
//   BRAIN_ISSUER=https://brain.doorloop.com node scripts/brain-bootstrap.mjs

import { createServer } from "node:http";
import { createHash, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";

const ISSUER = (process.env.BRAIN_ISSUER ?? "https://brain.doorloop.com").replace(/\/$/, "");
const PORT = Number(process.env.BOOTSTRAP_PORT ?? 8765);
const REDIRECT_URI = `http://localhost:${PORT}/callback`;

const b64url = (buf) => buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function main() {
  const meta = await (await fetch(`${ISSUER}/.well-known/oauth-authorization-server`, {
    headers: { accept: "application/json" },
  })).json();
  console.error("discovered:", meta.issuer, "\n  authorize:", meta.authorization_endpoint, "\n  token:", meta.token_endpoint);

  // 1. Dynamic client registration (confidential client, client_secret_post).
  const reg = await (await fetch(meta.registration_endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_name: "paid-analytics-pull",
      redirect_uris: [REDIRECT_URI],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "client_secret_post",
      scope: "mcp offline_access",
    }),
  })).json();
  if (!reg.client_id) throw new Error("registration failed: " + JSON.stringify(reg));
  console.error("registered client:", reg.client_id);

  // 2. PKCE + /authorize.
  const verifier = b64url(randomBytes(32));
  const challenge = b64url(createHash("sha256").update(verifier).digest());
  const stateTok = b64url(randomBytes(16));
  const authUrl = new URL(meta.authorization_endpoint);
  authUrl.search = new URLSearchParams({
    response_type: "code",
    client_id: reg.client_id,
    redirect_uri: REDIRECT_URI,
    scope: "mcp offline_access", // offline_access is REQUIRED for a refresh_token to be issued
    state: stateTok,
    code_challenge: challenge,
    code_challenge_method: "S256",
  }).toString();

  const code = await new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      const u = new URL(req.url, `http://localhost:${PORT}`);
      if (u.pathname !== "/callback") { res.writeHead(404); res.end(); return; }
      const err = u.searchParams.get("error");
      const got = u.searchParams.get("code");
      const st = u.searchParams.get("state");
      res.writeHead(200, { "content-type": "text/html" });
      res.end(`<h2>${err ? "Authorization failed" : "Authorized"}</h2><p>${err ?? "You can close this tab and return to the terminal."}</p>`);
      server.close();
      if (err) return reject(new Error("authorize error: " + err));
      if (st !== stateTok) return reject(new Error("state mismatch (possible CSRF)"));
      if (!got) return reject(new Error("no code in callback"));
      resolve(got);
    });
    server.listen(PORT, () => {
      console.error(`\nOpen this URL and sign in with your doorloop.com account:\n\n  ${authUrl}\n`);
      spawn("open", [authUrl.toString()], { stdio: "ignore" }).on("error", () => {});
    });
  });

  // 3. authorization_code → tokens.
  const tok = await (await fetch(meta.token_endpoint, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: REDIRECT_URI,
      client_id: reg.client_id,
      client_secret: reg.client_secret ?? "",
      code_verifier: verifier,
    }).toString(),
  })).json();
  if (!tok.refresh_token) throw new Error("token exchange returned no refresh_token: " + JSON.stringify(tok));

  console.error("\n✅ Brain headless credentials (store these as secrets):\n");
  console.log(JSON.stringify({
    BRAIN_ISSUER: ISSUER,
    BRAIN_CLIENT_ID: reg.client_id,
    BRAIN_CLIENT_SECRET: reg.client_secret ?? "",
    BRAIN_REFRESH_TOKEN: tok.refresh_token,
  }, null, 2));
  console.error("\naccess_token (expires in " + tok.expires_in + "s) obtained OK — the pull job refreshes on its own from here.");
}

main().catch((e) => { console.error("bootstrap failed:", e.message); process.exit(1); });
