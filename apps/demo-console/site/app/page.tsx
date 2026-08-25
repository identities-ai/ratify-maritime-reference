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

type Scenario = "allow" | "over_limit";
type Result = {
  correlation_id: string;
  scenario: Scenario;
  decision: string;
  reason: string;
  handler_invocations: number;
  requested_amount_minor: number;
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

export default function Home() {
  const [pending, setPending] = useState<Scenario | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<{ title: string; body: string } | null>(null);

  async function run(scenario: Scenario) {
    setPending(scenario);
    setError(null);
    setResult(null);
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
      setResult(await response.json() as Result);
    } catch {
      setError({
        title: "Unavailable",
        body: "The live scenario is temporarily unavailable. Please try again shortly.",
      });
    } finally {
      setPending(null);
    }
  }

  const money = (minor: number, currency: string) => new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100) + ` ${currency}`;
  const requested = result ? money(result.requested_amount_minor, result.currency) : "";
  const allowed = result?.decision === "ALLOW";
  const decisionExplanation = allowed
    ? "The receiver verified the signed delegation and this exact request stayed within every bound. It called the protected create_work_order handler."
    : "The receiver recognized the same agent and work-order scope, but the requested amount exceeded the signed ceiling. It rejected the request without calling the protected handler.";

  return (
    <main>
      <header className="nav">
        <a className="brand" href="https://labs.ratifyprotocol.com/" aria-label="Ratify Labs home">
          <Image src="/maritime/ratify-logo.png" alt="" width={28} height={28} unoptimized />
          <span>RATIFY <b>LABS</b></span>
        </a>
        <span className="live"><i /> Live reference</span>
      </header>

      <section className="hero">
        <p className="eyebrow">MARITIME × RATIFY</p>
        <h1>An agent can ask.<br /><em>Authority decides.</em></h1>
        <p className="lede">Run the same Maritime agent twice. A signed delegation permits one work order and rejects the other before protected code runs.</p>
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
          <div><p className="kicker">LIVE AUTHORIZATION LAB</p><h2 id="lab-title">Choose a work order</h2></div>
          <p>Same agent · Same site · One changed value</p>
        </div>
        <div className="scenario-grid">
          <button onClick={() => run("allow")} disabled={pending !== null}>
            <span className="scenario-tag">WITHIN AUTHORITY</span>
            <strong>Inspect and repair loading-bay lighting</strong>
            <span className="money">$420.00 <small>USD</small></span>
            <span className="button-action">{pending === "allow" ? "Running…" : "Run allowed scenario →"}</span>
          </button>
          <button onClick={() => run("over_limit")} disabled={pending !== null}>
            <span className="scenario-tag">ABOVE SIGNED LIMIT</span>
            <strong>Inspect and repair loading-bay lighting</strong>
            <span className="money">$501.00 <small>USD</small></span>
            <span className="button-action">{pending === "over_limit" ? "Running…" : "Run denied scenario →"}</span>
          </button>
        </div>

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
          {pending && <div className="result pending"><span className="spinner" />Executing the signed request…</div>}
          {error && <div className="result error" role="alert"><b>{error.title}</b><span>{error.body}</span></div>}
          {result && <div className={`result ${allowed ? "allow" : "deny"}`} role="status">
            <div className="decision-icon" aria-hidden="true">{allowed ? "✓" : "×"}</div>
            <div className="decision-copy"><p>AUTHORITY RESULT</p><h3>{result.decision}</h3><span>{decisionExplanation}</span></div>
            <dl>
              <div><dt>Requested</dt><dd>{requested}</dd></div>
              <div><dt>Authorized bound</dt><dd>≤ {money(result.authorized_max_amount_minor, result.authorized_currency)}</dd></div>
              <div><dt>Receiver reason</dt><dd><code>{result.reason}</code></dd></div>
              <div><dt>Shared receiver handler count</dt><dd>{result.handler_invocations}</dd></div>
              <div><dt>Delegated scope</dt><dd><code>{result.delegation_scope}</code></dd></div>
              <div><dt>Resource</dt><dd><code>{result.delegation_resource}</code></dd></div>
              <div><dt>Category</dt><dd>{result.delegation_category}</dd></div>
              <div><dt>Delegation expires</dt><dd>{new Date(result.delegation_expires_at * 1000).toLocaleString()}</dd></div>
            </dl>
            <p className="counter-note">This is a receiver-wide counter shared by every demo visitor, not your session count.</p>
            <details><summary>Technical evidence</summary><p>Audience <code>{result.delegation_audience}</code> · Delegation issued {new Date(result.delegation_issued_at * 1000).toLocaleString()} · Correlation {result.correlation_id} · Executed {new Date(result.timestamp).toLocaleString()} · No keys, proof material, or private identifiers are displayed.</p></details>
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

      <footer><span>Open-source reference · Not a Maritime endorsement</span><a href="https://github.com/identities-ai/ratify-maritime-reference">View source ↗</a></footer>
    </main>
  );
}
