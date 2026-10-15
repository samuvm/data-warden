"""Data Warden: conversational access to a lakehouse behind five control rings.

Each package has its own testing rule because it is verified differently
(see `docs/RULES.md` section 2):

  domain/                           frozen, pure types. TDD required, 90 %
  guard/ (+ rules/)                 the core. Strict TDD, 95 %, mutation 85 %
  mask/ · audit/ · principal/       deterministic. TDD required, 95 %
  cost/ · catalog/ · evalsupport/   deterministic, 90 %
  engines/ · mcp/ · http/           adapters: contract and snapshot tests, no line coverage
  nl2sql/ · agent/ · prompts/       measured by evals, not unit-tested
"""

__version__ = "0.1.0"
