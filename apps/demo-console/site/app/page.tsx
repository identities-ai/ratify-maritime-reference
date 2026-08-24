"use client";

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
  timestamp: string;
};

export default function Home() {
  const [pending, setPending] = useState<Scenario | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      if (!response.ok) throw new Error();
      setResult(await response.json() as Result);
    } catch {
      setError("The live scenario is temporarily unavailable. Please try again shortly.");
    } finally {
      setPending(null);
    }
  }

  const money = (minor: number, currency: string) => new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100) + ` ${currency}`;
  const requested = result ? money(result.requested_amount_minor, result.currency) : "";
  const allowed = result?.decision === "ALLOW";

  return (
    <main>
      <header className="nav">
        <a className="brand" href="https://labs.ratifyprotocol.com/" aria-label="Ratify Labs home">
          <img src="/maritime/ratify-logo.png" alt="" />
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
        <div className="lab-head">
          <div><p className="kicker">LIVE AUTHORIZATION LAB</p><h2 id="lab-title">Choose a work order</h2></div>
          <p>Same agent · Same site · One changed value</p>
        </div>
        <div className="scenario-grid">
          <button onClick={() => run("allow")} disabled={pending !== null}>
            <span className="scenario-tag">WITHIN AUTHORITY</span>
            <strong>Purchase safety equipment</strong>
            <span className="money">$420.00 <small>USD</small></span>
            <span className="button-action">{pending === "allow" ? "Running…" : "Run allowed scenario →"}</span>
          </button>
          <button onClick={() => run("over_limit")} disabled={pending !== null}>
            <span className="scenario-tag">ABOVE SIGNED LIMIT</span>
            <strong>Purchase safety equipment</strong>
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
          {error && <div className="result error" role="alert"><b>Unavailable</b><span>{error}</span></div>}
          {result && <div className={`result ${allowed ? "allow" : "deny"}`} role="status">
            <div className="decision-icon" aria-hidden="true">{allowed ? "✓" : "×"}</div>
            <div className="decision-copy"><p>AUTHORITY RESULT</p><h3>{result.decision}</h3><span>{allowed ? "Authorized request reached the handler." : "Signed limit enforced before handler entry."}</span></div>
            <dl>
              <div><dt>Requested</dt><dd>{requested}</dd></div>
              <div><dt>Authorized bound</dt><dd>≤ {money(result.authorized_max_amount_minor, result.currency)}</dd></div>
              <div><dt>Receiver reason</dt><dd><code>{result.reason}</code></dd></div>
              <div><dt>Shared receiver handler count</dt><dd>{result.handler_invocations}</dd></div>
            </dl>
            <p className="counter-note">This is a receiver-wide counter shared by every demo visitor, not your session count.</p>
            <details><summary>Technical evidence</summary><p>Correlation {result.correlation_id} · Executed {new Date(result.timestamp).toLocaleString()} · No keys, proof material, or private identifiers are displayed.</p></details>
          </div>}
        </div>}
      </section>

      <section className="explainer">
        <p className="kicker">WHAT THIS PROVES</p>
        <h2>The model is not the authority boundary.</h2>
        <div className="proof-grid">
          <article><span>01</span><h3>Recognition</h3><p>The agent understands the requested work. Recognition alone grants nothing.</p></article>
          <article><span>02</span><h3>Delegation</h3><p>The principal signs exact bounds: site, category, currency, amount, and audience.</p></article>
          <article><span>03</span><h3>Verification</h3><p>The separate receiver checks proof, policy, replay, and revocation before code runs.</p></article>
        </div>
      </section>

      <footer><span>Open-source reference · Not a Maritime endorsement</span><a href="https://github.com/identities-ai/ratify-maritime-reference">View source ↗</a></footer>
    </main>
  );
}
