const SCENARIOS = new Set([
  "allow",
  "over_limit",
  "wrong_resource",
  "altered_operation",
  "expired",
  "revoked",
  "replay",
  "wrong_agent",
  "copied_certificate",
]);
const SCENARIO_PATTERN = [
  "allow",
  "over_limit",
  "wrong_resource",
  "altered_operation",
  "expired",
  "revoked",
  "replay",
  "wrong_agent",
  "copied_certificate",
].join("|");
const WINDOW_MS = 60_000;
const PER_CLIENT_LIMIT = 10;
const GLOBAL_LIMIT = 80;
// Both Maritime runtimes auto-sleep independently. A request that arrives after
// an idle period has to wake the agent and then the receiver, and each wake was
// measured between 12 and 29 seconds. A 15 second budget turned that into a
// failed scenario; this budget turns it into a slow one, which is what the
// console's staged waiting copy already describes.
const AGENT_TIMEOUT_MS = 60_000;

interface RateLimitResult {
  allowed: boolean;
}

interface LimiterStub {
  fetch(request: Request): Promise<Response>;
}

interface Env {
  AGENT_URL: string;
  CONSOLE_ORIGIN: string;
  RATIFY_DEMO_TOKEN: string;
  SCENARIO_LIMITER: {
    idFromName(name: string): unknown;
    get(id: unknown): LimiterStub;
  };
}

interface Storage {
  transaction<T>(callback: (transaction: Transaction) => Promise<T>): Promise<T>;
}

interface Transaction {
  get<T>(key: string): Promise<T | undefined>;
  put<T>(key: string, value: T): Promise<void>;
  list<T>(options: { prefix: string }): Promise<Map<string, T>>;
  delete(key: string): Promise<boolean>;
}

interface DurableState {
  storage: Storage;
}

export class ScenarioLimiter {
  constructor(private readonly state: DurableState) {}

  async fetch(request: Request): Promise<Response> {
    try {
      const payload = await request.json<unknown>();
      if (
        typeof payload !== "object" || payload === null ||
        Object.keys(payload).length !== 2 ||
        typeof (payload as Record<string, unknown>).client !== "string" ||
        typeof (payload as Record<string, unknown>).now !== "number"
      ) {
        return Response.json({ allowed: false }, { status: 400 });
      }
      const client = (payload as { client: string }).client;
      const now = (payload as { now: number }).now;
      const allowed = await this.state.storage.transaction(async (transaction) => {
        const global = trim(
          await transaction.get<number[]>("global") ?? [], now
        );
        const storedClients = await transaction.list<number[]>({ prefix: "client:" });
        for (const [key, hits] of storedClients) {
          const active = trim(hits, now);
          if (active.length === 0) {
            await transaction.delete(key);
          } else if (active.length !== hits.length) {
            await transaction.put(key, active);
          }
        }
        const clientKey = `client:${client}`;
        const clientHits = trim(
          await transaction.get<number[]>(clientKey) ?? [], now
        );
        if (global.length >= GLOBAL_LIMIT || clientHits.length >= PER_CLIENT_LIMIT) {
          return false;
        }
        global.push(now);
        clientHits.push(now);
        await transaction.put("global", global);
        await transaction.put(clientKey, clientHits);
        return true;
      });
      return Response.json({ allowed });
    } catch {
      return Response.json({ allowed: false }, { status: 503 });
    }
  }
}

function trim(hits: number[], now: number): number[] {
  return hits.filter((hit) => Number.isFinite(hit) && hit > now - WINDOW_MS);
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handleRequest(request, env);
  },
};

