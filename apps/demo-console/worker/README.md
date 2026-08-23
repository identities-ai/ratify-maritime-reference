# Demo scenario proxy

This Cloudflare Worker is the credential-holding boundary between the static
demo console and the Maritime agent. It accepts only `POST /api/scenario` with
exactly one of these bodies:

```json
{"scenario":"allow"}
```

```json
{"scenario":"over_limit"}
```

It constructs the agent request from scratch, projects the response onto six
public fields, and uses one Durable Object to enforce exact five-per-client and
twenty-global requests per minute. The client key comes only from
`CF-Connecting-IP`. Limiter failure denies the request.

The Worker requires `RATIFY_DEMO_TOKEN` as a Cloudflare secret. Never put that
value in `wrangler.jsonc`, `.dev.vars`, source, or a client bundle committed to
the repository.

Run the local gate:

```bash
npm install
npm test
npm run typecheck
npx wrangler deploy --dry-run
```

Deployment is deliberately separate from implementation review. Confirm the
active Cloudflare account before setting the secret or deploying.
