# Demo walkthrough (about three minutes)

Everything runs offline. No API key, no Microsoft 365 tenant, no network. `DEMO_MODE=true` swaps the mailbox reader, the receipt reader and the draft extractor for fixture adapters, and the sender for one that writes every e-mail to `.demo/outbox/`. "Today" is pinned to **2026-09-17** (a Thursday), the day the fixtures are built around.

```bash
uv sync
cp .env.example .env        # DEMO_MODE=true already
```

## 1. The first run

```bash
DRY_RUN=false uv run settlement-reminders
```

The fixture mailbox holds eight messages. What happens to each:

| Message | Route | Outcome |
|---|---|---|
| A lawyer's notice for case `…0001` (three installments of R$ 3,000 from 22/09) | notice: parsed without a model | registered **active**; forwarded to the client without the machine block; confirmation to the lawyer + controllership |
| The client's reply to that notice with `receipt-3000.pdf` (and a `.vcf` signature) | reply about a known case: the receipt is read | installment 1 **settled** by amount; the `.vcf` is ignored, not held |
| A second notice for case `…0003` (two installments from 22/09, with `CC_EXTRA`) | notice | registered active; forwarded; confirmed |
| A client reply with no attachment ("we will pay on the date") | reply | **check** item in the digest; nothing settled |
| A `[TEST]` rehearsal of the first notice | test mode | ignored, recorded |
| A forwarded PDF draft without the skill (`draft-agreement.pdf`) | legacy: recorded extraction + directory match | registered active for the client found in `fixtures/clients.xlsx` |
| A notice whose block lacks required keys | invalid notice | **check** item; the sender gets an explanation e-mail |
| Group traffic that only copies the mailbox, with a PDF | not addressed to the bot | refused, never downloaded |

Then the cycle runs over five agreements (three from the store, two active rows of `fixtures/agreements.xlsx`):

- case `…0003`, installment 1 due 22/09: the **reminder** goes out (three business days before) as a reply in the notice's thread, with the controllership, the labour team and the notice's extra copy in Cc;
- case `…0001`, installment 1: also due 22/09, but already settled by the receipt: **skipped**;
- case `…0002` (spreadsheet), installment 1 due 16/09: overdue, first **collection**.

The digest to the operator lists the settlement, the two check items and nothing else. Open `.demo/outbox/` to read every e-mail as the recipient would.

## 2. Run it again

```bash
DRY_RUN=false uv run settlement-reminders
```

Every message is already processed, the reminder is already sent, the collection was already sent today: nothing goes out. Idempotency is the whole point.

## 3. Advance the clock

```bash
for day in 2026-09-21 2026-09-23 2026-09-25 2026-09-29 2026-09-30 2026-10-01; do
  DRY_RUN=false uv run settlement-reminders --date $day
done
```

- 21/09: collection 2 of case `…0002` (the 3rd business day after the due date).
- 23/09: collection 3 of `…0002` and collection 1 of `…0003` (its installment was due 22/09 without a receipt). At most one collection per agreement per day.
- 25/09 and 29/09: collections 4 and 5 of `…0002`; 2 and 3 of `…0003`.
- 30/09: the reminder of the spreadsheet's case `…0004` (due 05/10); `…0002` has exhausted its five attempts.
- 01/10: the 11th business day after the due date: the **escalation** of `…0002` goes to the lawyer with the controllership in Cc, and the automation stops collecting that installment.

## 4. Operate

```bash
uv run settlement-agreements list --demo                # every agreement with its status and issues
uv run settlement-agreements installments 0001234-74.2099.5.99.0001 --demo
uv run settlement-agreements holds --demo               # collections on hold (none in the demo)
uv run settlement-agreements settle 0001234-74.2099.5.99.0001 O1P2 "paid by bank slip" --demo
uv run settlement-audit --demo                          # the real sends against the store
uv run preview-email collection                         # renders a template to .demo/preview-collection.html
```

Delete `.demo/` to start over.
