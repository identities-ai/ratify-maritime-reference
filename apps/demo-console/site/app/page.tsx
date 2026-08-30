"use client";

import Image from "next/image";
import { useState } from "react";

const API = "https://maritime-api.ratifyprotocol.com/api/scenario";
const stages = [
  ["01", "Principal", "Delegates bounded authority"],
  ["02", "Maritime agent", "Recognizes the work order"],
  ["03", "External boundary", "Carries proof by reference"],
  ["04", "Ratify receiver", "Verifies identity and constraints"],
  ["05", "Protected handler", "Runs only after ALLOW"],
];

type Scenario =
  | "allow"
  | "over_limit"
  | "wrong_resource"
  | "altered_operation"
  | "expired"
  | "revoked"
  | "replay"
  | "wrong_agent"
  | "copied_certificate";
type IsolationScenario = "isolation_own" | "isolation_wrong_site" | "isolation_borrowed_certificate";

const scenarios: {
  id: Scenario;
  tag: string;
  title: string;
  detail: string;
  expectedDecision: "ALLOW" | "DENY";
  expectedReason: string;
}[] = [
  { id: "allow", tag: "WITHIN AUTHORITY", title: "$420 Seattle electrical repair", detail: "Exact agent, action, resource, bounds, and validity.", expectedDecision: "ALLOW", expectedReason: "ALLOW" },
  { id: "over_limit", tag: "EXCEEDED LIMIT", title: "$501 Seattle electrical repair", detail: "The same agent asks for more than the signed $500 ceiling.", expectedDecision: "DENY", expectedReason: "DENY_LIMIT_EXCEEDED" },
  { id: "wrong_resource", tag: "WRONG RESOURCE", title: "$420 Portland electrical repair", detail: "The signed authority names the Seattle warehouse only.", expectedDecision: "DENY", expectedReason: "DENY_RESOURCE_MISMATCH" },
  { id: "altered_operation", tag: "ALTERED OPERATION", title: "Action changed after proof creation", detail: "The dispatched description no longer matches the signed presentation.", expectedDecision: "DENY", expectedReason: "DENY_OPERATION_MISMATCH" },
  { id: "expired", tag: "EXPIRED", title: "Expired signed delegation", detail: "The credential is authentic but outside its validity window.", expectedDecision: "DENY", expectedReason: "DENY_EXPIRED" },
  { id: "revoked", tag: "REVOKED", title: "Revoked signed delegation", detail: "The credential is authentic and current, but the receiver has revoked it.", expectedDecision: "DENY", expectedReason: "DENY_REVOKED" },
  { id: "replay", tag: "REPLAY", title: "Consumed proof reference reused", detail: "One valid use is followed by a second attempt with the same one-time proof.", expectedDecision: "DENY", expectedReason: "DENY_REPLAY" },
  { id: "wrong_agent", tag: "WRONG AGENT", title: "Different valid agent credential", detail: "Another agent presents authority to a challenge issued for this agent.", expectedDecision: "DENY", expectedReason: "DENY_SUBJECT_MISMATCH" },
  { id: "copied_certificate", tag: "COPIED CERTIFICATE", title: "Genuine certificate, wrong holder", detail: "The signed delegation is authentic and names this agent, but the presenter does not hold its private key.", expectedDecision: "DENY", expectedReason: "DENY_VERIFICATION_FAILED" },
];
type Result = {
  correlation_id: string;
  scenario: Scenario;
  decision: string;
  reason: string;
  decided_by: string;
  verification_status: string | null;
  handler_invoked: boolean;
  handler_invocations: number;
  requested_amount_minor: number;
  requested_resource: string;
  requested_category: string;
  requested_description: string;
  authorized_max_amount_minor: number;
  currency: string;
  authorized_currency: string;
  delegation_scope: string;
  delegation_resource: string;
  delegation_category: string;
  delegation_audience: string;
  delegation_issued_at: number;
  delegation_expires_at: number;
  timestamp: string;
};

