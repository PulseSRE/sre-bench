"""Reference adapters that run against the simulated backend.

- ``scripted``: a deterministic rule-based agent. Not a leaderboard entry —
  it exists to prove the sim plumbing end to end and to serve as the adapter
  example. Any real agent should beat it.
- ``claude_agent``: a plain Claude model given the sim tool registry, with no
  SRE scaffolding. The published floor that purpose-built agents are compared
  against (requires ``pip install sre-bench[judge]`` and ``ANTHROPIC_API_KEY``).
"""
