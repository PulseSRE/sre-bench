# Documentation review — 2026-10-11

Reviewed all three Markdown files tracked at main `d0868298a0efa4998b9c968d63188a758e1aa92b`.

| File | Review and disposition |
| --- | --- |
| README.md | Checked task/scoring descriptions against scoring.py/types.py and CLI flags. Corrected diagnosis score wording, removed an unsupported historical deployment claim and machine-specific Python assumption, and labeled pinned comparisons as historical and preceding the corrected fixture. |
| docs/RUNNING.md | Corrected trajectory-default claims and verification semantics; added durable probe setup, mutation scope, partial coverage and current caller-identity limitation from durable.py. |
| CHANGELOG.md | Preserved release history; added the merged fixture correction and documentation changes under Unreleased. |

Local Markdown file links and `git diff --check` pass. Installed CLI help confirms documented score/durable flags. Commands involving an agent, provider or cluster were inspected, not executed. Historical trajectory results are retained, not relabeled as fresh model evaluations.
