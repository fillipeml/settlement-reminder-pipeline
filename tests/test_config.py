"""Settings: CSV lists, the payer map, the internal domain and the demo defaults."""

from __future__ import annotations

from datetime import date

from settlement_reminders.config import DEMO_REFERENCE_DATE, Settings, normalise


def test_csv_lists_and_payer_map():
    s = Settings(
        _env_file=None,
        sources="notices, excel",
        controllership_emails="a@lawfirm.example,b@lawfirm.example",
        cc_by_payer="Grupo Modelo: x@g.example, y@g.example; outra: z@o.example",
    )
    assert s.sources == ["notices", "excel"]
    assert s.controllership_emails == ["a@lawfirm.example", "b@lawfirm.example"]
    assert s.cc_by_payer == {
        "grupo modelo": ["x@g.example", "y@g.example"],
        "outra": ["z@o.example"],
    }
    assert s.cc_for_payer("GRUPO MODELO CONSTRUTORA LTDA") == ["x@g.example", "y@g.example"]
    assert s.cc_for_payer("Cliente sem regra") == []


def test_internal_domain_is_normalised():
    s = Settings(_env_file=None, internal_domain="@LawFirm.Example ")
    assert s.internal_domain == "lawfirm.example"
    assert s.is_internal("Someone@lawfirm.example")
    assert not s.is_internal("client@other.example")


def test_empty_values_fall_back_to_defaults():
    s = Settings(_env_file=None, reference_date="", demo_mode="", dry_run="")
    assert s.reference_date is None
    assert s.demo_mode is False
    assert s.dry_run is False


def test_demo_defaults_fill_only_the_empty_fields():
    s = Settings(
        _env_file=None, demo_mode=True, controllership_emails="mine@lawfirm.example"
    ).for_demo()
    assert s.demo_mode is True
    assert s.history_db == ".demo/state.sqlite"
    assert s.sources == ["notices", "excel"]
    assert s.ingest_mailbox == "automation@lawfirm.example"
    assert s.reference_date == DEMO_REFERENCE_DATE == date(2026, 9, 17)
    assert s.controllership_emails == ["mine@lawfirm.example"]  # kept


def test_normalise():
    assert normalise("  Grupo  MODELO Ltda ") == "grupo modelo ltda"
    assert normalise("Incorporação") == "incorporacao"
