import { describe, expect, it, vi } from "vitest";

import worker, { handleRequest, ScenarioLimiter } from "../src/index";

const TOKEN = "demo-token-marker";
const ORIGIN = "https://labs.ratifyprotocol.com";

class TestStorage {
  readonly values = new Map<string, unknown>();
  private pending = Promise.resolve();

  transaction<T>(callback: (transaction: TestStorage) => Promise<T>): Promise<T> {
    const result = this.pending.then(() => callback(this));
    this.pending = result.then(() => undefined, () => undefined);
    return result;
  }

  async get<T>(key: string): Promise<T | undefined> {
    return this.values.get(key) as T | undefined;
  }

  async put<T>(key: string, value: T): Promise<void> {
    this.values.set(key, value);
  }

  async list<T>(options: { prefix: string }): Promise<Map<string, T>> {
    return new Map(
      [...this.values.entries()].filter(([key]) => key.startsWith(options.prefix))
    ) as Map<string, T>;
  }

  async delete(key: string): Promise<boolean> {
    return this.values.delete(key);
  }
}

function limitRequest(client: string, now = 1_000_000): Request {
  return new Request("https://limiter/check", {
    method: "POST",
    body: JSON.stringify({ client, now }),
  });
}

function environment(options: { allowed?: boolean; limiterFailure?: boolean } = {}) {
  const limiter = vi.fn(async (_request: Request) => {
    if (options.limiterFailure) throw new Error("backend secret text");
    return Response.json({ allowed: options.allowed ?? true });
  });
  return {
    env: {
      AGENT_URL: "https://agent.example",
      AGENT_B_URL: "https://agent-b.example",
      CONSOLE_ORIGIN: ORIGIN,
      RATIFY_DEMO_TOKEN: TOKEN,
      RATIFY_DEMO_TOKEN_B: `${TOKEN}-b`,
      SCENARIO_LIMITER: {
        idFromName: () => "one-global-object",
        get: () => ({ fetch: limiter }),
      },
    },
    limiter,
  };
}

function request(body: unknown = { scenario: "allow" }, init: RequestInit = {}) {
  const headers = new Headers({
    "CF-Connecting-IP": "192.0.2.10",
    "Content-Type": "application/json",
    "Origin": ORIGIN,
  });
  new Headers(init.headers).forEach((value, name) => headers.set(name, value));
  return new Request("https://proxy.example/api/scenario", {
    ...init,
    method: init.method ?? "POST",
    headers,
    body: init.body ?? JSON.stringify(body),
  });
}

function agent(status = 200, extra: object = {}) {
  return vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    Response.json({
      decision: "ALLOW",
      reason: "ALLOW",
      decided_by: "ratify_verification",
      verification_status: "authorized_agent",
      verification_duration_ms: 41,
      challenge_duration_ms: 12,
      proof_build_duration_ms: 28,
      proof_upload_duration_ms: 9,
      dispatch_duration_ms: 63,
      interceptor_duration_ms: 118,
      handler_invoked: true,
      handler_invocations: 7,
      requested_amount_minor: 42_000,
      requested_resource: "site:warehouse-seattle-01",
      requested_category: "electrical",
      requested_description: "Inspect and repair loading-bay lighting",
      authorized_max_amount_minor: 50_000,
      currency: "USD",
      authorized_currency: "USD",
      delegation_scope: "custom:work_order:create",
      delegation_resource: "site:warehouse-seattle-01",
      delegation_category: "electrical",
      delegation_audience: "maritime-ratify-demo-receiver",
      delegation_issued_at: 1_800_000_000,
      delegation_expires_at: 1_800_604_800,
      private_key: "must-be-dropped",
      ...extra,
    }, { status })
  );
}

