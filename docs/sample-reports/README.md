# Sample report cards

Real-feeling examples of what Dendra emits during the lifecycle of a
wrapped switch. These are reference artifacts — the data is
hand-curated to look like a typical mid-market deployment so reviewers
can see the full evidence shape before installing locally.

| File | Command that produces it | When |
|---|---|---|
| [`_initial-analysis.md`](_initial-analysis.md) | `dendra analyze --report` | First scan of the codebase. Lists candidate sites with priority + projected $ savings + recommended graduation order. |
| [`triage_rule.md`](triage_rule.md) | `dendra report triage_rule` | Per-switch graduation card. Phase, gate's p-value at fire, transition curve, cost trajectory, drift checks. |
| [`_summary.md`](_summary.md) | `dendra report --summary` | Project rollup. Cockpit view across all wrapped switches: phase distribution, sparklines, aggregate cost reduction, action items. |

Run any of these against your own codebase to produce the real version
— same shape, your data. The cards are markdown so they live in your
repo, diff in PRs, and ship in releases as audit-grade evidence that
the gate fired correctly.
