"""Matching the paying client with the e-mail directory (legacy path).

Two stages, in cost/reliability order:

1. EXACT match of the normalised name -> resolved without calling the API (deterministic
   and free).
2. The model chooses among pre-filtered candidates (lexical similarity), with a confidence
   level and a rationale.

Conservative policy: only HIGH confidence goes to automatic sending; medium/low, a client
without e-mail or no match become an exception for a human.
"""

from __future__ import annotations

import difflib
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from settlement_reminders.config import normalise
from settlement_reminders.ingest.directory import ClientContact
from settlement_reminders.ingest.extract import DEFAULT_MODEL, usage_info

logger = logging.getLogger(__name__)

# how many pre-filtered candidates are shown to the model
_CANDIDATE_LIMIT = 40

_SYSTEM = """\
You match the name of a party in a court case (the paying client of a settlement) with the
client directory of a Brazilian law firm. Names are in Portuguese.

Rules:
1. Choose a candidate ONLY when it is the same legal entity/person. These count as the same
   entity: spelling and accent variations, corporate suffixes (LTDA, S.A., EIRELI, ME),
   abbreviations, complements such as "E OUTROS", and a legal name versus the group name.
2. Do NOT choose by mere similarity of trade or city. Different companies of the same group
   are different entities.
3. 'high' confidence only when there is no other plausible candidate. When two candidates
   compete, use 'medium' or 'low' and explain.
4. When no candidate matches, chosen_index = null.
5. The rationale must be short and objective (1-2 sentences).
"""


class _ModelChoice(BaseModel):
    """The model's structured output when choosing a candidate."""

    model_config = ConfigDict(extra="forbid")

    chosen_index: int | None = Field(
        description="Index of the candidate that matches the party, or null if none."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence that the chosen one is the same entity as the party."
    )
    rationale: str = Field(
        description="Short explanation of the choice (or of the absence of one)."
    )


class MatchResult(BaseModel):
    """The result of matching a recipient for a paying client."""

    status: Literal["ok", "no_email", "not_found", "low_confidence"]
    contact: ClientContact | None = None
    confidence: str = ""
    rationale: str = ""

    @property
    def emails(self) -> list[str]:
        """E-mails ready for sending (empty when the status is not 'ok')."""
        if self.status == "ok" and self.contact:
            return [str(e) for e in self.contact.emails]
        return []


def prefilter(
    target_norm: str, contacts: list[ClientContact], limit: int = _CANDIDATE_LIMIT
) -> list[ClientContact]:
    """Sorts by lexical similarity and returns the `limit` best."""
    target_tokens = {t for t in target_norm.split() if len(t) > 3}

    def score(contact: ClientContact) -> float:
        name_norm = normalise(contact.name)
        ratio = difflib.SequenceMatcher(None, target_norm, name_norm).ratio()
        name_tokens = {t for t in name_norm.split() if len(t) > 3}
        return ratio + 0.15 * len(target_tokens & name_tokens)

    return sorted(contacts, key=score, reverse=True)[:limit]


class RecipientMatcher:
    """Finds the paying client's e-mails in the directory."""

    def __init__(
        self,
        contacts: list[ClientContact],
        *,
        client=None,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.contacts = contacts
        self.model = model
        self._client = client
        self._api_key = api_key

    def _llm(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY missing: needed to match names without an exact match in the directory."
                )
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def match(self, target_name: str) -> MatchResult:
        """Matches `target_name` (the paying party) with a contact of the directory."""
        target_norm = normalise(target_name)
        if not target_norm:
            return MatchResult(status="not_found", rationale="empty name")
        if not self.contacts:
            return MatchResult(status="not_found", rationale="empty directory")

        exact = [c for c in self.contacts if normalise(c.name) == target_norm]
        if exact:
            contact = next((c for c in exact if c.emails), exact[0])
            return self._result(contact, "high", "exact name match")

        candidates = prefilter(target_norm, self.contacts)
        choice = self._choose_with_llm(target_name, candidates)

        if choice.chosen_index is None:
            return MatchResult(status="not_found", rationale=choice.rationale)
        if not 0 <= choice.chosen_index < len(candidates):
            logger.warning(
                "Index outside the candidate list (%s): %s", target_name, choice.chosen_index
            )
            return MatchResult(
                status="not_found", rationale=f"invalid index from the model: {choice.rationale}"
            )
        return self._result(candidates[choice.chosen_index], choice.confidence, choice.rationale)

    def _result(self, contact: ClientContact, confidence: str, rationale: str) -> MatchResult:
        """Applies the policy: only high confidence AND with an e-mail goes to sending."""
        if confidence != "high":
            status = "low_confidence"
        elif not contact.emails:
            status = "no_email"
        else:
            status = "ok"
        return MatchResult(
            status=status, contact=contact, confidence=confidence, rationale=rationale
        )

    def _choose_with_llm(self, target_name: str, candidates: list[ClientContact]) -> _ModelChoice:
        lines = []
        for i, c in enumerate(candidates):
            extras = " | ".join(
                item
                for item in (
                    f"Group: {c.group}" if c.group else "",
                    f"Tax ID: {c.tax_id}" if c.tax_id else "",
                    c.entity_type,
                )
                if item
            )
            lines.append(f"{i}. {c.name}" + (f"  ({extras})" if extras else ""))

        prompt = (
            f'Party to locate (the paying client of the settlement): "{target_name}"\n\n'
            "Directory candidates (pre-filtered by similarity):\n" + "\n".join(lines)
        )
        response = self._llm().messages.parse(
            model=self.model,
            max_tokens=1024,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=_ModelChoice,
        )
        choice = response.parsed_output
        logger.info(
            "Matcher for %r: index=%s confidence=%s%s",
            target_name,
            getattr(choice, "chosen_index", None),
            getattr(choice, "confidence", "?"),
            usage_info(response),
        )
        if choice is None:
            return _ModelChoice(
                chosen_index=None,
                confidence="low",
                rationale="the model returned no structured choice",
            )
        return choice
