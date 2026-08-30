# Maritime × Ratify live authorization lab

This standalone public console executes the Maritime pilot's one-allow plus
eight-denial adversarial gate. Each row is populated only after the scenario
proxy returns a live, redacted result from the separately deployed Maritime
agent and receiver.

Run locally:

```bash
npm install
npm run dev
```

Verify the production build and rendered contract:

```bash
npm test
```

The browser receives no demo token, Ratify private key, proof, full certificate,
or arbitrary proxy capability. Deployment is managed through the repository's
Sites configuration and the secret-bound Ratify Labs router.
