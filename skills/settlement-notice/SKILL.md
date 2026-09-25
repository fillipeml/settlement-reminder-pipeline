---
name: settlement-notice
description: >-
  Writes and sends the firm's standard settlement notice ("ACORDO PACTUADO") to the
  reminder automation. Use it when a lawyer asks to communicate, register or record a
  settlement (from the settlement agreement, draft or hearing minutes) so the system
  notifies the client, reminds before each due date and collects every installment until
  the receipt arrives.
---

# Settlement notice

You write the firm's standard **"ACORDO PACTUADO"** notice (in Portuguese, for a Brazilian client) and send it, through the Microsoft 365 connector's e-mail tool, to the collection automation. What happens after the send: the automation registers the agreement, **forwards the notice to the client**, reminds the client three business days before each due date and, once an installment is overdue without a receipt, **collects every two business days** until the receipt arrives. The controllership gets a copy of everything for awareness.

## Non-negotiable rules

1. **Never invent a datum.** Every field comes from the attached documents or from the lawyer. A missing or ambiguous field: **ask**. Sending with gaps, assumptions or markers such as "[CHECK]" is forbidden.
2. **Never send without explicit approval.** Show the whole notice and only send after the lawyer approves ("send it", "approved").
3. **Fixed recipients, no exception** (except TEST MODE, below).
   - To: `automation@lawfirm.example`
   - Cc: `controllership@lawfirm.example`
   - **Never** add other recipients: not the client (the automation will notify it), not team mailboxes (they may trigger another bot).
4. **The exact subject pattern**, never starting with `RE:`, `FW:`, `FWD:` or `RES:`:
   - Registration: `ACORDO PACTUADO - {CNJ case number} - {COUNTERPARTY} X {CLIENT}`
   - Correction: `ACORDO PACTUADO (CORREÇÃO) - {CNJ case number} - {COUNTERPARTY} X {CLIENT}`
5. **Client e-mails are never on the firm's domain.** If the lawyer gives an internal address as "the client's e-mail", refuse and ask for the paying client's real address.

## Step 1: collect and validate the inputs

Check the list. **Ask for what is missing before writing anything**, one item at a time, directly and cordially:

1. **The settlement document attached in the chat** (PDF): the agreement, the signed draft or the hearing minutes with the terms. Without it: *"To write the notice I need the settlement agreement, draft or hearing minutes as a PDF. Can you attach it here?"*
2. **The paying client's e-mail(s)**: REQUIRED. If not given: *"Which client e-mail(s) should the automation send the payment reminders to?"* Accept more than one.
3. **The lawyer responsible for the case**: if not given, assume it is the user and confirm in the preview.
4. **The lawyer's department** (for the signature, e.g. "Contencioso Trabalhista", "Controladoria Jurídica"). Ask with the other items if missing.
5. **Who pays**: confirm the payer is the firm's client (it may be on either side of the case). If the document leaves a doubt, ask.
6. **Extra copies (optional)**: by default only the controllership follows the reminders and collections in Cc. Ask whether anyone else should; if so, it goes in `CC_EXTRA`.

## Step 2: extract the data from the document

Read the whole document and extract:

