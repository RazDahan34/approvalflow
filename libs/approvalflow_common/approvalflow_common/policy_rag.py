"""RAG over the policy document (N5) — retrieve only the relevant clause(s) per item.

The policy is a small, *structured* markdown document (~20 rules with stable ids), so we
use a hybrid retriever instead of a vector store:

- **structural filter** — the invoice's own attributes select rules deterministically
  (category section; receipt/vendor/currency flags select the matching global rules);
- **lexical scoring** — token overlap pulls in cross-category rules the structure would
  miss (e.g. an "alcohol-only bar tab" note on an `other` item retrieves MEAL-03).

Embeddings were deliberately rejected for this corpus: 20 enumerable clauses don't need
approximate semantic search, and a GB-sized model dependency would violate the
free-and-local constraint for zero retrieval gain (see ADR-0007).
"""

import re
from pathlib import Path

from pydantic import BaseModel

from .schemas import Category, InvoiceSubmission

_RULE_ID = re.compile(r"`([A-Z][A-Z0-9-]+)`")
_MARKUP = re.compile(r"[*`_]")
_TOKEN = re.compile(r"[a-z0-9]+")

_STOP_WORDS = frozenset(
    "the a an and or is are was to for of in on by must any with not it its this that".split()
)

# Which policy section serves which invoice category (§5 globals are handled separately).
_CATEGORY_SECTION = {
    Category.meals: "1.",
    Category.travel: "2.",
    Category.saas: "3.",
    Category.hardware: "4.",
}

# Rules the router owns deterministically end-to-end; the model never needs them.
_NEVER_RETRIEVE = {"GLOBAL-DUP"}


class RuleChunk(BaseModel):
    rule_id: str
    section: str
    text: str


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 3 and t not in _STOP_WORDS}


class PolicyIndex:
    """Parses policy.md into rule chunks and retrieves the relevant ones per invoice."""

    def __init__(self, rules: list[RuleChunk]) -> None:
        self.rules = rules
        self._token_cache = {r.rule_id: _tokens(r.text) for r in rules}

    @classmethod
    def from_markdown(cls, markdown: str) -> "PolicyIndex":
        rules: list[RuleChunk] = []
        section = ""
        for line in markdown.splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            # Rule rows look like: | `MEAL-01` | rule text ... |
            if not line.lstrip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) < 3:
                continue
            match = _RULE_ID.search(cells[1])
            # Sections 6-7 (autonomy thresholds, budgets) belong to the router/config,
            # not to the agent's reading of the rules.
            if match and section[:1] in "12345":
                rules.append(
                    RuleChunk(
                        rule_id=match.group(1),
                        section=section,
                        text=_MARKUP.sub("", cells[2]).strip(),
                    )
                )
        if not rules:
            raise ValueError("no policy rules found — is this the right policy.md?")
        return cls(rules)

    @classmethod
    def from_file(cls, path: str | Path) -> "PolicyIndex":
        return cls.from_markdown(Path(path).read_text(encoding="utf-8"))

    def retrieve(self, invoice: InvoiceSubmission, k: int = 8) -> list[RuleChunk]:
        """Structural picks first, then the best lexical extras, capped at k."""
        picked: dict[str, RuleChunk] = {}

        def pick(rule: RuleChunk) -> None:
            picked.setdefault(rule.rule_id, rule)

        category_prefix = _CATEGORY_SECTION.get(invoice.category)
        for rule in self.rules:
            if rule.rule_id in _NEVER_RETRIEVE:
                continue
            # The invoice's own category section, in full.
            if category_prefix and rule.section.startswith(category_prefix):
                pick(rule)
            # Globals selected by the invoice's concrete attributes.
            elif rule.rule_id == "GLOBAL-MATH" or rule.rule_id == "GLOBAL-FRAUD":
                pick(rule)  # always relevant: reconciliation + fraud-signal instructions
            elif rule.rule_id == "GLOBAL-RECEIPT" and (not invoice.receipt_present or invoice.total > 25):
                pick(rule)
            elif rule.rule_id == "GLOBAL-VENDOR" and not invoice.vendor_known:
                pick(rule)
            elif rule.rule_id == "GLOBAL-FX" and invoice.currency.upper() != "USD":
                pick(rule)

        # Lexical extras: free text in the invoice can implicate rules outside its
        # category (the "alcohol-only bar tab under `other`" case).
        invoice_text = " ".join(
            [invoice.notes or "", invoice.vendor] + [li.description for li in invoice.line_items]
        )
        invoice_tokens = _tokens(invoice_text)
        scored = sorted(
            (
                (len(self._token_cache[r.rule_id] & invoice_tokens), r)
                for r in self.rules
                if r.rule_id not in picked and r.rule_id not in _NEVER_RETRIEVE
            ),
            key=lambda pair: -pair[0],
        )
        for score, rule in scored[:2]:
            if score >= 1:
                pick(rule)

        # Stable, document-order output => reproducible prompts.
        ordered = [r for r in self.rules if r.rule_id in picked]
        return ordered[:k]
