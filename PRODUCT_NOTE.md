# ParcelPilot AI Support & Operations — Product Note

## 1. Chosen Additional Problem: Trust & Reliability (Problem 2)

### Why Trust & Reliability Was Prioritized:
In B2B logistics, automated support cannot afford hallucinated policies, miscalculated SLA penalties, or unauthorized cross-tenant disclosures. A single incorrect cancellation fee or invalid service credit promise erodes customer trust and incurs financial liability. 

Furthermore, logistics documentation is inherently messy: enterprise contracts override standard policies, deprecated policies linger in document repositories, and past support tickets often contain obsolete or mistaken advice. We prioritized **Trust & Reliability** because solving source conflict resolution and access control at the architectural level is essential for a mission-critical support system.

### How Trust & Reliability Was Addressed:
1. **5-Tier Source Authority Engine**:
   - Structured hierarchy strictly evaluated on every user query:
     - **Tier 1 (Customer Agreement)** overrides general policy for that specific account.
     - **Tier 2 (Current SOP & Policy)** provides standard operating rules.
     - **Tier 3 (Product Operations Guide)** provides operational facts and workarounds.
     - **Tier 4 (Deprecated Policy)** is explicitly prevented from serving as current authority.
     - **Tier 5 (Historical Ticket Resolutions)** is flagged as unverified context only.
2. **Transparent Decision Provenance in the UI**:
   - Every answer displays clickable source citation badges indicating the document version, authority tier, and whether a customer-specific term superseded a standard SOP.
   - Operators can expand the **Tool Execution Trace** to inspect the exact database queries, calculations, and retrieval scores behind each answer.
3. **Confidence Gating & Escalation Recommendations**:
   - When sources conflict without a clear governing tier, or when critical P1 outage/security events occur, the agent avoids guessing, states the uncertainty, and stages an escalation via `propose_action`.
4. **Structural Data & Action Safety**:
   - Strict tenant isolation enforced at the SQLite query layer (`WHERE account_id = :ctx.account_id`).
   - Two-phase action execution (`propose_action` -> explicit user confirmation -> `execute_action`) prevents unauthorized or accidental state modifications.

---

## 2. What Else We Would Build Next (Product Roadmap)

If continuing development on ParcelPilot AI, we would prioritize the following enhancements:

1. **Real-Time Carrier Webhook Integration & Event Streaming**:
   - Connect live carrier webhooks (e.g. SwiftShip, BlueDart, RoadRunner) directly into the agent event bus. Automatically reconcile known webhook delays (e.g. `KI-211`) against driver GPS telemetry.
2. **Proactive SLA Predictive Alerting Engine**:
   - Transition from reactive SLA auditing to predictive alerting: calculate ticket aging trends and carrier transit times to alert CSMs 30 minutes *before* an SLA breach occurs.
3. **Automated Service Credit Billing Reconciliation**:
   - Integrate with billing gateways (Stripe / Razorpay / ERP) so that approved service credits under `execute_action` automatically generate invoice credit notes with audit trails.
4. **Multi-Channel Omnichannel Deployment**:
   - Package the agent core into Slack/Teams bots for internal operations and embedded web chat / Zendesk / Freshdesk integrations for customers.

---

## 3. What Was Intentionally Left Out of This Submission

To maintain focus on rock-solid core agent reliability and source conflict resolution within the assessment scope:

1. **Full-Blown Time-Series Anomaly Streaming Architecture**:
   - *Reason*: While we included an operations intelligence panel (`src/agent/analytics.py`), we did not build a complex real-time streaming pipeline (e.g. Kafka/Flink), choosing instead to perform on-demand audits against the SQLite snapshot.
2. **Live Third-Party Billing Gateway Execution**:
   - *Reason*: Financial ledger mutations are mocked via SQLite database records rather than live bank/payment APIs.
3. **Multi-Tenant User Management & Auth0 Integration**:
   - *Reason*: Authentication is simulated cleanly via session parameters and a dynamic `UserContext` selector, avoiding unnecessary external identity provider setup while thoroughly testing access control logic.

---

## 4. Primary Success Metric

### **Metric**: *First-Contact Policy Resolution Accuracy Rate (FCPR-A)*

$$\text{FCPR-A} = \frac{\text{Queries resolved by the agent adhering to the governing contract/policy without post-resolution dispute or re-escalation}}{\text{Total Policy \& Entitlement Queries Handled}} \times 100\%$$

### Why This Metric Matters:
- **Measures Ground Truth Correctness**: Tracks whether the agent correctly identified contract overrides vs standard SOPs.
- **Prevents False Positives**: Differentiates between an agent that quickly guesses wrong vs one that correctly resolves or accurately escalates ambiguous cases.
- **Direct Business Impact**: High FCPR-A directly reduces support backlog, eliminates disputed cancellation fees, and protects customer trust.
