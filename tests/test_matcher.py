"""The recipient matcher: the model's API is stubbed, no network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from settlement_reminders.config import normalise
from settlement_reminders.ingest import ClientContact, RecipientMatcher
from settlement_reminders.ingest.matcher import _ModelChoice, prefilter


def make_contacts() -> list[ClientContact]:
    return [
        ClientContact(
            name="GRUPO MODELO CONSTRUTORA E INCORPORADORA LTDA",
            group="Grupo Modelo",
            tax_id="",
            emails=["finance@example.com"],
        ),
        ClientContact(
            name="FUNDACAO EXEMPLO DE ASSISTENCIA", group="Exemplo", emails=["contact@example.org"]
        ),
        ClientContact(name="CONSTRUTORA SEM EMAIL LTDA", emails=[]),
    ]


class _StubMessages:
    def __init__(self, choice: _ModelChoice | None) -> None:
        self._choice = choice
        self.kwargs: dict | None = None
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return SimpleNamespace(parsed_output=self._choice, stop_reason="end_turn")


class _StubClient:
    def __init__(self, choice: _ModelChoice | None) -> None:
        self.messages = _StubMessages(choice)


class _ClientThatMustNotBeCalled:
    @property
    def messages(self):  # pragma: no cover - only fires on a regression
        raise AssertionError("the API should not be called in this scenario")


def test_exact_match_does_not_call_the_api():
    matcher = RecipientMatcher(make_contacts(), client=_ClientThatMustNotBeCalled())

    result = matcher.match("Grupo Modelo Construtora e Incorporadora Ltda")

    assert result.status == "ok"
    assert result.confidence == "high"
    assert result.emails == ["finance@example.com"]


def test_exact_match_without_email_becomes_no_email():
    matcher = RecipientMatcher(make_contacts(), client=_ClientThatMustNotBeCalled())

    result = matcher.match("CONSTRUTORA SEM EMAIL LTDA")

    assert result.status == "no_email"
    assert result.emails == []


def test_empty_directory_does_not_call_the_api():
    matcher = RecipientMatcher([], client=_ClientThatMustNotBeCalled())
    assert matcher.match("ANYONE").status == "not_found"


def test_model_high_confidence_returns_ok():
    client = _StubClient(_ModelChoice(chosen_index=0, confidence="high", rationale="same entity"))
    matcher = RecipientMatcher(make_contacts(), client=client)

    # a name with the "E OUTROS" suffix: not exact, goes to the model; the pre-filter puts
    # the most similar (Grupo Modelo) at position 0
    result = matcher.match("GRUPO MODELO CONSTRUTORA E OUTROS")

    assert client.messages.calls == 1
    assert result.status == "ok"
    assert result.contact.group == "Grupo Modelo"
    assert result.emails == ["finance@example.com"]
    assert client.messages.kwargs["output_format"] is _ModelChoice


def test_model_medium_confidence_becomes_an_exception():
    client = _StubClient(
        _ModelChoice(chosen_index=0, confidence="medium", rationale="two plausible")
    )
    matcher = RecipientMatcher(make_contacts(), client=client)

    result = matcher.match("GRUPO MODELO E OUTROS")

    assert result.status == "low_confidence"
    assert result.contact is not None  # an informative exception: shows the near match
    assert result.emails == []


def test_model_without_a_choice_becomes_not_found():
    client = _StubClient(
        _ModelChoice(chosen_index=None, confidence="low", rationale="no candidate")
    )
    matcher = RecipientMatcher(make_contacts(), client=client)

    result = matcher.match("A TOTALLY UNKNOWN COMPANY SA")

    assert result.status == "not_found"
    assert result.rationale == "no candidate"


def test_model_invalid_index_becomes_not_found():
    client = _StubClient(_ModelChoice(chosen_index=99, confidence="high", rationale="?"))
    matcher = RecipientMatcher(make_contacts(), client=client)

    assert matcher.match("ANY COMPANY LTDA").status == "not_found"


def test_without_a_key_only_fails_when_the_api_is_needed():
    matcher = RecipientMatcher(make_contacts(), api_key="")

    # exact: works without a key
    assert matcher.match("FUNDACAO EXEMPLO DE ASSISTENCIA").status == "ok"

    # fuzzy: needs the API -> a clear error
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        matcher.match("FUNDACAO EXEMPLO E OUTROS")


def test_prefilter_puts_the_most_similar_first():
    contacts = make_contacts()
    target = normalise("GRUPO MODELO CONSTRUTORA E OUTROS")

    ranking = prefilter(target, contacts, limit=2)

    assert ranking[0].group == "Grupo Modelo"
    assert len(ranking) == 2