const deciderLabels: Record<string, string> = {
  ratify_verification: "Ratify proof verification",
  receiver_policy: "Receiver-local policy",
  receiver_precheck: "Receiver request binding",
  proof_carrier: "Proof-carrier binding",
  receiver_error: "Receiver fault, failed closed",
};
const isolationScenarios: { id: IsolationScenario; title: string; detail: string; expectedDecision: "ALLOW" | "DENY"; expectedReason: string }[] = [
  { id: "isolation_own", title: "Agent B with its own authority", detail: "Portland site, $200 bound, separate Maritime runtime.", expectedDecision: "ALLOW", expectedReason: "ALLOW" },
  { id: "isolation_wrong_site", title: "Agent B requests Agent A's site", detail: "A legitimately authorized agent cannot cross its tenant boundary.", expectedDecision: "DENY", expectedReason: "DENY_RESOURCE_MISMATCH" },
  { id: "isolation_borrowed_certificate", title: "Agent B presents Agent A's certificate", detail: "A genuine certificate cannot be used without the matching private key.", expectedDecision: "DENY", expectedReason: "DENY_VERIFICATION_FAILED" },
];

export default function Home() {
  const [pending, setPending] = useState<Scenario | null>(null);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [results, setResults] = useState<Partial<Record<Scenario, Result>>>({});
  const [isolationResults, setIsolationResults] = useState<Partial<Record<IsolationScenario, Result>>>({});
  const [runningSuite, setRunningSuite] = useState(false);
  const [error, setError] = useState<{ title: string; body: string } | null>(null);

  async function execute(scenario: Scenario | IsolationScenario, target: "adversarial" | "isolation" = "adversarial"): Promise<Result | null> {
    setPending(scenario);
    setProgress(0);
    setError(null);
    setResult(null);
    const timers = [
      window.setTimeout(() => setProgress(1), 1500),
      window.setTimeout(() => setProgress(2), 4000),
      window.setTimeout(() => setProgress(3), 7000),
    ];
    try {
      const response = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario }),
      });
      if (response.status === 429) {
        setError({
          title: "Demo limit reached",
          body: "Try again in a minute.",
        });
        return;
      }
      if (!response.ok) throw new Error();
      const executed = await response.json() as Result;
      setResult(executed);
      if (target === "isolation") setIsolationResults((current) => ({ ...current, [scenario as IsolationScenario]: executed }));
      else setResults((current) => ({ ...current, [scenario as Scenario]: executed }));
      return executed;
    } catch {
      setError({
        title: "Runtime did not return a verified result",
        body: "The isolated Maritime runtimes may still be waking. No automatic retry was attempted. Wait a few seconds, then try again.",
      });
      return null;
    } finally {
      timers.forEach(window.clearTimeout);
      setPending(null);
    }
  }

  async function run(scenario: Scenario) {
    setResults({});
    await execute(scenario);
  }

  async function runIsolation(scenario: IsolationScenario) {
    await execute(scenario, "isolation");
  }

  async function runAll() {
    setRunningSuite(true);
    setResults({});
    setResult(null);
    setError(null);
    for (const scenario of scenarios) {
      const executed = await execute(scenario.id);
      if (executed === null) break;
    }
    setRunningSuite(false);
  }

  const progressCopy = [
    ["Sending the signed request", "The browser is contacting the Maritime-hosted agent."],
    ["Waiting for the isolated runtimes", "A sleeping Maritime runtime may need several seconds to become ready."],
    ["Waiting for receiver verification", "The agent is requesting a decision from the separately deployed Ratify receiver."],
    ["Still waiting for verified evidence", "The result will say whether protected code ran. This page will not assume or automatically retry."],
  ][progress];

  const money = (minor: number, currency: string) => new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100) + ` ${currency}`;
  const requested = result ? money(result.requested_amount_minor, result.currency) : "";
  const allowed = result?.decision === "ALLOW";
  const selectedScenario = result
    ? scenarios.find((scenario) => scenario.id === result.scenario)
    : null;
  const decisionExplanation = allowed
    ? "The receiver verified the signed delegation and exact request, then called the protected create_work_order handler."
    : selectedScenario?.detail ?? "The receiver rejected the request before protected code ran.";
  const passed = (scenario: typeof scenarios[number], executed: Result | undefined) =>
    executed?.decision === scenario.expectedDecision &&
    executed.reason === scenario.expectedReason &&
    executed.handler_invoked === (scenario.expectedDecision === "ALLOW");

  return (
    <main>
      <header className="nav">
        <a className="brand" href="https://labs.ratifyprotocol.com/" aria-label="Ratify Labs home">
          <Image src="/maritime/ratify-logo.png" alt="" width={28} height={28} unoptimized />
          <span>RATIFY <b>LABS</b></span>
        </a>
        <span className="live"><i /> Live pilot</span>
      </header>

      <section className="hero">
        <p className="eyebrow">MARITIME × RATIFY</p>
        <h1>An agent can ask.<br /><em>Authority decides.</em></h1>
        <p className="lede">Run one permitted work order and eight adversarial requests against the same Maritime-hosted authorization boundary. Every denial must stop before protected code runs.</p>
        <div className="hero-links" aria-label="Learn about the technologies in this pilot">
          <a href="https://maritime.sh/" target="_blank" rel="noreferrer">About Maritime ↗</a>
          <a href="https://ratifyprotocol.com/" target="_blank" rel="noreferrer">About Ratify Protocol ↗</a>
        </div>
      </section>

      <section className="lab" aria-labelledby="lab-title">
        <div className="authority-card" aria-labelledby="authority-title">
          <div className="authority-intro">
            <p className="kicker">THE SIGNED PERMISSION</p>
            <h2 id="authority-title">What authority does this agent carry?</h2>
            <p>A demo principal signed a short-lived delegation bound to this Maritime agent&rsquo;s key. It is permission to request one narrowly described kind of work—not a general credential and not permission chosen by the model.</p>
          </div>
          <dl className="authority-facts">
            <div><dt>Operation</dt><dd>Create a work order</dd></div>
            <div><dt>Protocol scope</dt><dd><code>custom:work_order:create</code></dd></div>
            <div><dt>Resource</dt><dd>Seattle warehouse 01</dd></div>
            <div><dt>Category</dt><dd>Electrical work</dd></div>
            <div><dt>Signed ceiling</dt><dd>$500.00 USD</dd></div>
            <div><dt>Validity</dt><dd>Seven days; exact live expiry shown after execution</dd></div>
          </dl>
          <p className="meaning"><b>ALLOW</b> means the receiver verified the delegation and invoked protected code. <b>DENY</b> means the receiver stopped the request before that code ran.</p>
        </div>

        <div className="lab-head">
          <div><p className="kicker">LIVE AUTHORIZATION LAB</p><h2 id="lab-title">Allow plus eight adversarial denials</h2></div>
          <button className="run-all" onClick={runAll} disabled={pending !== null || runningSuite}>
            {runningSuite ? `Running ${Object.keys(results).length + 1} of 9…` : "Run full adversarial gate →"}
          </button>
        </div>
        <p className="harness-note">Every scenario dispatches a fixed, enumerated action. This public demo runs a deterministic tool-call harness in place of a reasoning model, because the model is not the security decision. The receiver reaches its decision without trusting the prompt, the model, or the agent&rsquo;s transport credential.</p>
        <div className="scenario-grid">
          {scenarios.map((scenario) => {
            const executed = results[scenario.id];
            return <button
              key={scenario.id}
              onClick={() => run(scenario.id)}
              disabled={pending !== null || runningSuite}
              className={executed ? (passed(scenario, executed) ? "scenario-pass" : "scenario-fail") : ""}
            >
              <span className="scenario-tag">{scenario.tag}</span>
              <strong>{scenario.title}</strong>
              <span className="scenario-detail">{scenario.detail}</span>
              <span className="button-action">
                {pending === scenario.id
                  ? "Running…"
                  : executed
                    ? `${executed.reason} · ${passed(scenario, executed) ? "PASS" : "CHECK"}`
                    : "Run scenario →"}
              </span>
            </button>;
          })}
        </div>

        {Object.keys(results).length > 1 && <section className="gate-results" aria-labelledby="gate-results-title">
          <div><p className="kicker">EXECUTED EVIDENCE</p><h3 id="gate-results-title">Adversarial gate results</h3></div>
          <ol>
            {scenarios.map((scenario, index) => {
              const executed = results[scenario.id];
              return <li key={scenario.id}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><b>{scenario.tag}</b><small>{executed ? executed.reason : "Waiting"}</small></div>
                <strong className={executed && passed(scenario, executed) ? "pass" : ""}>
                  {executed ? (passed(scenario, executed) ? "PASS" : "CHECK") : "—"}
                </strong>
              </li>;
            })}
          </ol>
          <p>Every row is populated from a request executed against the deployed Maritime agent and receiver. No outcome is prefilled.</p>
        </section>}

        <section className="isolation-check" aria-labelledby="isolation-title">
          <div className="isolation-heading"><p className="kicker">MARITIME RUNTIME ISOLATION</p><h3 id="isolation-title">Runtime isolation check</h3><p>Agent B runs the same image in a separate Maritime runtime with Portland authority capped at $200. These checks are separate from the nine-case adversarial gate.</p></div>
          <div className="isolation-grid">{isolationScenarios.map((scenario) => { const executed = isolationResults[scenario.id]; const passes = executed?.decision === scenario.expectedDecision && executed.reason === scenario.expectedReason && executed.handler_invoked === (scenario.expectedDecision === "ALLOW"); return <button key={scenario.id} onClick={() => runIsolation(scenario.id)} disabled={pending !== null || runningSuite} className={executed ? (passes ? "scenario-pass" : "scenario-fail") : ""}><strong>{scenario.title}</strong><span className="scenario-detail">{scenario.detail}</span><span className="button-action">{pending === scenario.id ? "Running…" : executed ? `${executed.reason} · ${passes ? "PASS" : "CHECK"}` : "Run check →"}</span></button>; })}</div>
          <p className="isolation-note">The two runtimes use byte-identical agent images but different subjects, credentials, and bounds. Results are live responses; no row is prefilled.</p>
        </section>

        {(pending || result || error) && <div className="execution" aria-live="polite">
          <div className="stages" aria-label="Executed request stages">
            {stages.map(([number, name, detail], index) => {
              const state = result
                ? index < 4 || (index === 4 && result.decision === "ALLOW")
                  ? "done"
                  : "blocked"
                : pending && index === 0
                  ? "active"
                  : "";
              return <div className={`stage ${state}`} key={name}>
              <span className="stage-number">{number}</span><div><strong>{name}</strong><small>{detail}</small></div>
              </div>;
            })}
          </div>
          {pending && <div className="result pending" role="status">
            <span className="spinner" />
            <span className="progress-copy" key={progress}><b>{progressCopy[0]}</b><small>{progressCopy[1]}</small></span>
          </div>}
          {error && <div className="result error" role="alert"><b>{error.title}</b><span>{error.body}</span></div>}
          {result && <div className={`result ${allowed ? "allow" : "deny"}`} role="status">
            <div className="decision-icon" aria-hidden="true">{allowed ? "✓" : "×"}</div>
            <div className="decision-copy"><p>AUTHORITY RESULT</p><h3>{result.decision}</h3><span>{decisionExplanation}</span></div>
            <dl>
              <div><dt>Requested</dt><dd>{requested}</dd></div>
              <div><dt>Authorized bound</dt><dd>≤ {money(result.authorized_max_amount_minor, result.authorized_currency)}</dd></div>
              <div><dt>Receiver reason</dt><dd><code>{result.reason}</code></dd></div>
              <div><dt>Decided by</dt><dd>{deciderLabels[result.decided_by] ?? result.decided_by}</dd></div>
              <div><dt>Handler entered for this request</dt><dd>{result.handler_invoked ? "Yes" : "No"}</dd></div>
              <div><dt>Shared receiver handler count</dt><dd>{result.handler_invocations}</dd></div>
              <div><dt>Requested resource</dt><dd><code>{result.requested_resource}</code></dd></div>
              <div><dt>Requested category</dt><dd>{result.requested_category}</dd></div>
              <div><dt>Requested operation detail</dt><dd>{result.requested_description}</dd></div>
              <div><dt>Delegated scope</dt><dd><code>{result.delegation_scope}</code></dd></div>
              <div><dt>Delegated resource</dt><dd><code>{result.delegation_resource}</code></dd></div>
              <div><dt>Delegated category</dt><dd>{result.delegation_category}</dd></div>
              <div><dt>Delegation expires</dt><dd>{new Date(result.delegation_expires_at * 1000).toLocaleString()}</dd></div>
            </dl>
            <p className="counter-note">This is a receiver-wide counter shared by every demo visitor, not your session count.</p>
            <details><summary>Technical evidence</summary><p>Deciding layer <code>{result.decided_by}</code> · Ratify verification status <code>{result.verification_status ?? "not reached"}</code> · Audience <code>{result.delegation_audience}</code> · Delegation issued {new Date(result.delegation_issued_at * 1000).toLocaleString()} · Correlation {result.correlation_id} · Executed {new Date(result.timestamp).toLocaleString()} · No keys, proof material, or private identifiers are displayed.</p></details>
          </div>}
        </div>}
      </section>

      <section className="explainer">
        <p className="kicker">WHAT THIS PROVES</p>
        <h2>The model is not the authority boundary.</h2>
        <div className="proof-grid">
          <article><span>01</span><h3>Delegation</h3><p>A principal signs permission for one agent key. Copying the certificate does not give another agent that authority.</p></article>
          <article><span>02</span><h3>Scope and bounds</h3><p>The scope names the permitted operation. Resource, category, currency, amount, and audience narrow where it applies.</p></article>
          <article><span>03</span><h3>Expiry and verification</h3><p>After expiry the permission is invalid. Before every action, the receiver also checks freshness, replay, revocation, and its own policy.</p></article>
        </div>
      </section>

      <section className="why" aria-labelledby="why-title">
        <div className="why-heading">
          <p className="kicker">WHY THIS REFERENCE EXISTS</p>
          <h2 id="why-title">Isolation controls where an agent runs.<br />Delegation controls what it may do.</h2>
          <p>Prompts can guide a model, and API keys can identify a caller. Neither is a precise grant for one agent to perform one bounded action. This reference combines an isolated Maritime runtime with authority that a separate Ratify receiver verifies before business logic executes.</p>
        </div>

        <div className="partner-grid">
          <article>
            <span>MARITIME</span>
            <h3>Isolated execution</h3>
            <p>Runs the LangChain agent and receiver in separate managed runtimes, keeping execution boundaries explicit.</p>
            <a href="https://maritime.sh/" target="_blank" rel="noreferrer">Explore Maritime ↗</a>
          </article>
          <article>
            <span>RATIFY</span>
            <h3>Portable authority</h3>
            <p>Binds permission to the agent&rsquo;s key and exact scope, resource, category, amount, audience, and validity window.</p>
            <a href="https://ratifyprotocol.com/" target="_blank" rel="noreferrer">Explore Ratify Protocol ↗</a>
          </article>
          <article>
            <span>TOGETHER</span>
            <h3>Enforcement before action</h3>
            <p>The receiver trusts neither the prompt nor a model assertion. It verifies proof and local policy before protected code runs.</p>
            <a href="https://github.com/identities-ai/ratify-maritime-reference" target="_blank" rel="noreferrer">Inspect the implementation ↗</a>
          </article>
        </div>

        <div className="architecture" aria-labelledby="architecture-title">
          <div className="architecture-copy">
            <p className="kicker">EXECUTION PATH</p>
            <h3 id="architecture-title">From signed permission to protected code</h3>
            <p>The delegation travels with the agent as a verifiable public credential. The private agent key never enters the browser.</p>
          </div>
          <ol className="architecture-flow">
            <li><span>01</span><b>Principal</b><small>Signs bounded authority</small></li>
            <li><span>02</span><b>Maritime agent</b><small>Builds the requested action</small></li>
            <li><span>03</span><b>Ratify proof</b><small>Binds identity and action</small></li>
            <li><span>04</span><b>Receiver</b><small>Verifies proof and policy</small></li>
            <li><span>05</span><b>Handler</b><small>Runs only after ALLOW</small></li>
          </ol>
        </div>

        <div className="evidence-boundary">
          <article>
            <p className="kicker">WHAT THE LIVE RESULT PROVES</p>
            <ul>
              <li>The same agent can be allowed or denied without changing its identity.</li>
              <li>Seven distinct authority failures are stopped before the protected handler.</li>
              <li>The displayed scope, amount, bound, and expiry come from live execution evidence.</li>
            </ul>
          </article>
          <article>
            <p className="kicker">BOUNDARY OF THE CLAIM</p>
            <ul>
              <li>This is an open reference implementation, not a production service or Maritime endorsement.</li>
              <li>The receiver is separately deployed but currently operated by Ratify for this pilot.</li>
              <li>The shared counter is system-wide evidence, not a visitor-specific activity record.</li>
            <li>Scenarios are enumerated rather than chosen by a model, and every result on this page is reported by the Ratify-operated deployment itself.</li>
            <li>Deployment identifiers in the published results file are recorded by the operator. They are not yet attested by Maritime.</li>
            </ul>
          </article>
        </div>
      </section>

      <footer><span>Open-source pilot implementation · Not a Maritime endorsement</span><a href="https://github.com/identities-ai/ratify-maritime-reference">View source ↗</a></footer>
    </main>
  );
}
