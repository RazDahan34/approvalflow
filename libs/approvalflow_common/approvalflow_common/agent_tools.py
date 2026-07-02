"""The agent's tools — pure, testable implementations (B2).

The MCP server (`services/mcp-server`) is a thin protocol wrapper around this toolbox;
keeping the logic here means unit tests exercise exactly what the server serves, and
the ai-decision service could fall back to in-process wiring if it ever had to.
"""

import json

from .decision import PolicyConfig
from .policy_rag import PolicyIndex, _tokens
from .schemas import Category

_SECTION_BY_CATEGORY = {
    Category.meals: "1.",
    Category.travel: "2.",
    Category.saas: "3.",
    Category.hardware: "4.",
}


class AgentToolbox:
    """fetch_policy / lookup_vendor / get_autonomy_thresholds over shared data."""

    def __init__(self, index: PolicyIndex, known_vendors: set[str], config: PolicyConfig) -> None:
        self.index = index
        self.known_vendors = {v.casefold() for v in known_vendors}
        self.config = config

    def fetch_policy(self, category: str | None = None, query: str | None = None) -> str:
        """Return the policy clauses for a category and/or a free-text query."""
        chunks = []
        if category:
            try:
                prefix = _SECTION_BY_CATEGORY.get(Category(category.lower()))
            except ValueError:
                return json.dumps({"error": f"unknown category '{category}'"})
            if prefix:
                chunks += [r for r in self.index.rules if r.section.startswith(prefix)]
        if query:
            query_tokens = _tokens(query)
            scored = sorted(
                ((len(_tokens(r.text) & query_tokens), r) for r in self.index.rules),
                key=lambda pair: -pair[0],
            )
            chunks += [r for score, r in scored[:3] if score >= 1 and r not in chunks]
        if not category and not query:
            chunks = [r for r in self.index.rules if r.section.startswith("5.")]
        return json.dumps(
            {"clauses": [{"rule_id": r.rule_id, "section": r.section, "text": r.text} for r in chunks]}
        )

    def lookup_vendor(self, name: str) -> str:
        """Check the vendor master list. Advisory for the agent's reasoning only —
        the router always trusts the deterministic `vendorKnown` field on the invoice."""
        known = name.casefold().strip() in self.known_vendors
        return json.dumps({"name": name, "known": known})

    def get_autonomy_thresholds(self) -> str:
        """Expose the current posture so the agent can explain routing in its reason."""
        return json.dumps(
            {
                "envelope_usd": self.config.ceiling_usd,
                "confidence_threshold": self.config.confidence_threshold,
                "category_ceilings_usd": {c.value: v for c, v in self.config.category_ceilings.items()},
                "note": "The deterministic router enforces these; the agent only recommends.",
            }
        )