- **The CNJ case number** as `NNNNNNN-DD.YYYY.J.TR.OOOO`: required.
- **The parties**: the client (and its side) and the counterparty (and its side).
- **The court** and **the claim value**, when present.
- **The payment obligations**: each block with its own beneficiary, method and bank details is a separate obligation (e.g. the claimant's credit + attorney fees). For each, the **complete schedule**: number, due date and amount of every installment.
- **The late-payment clause**, faithful to the document.
- **Payments that do NOT depend on the client**: a court order releasing a deposit, a withdrawal of a court deposit: they stay OUT of the installments and go in `NO_COLLECTION`.
- **Duties with a deadline** (labour card update, severance fund, unemployment forms, payroll filings): they go in `DUTY`; the automation does not collect them yet, but they are documented in the notice.

Extraction rules:

- **NEVER adjust a due date.** Transcribe **exactly** the date in the document, even on a Saturday, a Sunday or a holiday. The automation moves it to the next business day by itself with the official holiday calendar, and the e-mail to the client explains the postponement. If you "fix" the date by hand you get it wrong (state and moveable holidays are not obvious) and you erase the fact that there was a postponement. The same goes for any business-day arithmetic: **do not do it**.
- **An implied schedule** (*"1st installment on 15/06 and the others on the same day of the following months"*): expand every installment with explicit dates and amounts, keeping the same day of the month without any business-day adjustment, and **show the list to the lawyer for confirmation** before the final preview.
- **Mandatory checks**: dates in ascending order; amounts > 0; when the document declares a total, the installments must add up to it (cent differences from rounding are acceptable: point them out).
- Documents that disagree, an illegible case number, incomplete bank details: **ask**, never guess.

## Step 3: write the notice

The body in simple HTML (`<p>`, `<br>`, lists, `<pre>` only; no images, no CSS), in Portuguese, following the firm's standard (the complete example is in `references/example-notice.md`):

1. Greeting: *"Prezados, bom dia!"* / *"boa tarde!"* by the time of day.
2. Opening: *"Servimo-nos do presente para informar acerca do acordo pactuado na Ação Judicial em destaque. Abaixo, esclarecimentos e considerações do caso."*
3. Identification block: case number, client | side, counterparty | side, court, claim value.
4. **1. Termos do Acordo**: per obligation (A, B, ...), with the schedule installment by installment: *"Nª parcela, no valor de R$ X.XXX,XX (amount in words), até o dia DD/MM/AAAA;"*. **Repeat the full amount, in words, on EVERY line**; never abbreviate with "same amount": it is the firm's standard and it avoids ambiguity for whoever pays.
5. **2. Forma de Pagamento**: the beneficiary and the bank details faithful to the document (bank, branch, account, CPF/CNPJ, PIX key).
6. Duties and other considerations, when there are any.
7. The late-payment clause, faithful to the document.
8. Closing: *"Por fim, colocamo-nos à disposição para maiores esclarecimentos."* + *"Atenciosamente,"* + **the lawyer's DEPARTMENT** (e.g. "Contencioso Trabalhista | Example Law Firm"), never a person's name.
9. **The `AUTOMATION-DATA` block** (spec below) inside `<pre>`, at the end, preceded by the line *"— automation data, internal use —"*.

Amounts always with the words; dates always `DD/MM/YYYY` in the human text and `YYYY-MM-DD` in the data block; in both, the **original date of the document**, never one you adjusted.

## The AUTOMATION-DATA block (machine reading, exact format)

`KEY: value` lines, keys without accents, one datum per line. List fields use `;` as the separator:

```
=== AUTOMATION-DATA v1 ===
OPERATION: REGISTER              (or CORRECTION)
CASE: 0001979-51.2099.5.99.0002
CLIENT: FULL NAME OF THE CLIENT
CLIENT_SIDE: DEFENDANT           (or PLAINTIFF)
COUNTERPARTY: FULL NAME
CLIENT_EMAILS: contact@client.example; finance@client.example
LAWYER: lawyer@lawfirm.example
CC_EXTRA: coordinator@lawfirm.example      (optional; omit when there is none)
COURT: 8ª Vara do Trabalho | Cidade - UF
TOTAL_AMOUNT: 6500.00            (omit when the document declares none)
LATE_CLAUSE: em caso de atraso no pagamento das parcelas, multa de 50% ...
OBLIGATION: 1 | crédito do reclamante | beneficiary=Name of the Holder | honorific=Sr.
PAYMENT: 1 | depósito bancário ou PIX | Banco 000; Ag 0001; CC 12345-6; CPF 000.000.000-00; PIX 000.000.000-00
INSTALLMENT: 1 | 1 | 2026-06-15 | 2166.66
INSTALLMENT: 1 | 2 | 2026-07-15 | 2166.66
INSTALLMENT: 1 | 3 | 2026-08-17 | 2166.68
NO_COLLECTION: court fees of R$ 1.500,00 deposited in court
DUTY: CTPS update by 2026-06-25
=== END AUTOMATION-DATA ===
```

- `INSTALLMENT: {obligation number} | {installment number} | {due date YYYY-MM-DD} | {amount with a decimal point}`.
- One `OBLIGATION`/`PAYMENT` line per obligation; the `INSTALLMENT` lines reference the obligation by the first field.
- `honorific`: `Dr.`/`Dra.` when the beneficiary is a lawyer or attorney-in-fact (e.g. installments paid into the counterparty's counsel's account); `Sr.`/`Sra.` otherwise. Be consistent across obligations of the same beneficiary.
- `NO_COLLECTION` and `DUTY`: one line per item; omit when there is none.
- `CC_EXTRA`: e-mails (separated by `;`) that will get a copy of the reminders and collections BESIDES the controllership; only when the lawyer asks. Internal addresses are accepted.
- Never translate, summarise or reorder the keys; the automation depends on them.

## Step 4: preview and approval

Show the lawyer: the **subject**, the **recipients** (To/Cc) and the **complete notice** (including the data block). Then a one-line summary: *"{N} installments from {first date} to {last date}, reminders to {client e-mails}"*. Ask: **"Confirm the send?"**

- Requested changes: apply and show the preview again.
- Only proceed with explicit approval.

## Step 5: send

Send with the Outlook **e-mail send tool** (the Microsoft 365 connector), `bodyType` HTML, from the lawyer's own mailbox:

- To: `automation@lawfirm.example` · Cc: `controllership@lawfirm.example`
- The subject and the body exactly as approved.

If the send tool is unavailable, guide: *"Connect your Microsoft 365 account in Settings > Connectors > Microsoft 365 > Connect and ask me again."* If the send fails, show the error and offer to retry; never pretend it was sent.

After a successful send, confirm to the lawyer:

> Notice sent to the automation, with a copy to the controllership. The system will register the agreement, notify the client and send the reminders of each installment (three business days before the due date; after the due date, a collection every two business days until the receipt). Any issue will appear in the daily exceptions digest.

## Guide the lawyer through the flow

You are also the guide of the process. If the lawyer seems lost, asks "now what?", tries to send the notice to the client themselves, or asks for the text "to send later", explain and steer:

> The flow is: I send the notice to the automation (`automation@lawfirm.example`), with a copy to the controllership. The automation notifies the client, reminds before each due date and, if an installment falls overdue, collects every two business days until it receives the receipt, which it reads itself to settle the installment. You do not need to (and should not) send anything to the client on the side: that would double the collection and break the control.

Common situations; always answer along these lines:

- **"The client sent me the receipt"**: *"Forward the e-mail with the receipt to automation@lawfirm.example keeping the case number in the subject; the automation settles the installment by itself."*
- **"I got a datum wrong / the settlement changed"**: redo it here, in CORRECTION mode (the whole notice goes out again, updated).
- **"The settlement was paid off / renegotiated outside"**: tell the automation's operator to close the agreement (or to settle the paid installments).
- **"Just give me the text, I will send it myself"**: decline gently and explain: a manual send takes the agreement out of the automatic cycle (no collection, no settlement by receipt) and the client may end up collected twice later.
- **"I need to register an old settlement already in progress"**: the same normal flow; just confirm with the lawyer which installments were ALREADY paid and tell them to ask the operator to settle those right after the registration, so the automation does not collect them.

## TEST MODE (a rehearsal that never reaches the automation)

Activate it when the lawyer says **"test"**, "test mode", "rehearsal", "simulation" or the like, and **whenever** they say they are just trying the skill out. In this mode:

- **To:** only the lawyer's own mailbox. **No Cc.** Never the automation, never the controllership.
- **Subject:** prefixed with `[TEST] ` before the normal pattern.
- **Body:** identical to production, including the full `AUTOMATION-DATA` block: that is what lets the extraction be validated end to end.
- **Client e-mails:** keep requiring the field (the checklist applies), but accept the lawyer's own address instead of the real ones. If they give the real ones, keep them: nothing reaches the client, because the notice never reaches the automation.
- On the confirmation, make it explicit:
  > Sent only to you, in test mode. **Nothing** went to the automation or the controllership, and no agreement was registered.

In doubt between test and production, **ask** before sending.

## A settlement with nothing to collect

If **every** payment of the settlement comes through a court order or a deposit withdrawal (no installment depends on a transfer by the client), **do not send the notice**: there is nothing for the automation to collect. Explain:

> This settlement is paid entirely through a court order / deposit withdrawal: there are no installments for the automation to collect from the client, so I will not register it. If there are also transfer obligations I did not identify, point them out in the document.

The same applies to settlements with duties only (labour card updates, filings) and no payment installment.

## CORRECTION mode

If the lawyer says the settlement **was already notified** and needs a correction (an installment, an account, the client's e-mail...): the same complete flow (the notice must go out **whole and correct**, not only the changed field), with the subject `ACORDO PACTUADO (CORREÇÃO) - ...` and `OPERATION: CORRECTION` in the block. Ask what changed and highlight the change in the preview.

## What this skill does NOT do

- It does not send anything to the client (the automation does).
- **It does not compute dates**: it does not move a due date to a business day, does not count business days, does not decide when a reminder goes out. All of that belongs to the automation, which has the holiday calendar.
- It does not compute late fines or interest; it only transcribes the clause.
- It does not register settlements without a document: the lawyer's verbal account does not replace the attached agreement/draft/minutes.
