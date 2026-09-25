from datetime import date
from decimal import Decimal

from settlement_reminders.sources.sharepoint import SharePointSource, decode_internal_name
from tests.factories import CASE_B


class FakeGraph:
    def __init__(self, items):
        self._items = items

    def get(self, path, params=None):
        if path.startswith("/sites/") and "/lists" not in path:
            return {"id": "site-1"}
        raise AssertionError(path)

    def get_all(self, path, params=None):
        if path.endswith("/lists"):
            return [{"id": "list-1", "displayName": "Agreements"}]
        if "/items" in path:
            return self._items
        raise AssertionError(path)


def test_decode_internal_name():
    assert decode_internal_name("Installment_x0020_count") == "Installment count"


def test_reads_an_agreement_from_sharepoint():
    items = [
        {
            "id": "1",
            "fields": {
                "Case_x0020_number": CASE_B,
                "Claimant": "Sicrana",
                "Payer": "Empresa",
                "Beneficiary": "Sicrana",
                "Honorific": "Sra.",
                "Client_x0020_emails": "a@x.com, b@y.com",
                "Installment_x0020_amount": "2166,66",
                "Installment_x0020_count": 3,
                "First_x0020_due": "2026-06-15T00:00:00Z",
                "Payment_x0020_details": "Banco Exemplo\nPIX: 000",
                "Late_x0020_clause": "em caso de atraso, multa",
                "Active": "Yes",
            },
        }
    ]
    source = SharePointSource(FakeGraph(items), "host:/sites/Agreements", "Agreements")
    agreements = list(source.fetch_agreements())
    assert len(agreements) == 1
    a = agreements[0]
    assert a.installment_amount == Decimal("2166.66")
    assert a.first_due == date(2026, 6, 15)
    assert len(a.installments()) == 3