describe("scenario proxy", () => {
  it.each([
    ["allow", "ALLOW", "ALLOW", "ratify_verification", "authorized_agent"],
    ["over_limit", "DENY", "DENY_LIMIT_EXCEEDED", "ratify_verification", "constraint_denied"],
    ["wrong_resource", "DENY", "DENY_RESOURCE_MISMATCH", "ratify_verification", "constraint_denied"],
    ["altered_operation", "DENY", "DENY_OPERATION_MISMATCH", "proof_carrier", null],
    ["expired", "DENY", "DENY_EXPIRED", "ratify_verification", "expired"],
    ["revoked", "DENY", "DENY_REVOKED", "ratify_verification", "revoked"],
    ["replay", "DENY", "DENY_REPLAY", "proof_carrier", null],
    ["wrong_agent", "DENY", "DENY_SUBJECT_MISMATCH", "receiver_precheck", null],
    ["copied_certificate", "DENY", "DENY_VERIFICATION_FAILED", "ratify_verification", "invalid"],
  ])("constructs and projects %s", async (scenario, decision, reason, decidedBy, verificationStatus) => {
    const { env } = environment();
    const fetchAgent = agent(200, {
      decision,
      reason,
      decided_by: decidedBy,
      verification_status: verificationStatus,
      handler_invoked: decision === "ALLOW",
      requested_amount_minor: scenario === "over_limit" ? 50_100 : 42_000,
    });
    const response = await handleRequest(request({ scenario }), env, fetchAgent);
    const body = await response.json<Record<string, unknown>>();
    expect(response.status).toBe(200);
    expect(body).toMatchObject({
      scenario,
      decision,
      reason,
      decided_by: decidedBy,
      verification_status: verificationStatus,
      handler_invoked: decision === "ALLOW",
      handler_invocations: 7,
    });
    expect(Object.keys(body).sort()).toEqual([
      "authorized_currency", "authorized_max_amount_minor", "challenge_duration_ms",
      "correlation_id", "currency", "decided_by", "decision", "delegation_audience",
      "delegation_category", "delegation_expires_at", "delegation_issued_at",
      "delegation_resource", "delegation_scope", "dispatch_duration_ms",
      "handler_invocations", "handler_invoked", "interceptor_duration_ms",
      "proof_build_duration_ms", "proof_upload_duration_ms", "reason",
      "requested_amount_minor", "requested_category", "requested_description",
      "requested_resource", "scenario", "timestamp", "upstream_duration_ms",
      "verification_duration_ms", "verification_status",
    ]);
    expect(body).toMatchObject({ requested_amount_minor: scenario === "over_limit" ? 50_100 : 42_000, authorized_max_amount_minor: 50_000, currency: "USD", authorized_currency: "USD" });
    const init = fetchAgent.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(JSON.stringify({ message: scenario }));
    expect(init.headers).toEqual({
      "Content-Type": "application/json",
      "X-Ratify-Demo-Token": `Bearer ${TOKEN}`,
    });
  });

  it("projects execution facts from the agent rather than local policy constants", async () => {
    const { env } = environment();
    const response = await handleRequest(request(), env, agent(200, {
      requested_amount_minor: 12_345,
      authorized_max_amount_minor: 23_456,
      currency: "CAD",
      authorized_currency: "EUR",
      delegation_scope: "custom:invoice:approve",
      delegation_resource: "account:test",
      delegation_category: "invoice",
      delegation_audience: "verifier-test",
      delegation_issued_at: 1_900_000_000,
      delegation_expires_at: 1_900_086_400,
    }));
    expect(await response.json()).toMatchObject({
      requested_amount_minor: 12_345,
      authorized_max_amount_minor: 23_456,
      currency: "CAD",
      authorized_currency: "EUR",
      delegation_scope: "custom:invoice:approve",
      delegation_resource: "account:test",
      delegation_category: "invoice",
      delegation_audience: "verifier-test",
      delegation_issued_at: 1_900_000_000,
      delegation_expires_at: 1_900_086_400,
    });
  });

  it.each([
    { scenario: "unknown" }, { scenario: null }, { scenario: [] },
    { scenario: {} }, { scenario: 1 }, { scenario: "allow", extra: true },
    { scenario: "allow", proof: "fake" },
  ])("rejects invalid or extended bodies", async (body) => {
    const { env, limiter } = environment();
    const fetchAgent = agent();
    const response = await handleRequest(request(body), env, fetchAgent);
    expect(response.status).toBe(400);
    expect(limiter).not.toHaveBeenCalled();
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("rejects duplicate scenario keys as ambiguous", async () => {
    const { env, limiter } = environment();
    const fetchAgent = agent();
    const response = await handleRequest(request(undefined, {
      body: '{"scenario":"allow","scenario":"over_limit"}',
    }), env, fetchAgent);
    expect(response.status).toBe(400);
    expect(limiter).not.toHaveBeenCalled();
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("does not forward client headers, query, cookies, or identifiers", async () => {
    const { env } = environment();
    const fetchAgent = agent();
    const supplied = new Request(
      "https://proxy.example/api/scenario?proof=fake",
      {
        method: "POST",
        headers: {
          "CF-Connecting-IP": "192.0.2.10",
          "Origin": ORIGIN,
          "Authorization": "Bearer attacker",
          "Cookie": "session=attacker",
          "X-Forwarded-For": "8.8.8.8",
        },
        body: JSON.stringify({ scenario: "allow" }),
      },
    );
    const response = await handleRequest(supplied, env, fetchAgent);
    expect(response.status).toBe(200);
    const init = fetchAgent.mock.calls[0][1] as RequestInit;
    expect(String(init.body)).toBe('{"message":"allow"}');
    expect(JSON.stringify(init)).not.toContain("attacker");
    expect(JSON.stringify(init)).not.toContain("proof");
  });

  it.each(["GET", "PUT", "DELETE"])("rejects %s without side effects", async (method) => {
    const { env, limiter } = environment();
    const fetchAgent = agent();
    const response = await handleRequest(new Request(
      "https://proxy.example/api/scenario", { method, headers: { Origin: ORIGIN } }
    ), env, fetchAgent);
    expect(response.status).toBe(404);
    expect(limiter).not.toHaveBeenCalled();
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("answers the exact console preflight without triggering a scenario", async () => {
    const { env, limiter } = environment();
    const fetchAgent = agent();
    const response = await handleRequest(new Request(
      "https://proxy.example/api/scenario",
      { method: "OPTIONS", headers: { Origin: ORIGIN } },
    ), env, fetchAgent);
    expect(response.status).toBe(204);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(ORIGIN);
    expect(limiter).not.toHaveBeenCalled();
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("rejects other paths", async () => {
    const { env, limiter } = environment();
    const response = await handleRequest(new Request(
      "https://proxy.example/other", { method: "POST", headers: { Origin: ORIGIN } }
    ), env, agent());
    expect(response.status).toBe(404);
    expect(limiter).not.toHaveBeenCalled();
  });

  it("uses the attested client address and ignores spoofed forwarding", async () => {
    const { env, limiter } = environment();
    await handleRequest(request({ scenario: "allow" }, {
      headers: { "X-Forwarded-For": "8.8.8.8" },
    }), env, agent());
    const limiterRequest = limiter.mock.calls[0][0] as Request;
    expect(await limiterRequest.json()).toMatchObject({ client: "192.0.2.10" });
  });

  it("fails closed when the limiter is unavailable", async () => {
    const { env } = environment({ limiterFailure: true });
    const fetchAgent = agent();
    const response = await handleRequest(request(), env, fetchAgent);
    expect(response.status).toBe(503);
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("does not contact the agent after a limit rejection", async () => {
    const { env } = environment({ allowed: false });
    const fetchAgent = agent();
    const response = await handleRequest(request(), env, fetchAgent);
    expect(response.status).toBe(429);
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("maps agent 401 and 503 to the same response", async () => {
    const { env } = environment();
    const first = await handleRequest(request(), env, agent(401));
    const second = await handleRequest(request(), env, agent(503));
    expect(first.status).toBe(second.status);
    expect(await first.text()).toBe(await second.text());
  });

  it("maps timeouts and invalid responses without internal text", async () => {
    const { env } = environment();
    const fetchAgent = vi.fn(async () => { throw new Error("redis://secret"); });
    const response = await handleRequest(request(), env, fetchAgent);
    expect(response.status).toBe(502);
    expect(await response.text()).toBe('{"error":"SCENARIO_UNAVAILABLE"}');
  });

  it.each([
    { decided_by: undefined },
    { decided_by: 7 },
    { verification_status: 7 },
  ])("refuses a result that omits or malforms its deciding layer", async (extra) => {
    const { env } = environment();
    const fetchAgent = agent(200, extra);
    const response = await handleRequest(request(), env, fetchAgent);
    expect(response.status).toBe(502);
    expect(await response.text()).toBe('{"error":"SCENARIO_UNAVAILABLE"}');
  });

  it("rejects foreign origins and marks every response no-store", async () => {
    const { env } = environment();
    const response = await handleRequest(request(undefined, {
      headers: { Origin: "https://evil.example" },
    }), env, agent());
    expect(response.status).toBe(403);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(ORIGIN);
  });

  it("rejects a missing origin before side effects", async () => {
    const { env, limiter } = environment();
    const fetchAgent = agent();
    const response = await handleRequest(new Request("https://proxy.example/api/scenario", { method: "POST", headers: { "CF-Connecting-IP": "192.0.2.10" }, body: '{"scenario":"allow"}' }), env, fetchAgent);
    expect(response.status).toBe(403);
    expect(limiter).not.toHaveBeenCalled();
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("requires Cloudflare's attested client address", async () => {
    const { env } = environment();
    const response = await handleRequest(new Request(
      "https://proxy.example/api/scenario",
      { method: "POST", headers: { Origin: ORIGIN }, body: '{"scenario":"allow"}' },
    ), env, agent());
    expect(response.status).toBe(503);
  });

  it("fails before limiting when the secret is missing", async () => {
    const { env, limiter } = environment();
    env.RATIFY_DEMO_TOKEN = "";
    const fetchAgent = agent();
    const response = await handleRequest(request(), env, fetchAgent);
    expect(response.status).toBe(503);
    expect(limiter).not.toHaveBeenCalled();
    expect(fetchAgent).not.toHaveBeenCalled();
  });

  it("exports the Worker fetch handler", async () => {
    const { env } = environment();
    const response = await worker.fetch(request({ scenario: "bad" }), env);
    expect(response.status).toBe(400);
  });

  it("never includes the token in observed responses", async () => {
    const { env } = environment({ limiterFailure: true });
    const responses = [
      await handleRequest(request({ scenario: "bad" }), env, agent()),
      await handleRequest(request(), env, agent(401)),
      await handleRequest(request(), env, agent()),
    ];
    for (const response of responses) {
      expect(await response.text()).not.toContain(TOKEN);
    }
  });
});

describe("durable limiter", () => {
  it("enforces one per-client count across separate instances", async () => {
    const storage = new TestStorage();
    const first = new ScenarioLimiter({ storage });
    const second = new ScenarioLimiter({ storage });
    const responses = await Promise.all(
      Array.from({ length: 11 }, (_, index) =>
        (index % 2 ? first : second).fetch(limitRequest("same-client"))
      ),
    );
    const results = await Promise.all(
      responses.map((response) => response.json<{ allowed: boolean }>()),
    );
    expect(results.filter((result) => result.allowed).length).toBe(10);
    expect(results.filter((result) => !result.allowed).length).toBe(1);
  });

  it("enforces the global budget across separate instances", async () => {
    const storage = new TestStorage();
    const first = new ScenarioLimiter({ storage });
    const second = new ScenarioLimiter({ storage });
    const responses = await Promise.all(
      Array.from({ length: 81 }, (_, index) =>
        (index % 2 ? first : second).fetch(limitRequest(`client-${index}`))
      ),
    );
    const results = await Promise.all(
      responses.map((response) => response.json<{ allowed: boolean }>()),
    );
    expect(results.filter((result) => result.allowed).length).toBe(80);
    expect(results.filter((result) => !result.allowed).length).toBe(1);
  });

  it("deletes client-address keys after their window expires", async () => {
    const storage = new TestStorage();
    const limiter = new ScenarioLimiter({ storage });
    await limiter.fetch(limitRequest("old-client", 1_000_000));
    expect(storage.values.has("client:old-client")).toBe(true);
    await limiter.fetch(limitRequest("new-client", 1_060_001));
    expect(storage.values.has("client:old-client")).toBe(false);
    expect(storage.values.has("client:new-client")).toBe(true);
  });
});

describe("runtime routing", () => {
  it.each([
    ["allow", "https://agent.example/chat", `Bearer ${TOKEN}`],
    ["copied_certificate", "https://agent.example/chat", `Bearer ${TOKEN}`],
    ["isolation_own", "https://agent-b.example/chat", `Bearer ${TOKEN}-b`],
    ["isolation_borrowed_certificate", "https://agent-b.example/chat", `Bearer ${TOKEN}-b`],
  ])("sends %s to its own runtime with that runtime's credential", async (
    scenario, url, credential,
  ) => {
    const { env } = environment();
    const fetchAgent = agent();
    await handleRequest(request({ scenario }), env, fetchAgent);
    expect(fetchAgent.mock.calls[0][0]).toBe(url);
    const init = fetchAgent.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Ratify-Demo-Token"])
      .toBe(credential);
  });

  it("never sends the second runtime's credential to the first", async () => {
    const { env } = environment();
    const fetchAgent = agent();
    await handleRequest(request({ scenario: "allow" }), env, fetchAgent);
    const init = fetchAgent.mock.calls[0][1] as RequestInit;
    expect(JSON.stringify(init.headers)).not.toContain(`${TOKEN}-b`);
  });

  it.each(["isolation_own", "allow"])(
    "fails closed for %s when its runtime is not configured",
    async (scenario) => {
      const { env } = environment();
      const fetchAgent = agent();
      const stripped = scenario.startsWith("isolation_")
        ? { ...env, RATIFY_DEMO_TOKEN_B: "" }
        : { ...env, RATIFY_DEMO_TOKEN: "" };
      const response = await handleRequest(request({ scenario }), stripped, fetchAgent);
      expect(response.status).toBe(503);
      expect(fetchAgent).not.toHaveBeenCalled();
    },
  );
});

describe("measured spans", () => {
  it("forwards every span the runtimes measured", async () => {
    const { env } = environment();
    const response = await handleRequest(request(), env, agent());
    const body = await response.json<Record<string, unknown>>();
    expect(body).toMatchObject({
      verification_duration_ms: 41,
      proof_build_duration_ms: 28,
      interceptor_duration_ms: 118,
    });
  });

  it("accepts a null span for a refusal reached before verification", async () => {
    const { env } = environment();
    const fetchAgent = agent(200, {
      decision: "DENY", reason: "DENY_SUBJECT_MISMATCH",
      decided_by: "receiver_precheck", verification_status: null,
      verification_duration_ms: null, handler_invoked: false,
    });
    const response = await handleRequest(request(), env, fetchAgent);
    expect(response.status).toBe(200);
    expect((await response.json<Record<string, unknown>>())
      .verification_duration_ms).toBeNull();
  });

  it.each(["verification_duration_ms", "proof_build_duration_ms"])(
    "fails closed when %s is malformed", async (field) => {
      const { env } = environment();
      const response = await handleRequest(
        request(), env, agent(200, { [field]: "fast" }),
      );
      expect(response.status).toBe(502);
    },
  );
});

describe("upstream timing", () => {
  it("reports a duration the proxy measured around its own upstream call", async () => {
    const { env } = environment();
    const fetchAgent = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
      return (agent())(new Request("https://agent.example"));
    });
    const response = await handleRequest(request(), env, fetchAgent as never);
    const body = await response.json<Record<string, unknown>>();
    expect(typeof body.upstream_duration_ms).toBe("number");
    // A measured value, not a placeholder: it has to reflect the real delay
    // and stay below the wall clock of the whole request.
    expect(body.upstream_duration_ms as number).toBeGreaterThanOrEqual(20);
    expect(body.upstream_duration_ms as number).toBeLessThan(5_000);
  });

  it("reports no timing when the upstream call fails", async () => {
    const { env } = environment();
    const fetchAgent = vi.fn(async () => { throw new Error("upstream down"); });
    const response = await handleRequest(request(), env, fetchAgent);
    expect(response.status).toBe(502);
    expect(await response.text()).toBe('{"error":"SCENARIO_UNAVAILABLE"}');
  });
});
