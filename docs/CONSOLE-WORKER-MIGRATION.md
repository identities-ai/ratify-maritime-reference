# Maritime console Worker migration

## Current state

The public console is served by the production Cloudflare Worker at `/maritime`
through the Ratify Labs Worker. The Labs Worker targets:

`https://ratify-maritime-demo-console-production.chuks-04d.workers.dev`

The former Sites/provider origin is no longer routed. The Worker has no built-in
hostname: it answers only hostnames listed in `CONSOLE_ALLOWED_HOSTNAMES`.

The production Worker uses the same console build and the production router
secret. The separate staging Worker remains available for pre-release checks.

## Staging evidence

| Check | Result |
|---|---|
| Valid staging hostname and token, `/maritime` | 200 |
| Wrong token, `/maritime` | 404 |
| Missing token, `/maritime` | 404 |
| Console build tests | 2 passed, 0 failed, 0 skipped |
| Console lint | passed |

The hostname gate is configured through `CONSOLE_ALLOWED_HOSTNAMES`. When it
is absent or empty, no hostname is routed and every request returns 404.

## Wrangler compatibility

The generated `dist/server/wrangler.json` contains the obsolete `legacy_env`
field. Wrangler 4.127.1 rejects that field. A Worker deployment must remove it
from the generated deployment copy while preserving the relative `main` and
`assets` paths by editing the file in `dist/server`, not by moving it elsewhere.

The deployment must also set `CONSOLE_ALLOWED_HOSTNAMES` explicitly. A generated
config otherwise emits an empty `vars` object, and a Worker with no listed
hostname returns 404 for every request.

## Production sequence

The production cutover is complete. The Labs Worker now routes the registered
Maritime page and assets to the production console Worker. Before future
changes:

1. Use the CI build-and-deploy job with the pinned Wrangler version, remove
   `legacy_env`, set the hostname variable, and require the console secret.
2. Verify the staging Worker with the rendered HTML, assets, metadata, valid and
   invalid router credentials, and the full scenario proxy path.
3. Deploy the production console Worker while the former provider origin remains
   available as fallback.
4. Point the Labs Worker at the production Worker only after direct checks pass.
5. Retain the old provider hostname and token path for rollback during the
   observation window.

The console's scenario proxy is a separate Worker and is not part of this
static-console deployment.
