# Maritime console Worker migration

## Current state

The public console remains on Sites at `/maritime`. A separate staging Worker
has been deployed without changing the Sites deployment or DNS:

`https://ratify-maritime-demo-console-staging.chuks-04d.workers.dev`

The staging Worker uses the same console build and a staging-only router secret.
The production `LABS_ROUTER_TOKEN` and Sites deployment were not modified.

## Staging evidence

| Check | Result |
|---|---|
| Valid staging hostname and token, `/maritime` | 200 |
| Wrong token, `/maritime` | 404 |
| Missing token, `/maritime` | 404 |
| Console build tests | 2 passed, 0 failed, 0 skipped |
| Console lint | passed |

The hostname gate is configurable through `CONSOLE_ALLOWED_HOSTNAMES`. When it
is absent, the existing Sites provider hostname remains the only routed host.
This preserves the current behavior and keeps rollback available.

## Wrangler compatibility

The generated `dist/server/wrangler.json` contains the obsolete `legacy_env`
field. Wrangler 4.127.1 rejects that field. A Worker deployment must remove it
from the generated deployment copy while preserving the relative `main` and
`assets` paths by editing the file in `dist/server`, not by moving it elsewhere.

The deployment must also set `CONSOLE_ALLOWED_HOSTNAMES` explicitly. A generated
config otherwise emits an empty `vars` object, which would silently fall back to
the old provider hostname and make a staging Worker return 404.

## Production sequence

Production migration is not approved by this document. Before changing the
console origin or DNS:

1. Add a CI build-and-deploy job that uses the pinned Wrangler version, removes
   `legacy_env`, sets the hostname variable, and requires the console secret.
2. Verify the staging Worker with the rendered HTML, assets, metadata, valid and
   invalid router credentials, and the full scenario proxy path.
3. Deploy a production Worker while Sites remains available as fallback.
4. Point the Labs proxy at the production Worker only after direct checks pass.
5. Retain the old provider hostname and token path during the observation window.
6. Remove the Sites dependency only in a later, separately reviewed change.

The console's scenario proxy is a separate Worker and is not part of this
static-console migration.
