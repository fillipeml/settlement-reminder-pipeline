# The notice and its AUTOMATION-DATA block

The entry point of the pipeline is an ordinary e-mail: the settlement notice a lawyer sends to the client, written by the `settlement-notice` skill (see `skills/settlement-notice/`). The human part of the notice is the firm's standard Portuguese text; the machine part is a block at the end, inside `<pre>`, that the automation parses **deterministically** (no model call, zero cost). The notice is forwarded to the client without the block.

## Why a block in the body

The connector that sends e-mail from the assistant does not attach files, so the notice carries every datum in the body. That turned out to be the better design anyway: the registration became deterministic, the client's e-mails come from the lawyer who read the settlement (instead of an incomplete directory), and the text the client receives is written by someone who read the document.

## Format (v1)

```
=== AUTOMATION-DATA v1 ===
OPERATION: REGISTER              (or CORRECTION)
CASE: 0001234-74.2099.5.99.0001
CLIENT: FULL NAME OF THE CLIENT
CLIENT_SIDE: DEFENDANT           (or PLAINTIFF)
COUNTERPARTY: FULL NAME
CLIENT_EMAILS: contact@client.example; finance@client.example
LAWYER: lawyer@lawfirm.example
CC_EXTRA: coordinator@lawfirm.example      (optional)
COURT: 1ª Vara do Trabalho | Cidade - UF   (optional)
TOTAL_AMOUNT: 6500.00                       (omit when the document declares none)
LATE_CLAUSE: em caso de atraso no pagamento das parcelas, multa de 50% ...
OBLIGATION: 1 | crédito do reclamante | beneficiary=Name of the Holder | honorific=Sr.
PAYMENT: 1 | depósito bancário ou PIX | Banco 000; Ag 0001; CC 12345-6; CPF 000.000.000-00
INSTALLMENT: 1 | 1 | 2026-06-15 | 2166.66
INSTALLMENT: 1 | 2 | 2026-07-15 | 2166.66
INSTALLMENT: 1 | 3 | 2026-08-17 | 2166.68
NO_COLLECTION: court fees of R$ 1.500,00 deposited in court   (optional, repeatable)
DUTY: CTPS update by 2026-06-25                                (optional, repeatable)
=== END AUTOMATION-DATA ===
```

| Key | Rule |
|---|---|
| `OPERATION` | `REGISTER` creates or updates; `CORRECTION` updates and forwards the notice to the client again. A closed agreement is never reopened by either. |
| `CASE` | The CNJ case number, `NNNNNNN-DD.YYYY.J.TR.OOOO`. Also what matches the client's replies (it must stay in the subject). |
| `CLIENT_EMAILS` | Separated by `;`. Internal addresses are removed with a doubt; none left = the agreement is born pending. |
| `LAWYER` | Gets the registration confirmation and the escalation; outside the firm's domain = a doubt. |
| `CC_EXTRA` | Extra copies of the reminders and collections, besides the controllership. Internal addresses allowed. |
| `OBLIGATION` | One per payment block: number, description, `beneficiary=`, `honorific=` (`Sr.`/`Sra.` for a party, `Dr.`/`Dra.` for a lawyer). |
| `PAYMENT` | Number, method (as the e-mail will say it), bank details. Extra `\|` segments are joined. |
| `INSTALLMENT` | Obligation number, installment number, due date, amount. The date is the document's, **never** moved to a business day by the skill: the engine does that. |
| `NO_COLLECTION` | Components paid outside the installments (court order, deposit withdrawal): they let the installment sum stay below `TOTAL_AMOUNT`. |
| `DUTY` | Non-monetary obligations (labour card updates, filings); documented, not collected. |

## Tolerances and errors

- Brazilian slips are tolerated: `2.119,17` and `23/04/2026` are read as `2119.17` and `2026-04-23`.
- A line that does not start with `KEY:` continues the previous key (mail clients rewrap long lines).
- HTML entities and tags are neutralised; the block survives the connector's sanitisation.
- **Structural** problems (block missing, version unsupported, a required key missing, a malformed line, an installment of an unknown obligation) raise an error: the message is recorded, the sender gets an explanation and nothing is registered.
- **Soft** problems (the sum differs from the total, dates out of order, an internal e-mail as a client, no lawyer) become doubts: the agreement is registered **pending** and shows up in the digest until a `CORRECTION` arrives.

## Test mode

A subject starting with `[TEST]` (or `[TESTE]`) is ignored by the automation even when it carries a valid block, so a lawyer can rehearse the skill by sending the notice to themselves.