export async function handleRequest(
  request: Request,
  env: Env,
  fetchAgent: typeof fetch = fetch,
): Promise<Response> {
  const origin = request.headers.get("Origin");
  if (origin !== env.CONSOLE_ORIGIN) {
    return reply({ error: "INVALID_ORIGIN" }, 403, env.CONSOLE_ORIGIN);
  }

  const url = new URL(request.url);
  if (request.method === "OPTIONS" && url.pathname === "/api/scenario") {
    return new Response(null, {
      status: 204,
      headers: responseHeaders(env.CONSOLE_ORIGIN),
    });
  }
  if (request.method !== "POST" || url.pathname !== "/api/scenario") {
    return reply({ error: "INVALID_REQUEST" }, 404, env.CONSOLE_ORIGIN);
  }

  const client = request.headers.get("CF-Connecting-IP");
  if (!client) {
    return reply({ error: "SCENARIO_UNAVAILABLE" }, 503, env.CONSOLE_ORIGIN);
  }

  let scenario: string;
  try {
    const raw = await request.text();
    if (raw.length > 256) throw new Error();
    const match = new RegExp(
      `^\\s*\\{\\s*"scenario"\\s*:\\s*"(${SCENARIO_PATTERN})"\\s*\\}\\s*$`,
    ).exec(raw);
    if (!match || !SCENARIOS.has(match[1])) throw new Error();
    scenario = match[1];
  } catch {
    return reply({ error: "INVALID_REQUEST" }, 400, env.CONSOLE_ORIGIN);
  }

  if (
    typeof env.RATIFY_DEMO_TOKEN !== "string" ||
    env.RATIFY_DEMO_TOKEN.length === 0
  ) {
    return reply({ error: "SCENARIO_UNAVAILABLE" }, 503, env.CONSOLE_ORIGIN);
  }

  try {
    const limiter = env.SCENARIO_LIMITER.get(
      env.SCENARIO_LIMITER.idFromName("public-demo")
    );
    const limited = await limiter.fetch(new Request("https://limiter/check", {
      method: "POST",
      body: JSON.stringify({ client, now: Date.now() }),
    }));
    if (!limited.ok) throw new Error();
    const result = await limited.json<RateLimitResult>();
    if (result.allowed !== true) {
      return reply({ error: "RATE_LIMITED" }, 429, env.CONSOLE_ORIGIN);
    }
  } catch {
    return reply({ error: "SCENARIO_UNAVAILABLE" }, 503, env.CONSOLE_ORIGIN);
  }

  try {
    const agent = await fetchAgent(`${env.AGENT_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Ratify-Demo-Token": `Bearer ${env.RATIFY_DEMO_TOKEN}`,
      },
      body: JSON.stringify({ message: scenario }),
      signal: AbortSignal.timeout(AGENT_TIMEOUT_MS),
    });
    if (!agent.ok) throw new Error();
    const payload: unknown = await agent.json();
    if (typeof payload !== "object" || payload === null) throw new Error();
    const value = payload as Record<string, unknown>;
    if (
      typeof value.decision !== "string" ||
      typeof value.reason !== "string" ||
      typeof value.decided_by !== "string" ||
      !(typeof value.verification_status === "string" ||
        value.verification_status === null) ||
      typeof value.handler_invoked !== "boolean" ||
      typeof value.handler_invocations !== "number" ||
      typeof value.requested_amount_minor !== "number" ||
      typeof value.requested_resource !== "string" ||
      typeof value.requested_category !== "string" ||
      typeof value.requested_description !== "string" ||
      typeof value.authorized_max_amount_minor !== "number" ||
      typeof value.currency !== "string" ||
      typeof value.authorized_currency !== "string" ||
      typeof value.delegation_scope !== "string" ||
      typeof value.delegation_resource !== "string" ||
      typeof value.delegation_category !== "string" ||
      typeof value.delegation_audience !== "string" ||
      typeof value.delegation_issued_at !== "number" ||
      typeof value.delegation_expires_at !== "number"
    ) {
      throw new Error();
    }
    return reply({
      correlation_id: crypto.randomUUID(),
      scenario,
      decision: value.decision,
      reason: value.reason,
      decided_by: value.decided_by,
      verification_status: value.verification_status,
      handler_invoked: value.handler_invoked,
      handler_invocations: value.handler_invocations,
      requested_amount_minor: value.requested_amount_minor,
      requested_resource: value.requested_resource,
      requested_category: value.requested_category,
      requested_description: value.requested_description,
      authorized_max_amount_minor: value.authorized_max_amount_minor,
      currency: value.currency,
      authorized_currency: value.authorized_currency,
      delegation_scope: value.delegation_scope,
      delegation_resource: value.delegation_resource,
      delegation_category: value.delegation_category,
      delegation_audience: value.delegation_audience,
      delegation_issued_at: value.delegation_issued_at,
      delegation_expires_at: value.delegation_expires_at,
      timestamp: new Date().toISOString(),
    }, 200, env.CONSOLE_ORIGIN);
  } catch {
    return reply({ error: "SCENARIO_UNAVAILABLE" }, 502, env.CONSOLE_ORIGIN);
  }
}

function reply(body: object, status: number, origin: string): Response {
  return Response.json(body, {
    status,
    headers: responseHeaders(origin),
  });
}

function responseHeaders(origin: string): HeadersInit {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'",
    "Vary": "Origin",
  };
}
