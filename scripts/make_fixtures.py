"""Generates the binary fixtures of the demo: two spreadsheets and two placeholder PDFs.

    uv run python scripts/make_fixtures.py

Every value is fictional. The case numbers are dated 2099 with invalid check digits; the
e-mail domains are reserved (`.example`).
"""

from __future__ import annotations

import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
# fixed document and zip timestamps: the files are byte-reproducible, so CI can check
# they match the script
STAMP = datetime(2026, 1, 1)

AGREEMENT_COLUMNS = [
    "Case number",
    "Claimant",
    "Payer",
    "Beneficiary",
    "Honorific",
    "Client emails",
    "Lawyer email",
    "Installment amount",
    "Installment count",
    "First due",
    "Payment method",
    "Payment details",
    "Late clause",
    "Active",
]

CLIENT_COLUMNS = ["Name", "Group", "Tax ID", "Entity type", "E-mails"]

LATE_CLAUSE = (
    "em caso de atraso no pagamento da parcela, haverá incidência de multa de 50% (cinquenta por cento) "
    "sobre o valor inadimplido, aplicável a partir do 5º (quinto) dia de mora"
)


def _pin_modified(core_xml: bytes) -> bytes:
    """openpyxl overwrites `modified` at save time; pin it like `created`."""
    stamp = STAMP.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    return re.sub(
        rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
        rb"\g<1>" + stamp + rb"\g<2>",
        core_xml,
    )


def save_reproducible(wb: Workbook, path: Path) -> None:
    """Saves the workbook byte-reproducibly on every platform.

    openpyxl stamps the current time in the core properties, writes cell newlines as CRLF
    on Windows, the zip entries carry the current time and the platform, and deflate
    streams differ between zlib builds; so the file is rewritten with pinned timestamps,
    LF newlines, stored (uncompressed) entries and a fixed central directory.
    """
    wb.properties.created = STAMP
    wb.save(path)
    with zipfile.ZipFile(path) as zf:
        entries = [(name, zf.read(name)) for name in zf.namelist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for name, content in entries:
            if name == "docProps/core.xml":
                content = _pin_modified(content)
            content = content.replace(b"\r\n", b"\n")  # cell newlines: CRLF on Windows
            info = zipfile.ZipInfo(name, date_time=STAMP.timetuple()[:6])
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, content)


def make_agreements() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Agreements"
    ws.append(AGREEMENT_COLUMNS)
    # B: one installment already overdue on the demo's reference date (2026-09-17)
    ws.append(
        [
            "0001979-51.2099.5.99.0002",
            "Sicrana de Tal",
            "Loja Exemplo Comércio LTDA",
            "Sicrana de Tal",
            "Sra.",
            "finance@loja-exemplo.example",
            "lawyer@lawfirm.example",
            "2166,66",
            1,
            date(2026, 9, 16),
            "depósito ou transferência",
            "Banco Exemplo (000)\nCorrentista: Sicrana de Tal;\nAgência: 0000-0;\nConta: 00000-0;\nChave PIX: 000.000.000-00.",
            LATE_CLAUSE,
            "Yes",
        ]
    )
    # C: three installments starting in October (the first reminder falls on 2026-09-30)
    ws.append(
        [
            "0000034-23.2099.5.99.0004",
            "Beltrano de Tal",
            "Oficina Modelo ME",
            "Beltrano de Tal",
            "Sr.",
            "owner@oficina-modelo.example",
            "lawyer@lawfirm.example",
            "1500,00",
            3,
            date(2026, 10, 5),
            "transferência bancária",
            "Titular: Beltrano de Tal;\nCPF: 000.000.000-00;\nBanco: 000 - Banco Exemplo;\n"
            "Agência: 0000;\nConta Corrente: 00000-0.",
            LATE_CLAUSE,
            "Yes",
        ]
    )
    # an inactive row, to show the filter
    ws.append(
        [
            "0009999-19.2099.5.99.0006",
            "Outra Parte",
            "Cliente Inativo LTDA",
            "Outra Parte",
            "Sr.",
            "x@inativo.example",
            "",
            "100,00",
            1,
            date(2026, 9, 30),
            "PIX",
            "PIX: 000",
            LATE_CLAUSE,
            "No",
        ]
    )
    save_reproducible(wb, FIXTURES / "agreements.xlsx")


def make_clients() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Clients"
    ws.append(CLIENT_COLUMNS)
    ws.append(
        [
            "CONSTRUTORA EXEMPLO LTDA",
            "Grupo Exemplo",
            "",
            "Legal entity",
            "finance@construtora-exemplo.example; legal@construtora-exemplo.example",
        ]
    )
    ws.append(
        ["LOJA EXEMPLO COMÉRCIO LTDA", "", "", "Legal entity", "finance@loja-exemplo.example"]
    )
    ws.append(["CLIENTE SEM E-MAIL LTDA", "", "", "Legal entity", None])
    save_reproducible(wb, FIXTURES / "clients.xlsx")


def make_pdfs() -> None:
    """Placeholder PDFs: the demo readers answer from the recorded readings, by file name."""
    body = b"%PDF-1.4\n% fictional placeholder for the demo: the content is not read\n%%EOF\n"
    (FIXTURES / "mailbox" / "attachments" / "receipt-3000.pdf").write_bytes(body)
    (FIXTURES / "mailbox" / "attachments" / "draft-agreement.pdf").write_bytes(body)


if __name__ == "__main__":
    make_agreements()
    make_clients()
    make_pdfs()
    print(f"Fixtures written to {FIXTURES}")
