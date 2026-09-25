# Glossary

Brazilian legal and operational terms behind the English identifiers, and the Portuguese that stays in the client-facing e-mails on purpose.

## The cycle

| Portuguese | In the code | Meaning |
|---|---|---|
| acordo (judicial/extrajudicial) | agreement, settlement | A settlement of a lawsuit, paid by the firm's client in installments. |
| comunicado "ACORDO PACTUADO" | notice | The e-mail the lawyer sends to the client announcing the settlement; it registers the agreement and opens the conversation thread. |
| minuta / termo de acordo | draft | The settlement document (PDF); the legacy entry path, read by the model. |
| cliente pagador | payer, client | The firm's client, who pays the installments and receives every reminder, whatever side of the case it is on. |
| parte contrária | claimant, counterparty | Who receives the settlement. |
| parcela | installment | One dated payment. `external_id` = `{case}#O{obligation}P{number}`. |
| obrigação | obligation | A payment block with its own beneficiary and bank account (the credit, attorney fees...). |
| data limite | due date | The original due date moved to the next business day (national + state holidays). |
| lembrete de pagamento | preventive reminder | The e-mail sent N business days before the due date. |
| cobrança ("PAGAMENTO EM ABERTO") | collection | The e-mail sent every N business days after the due date without a settlement, up to the cap. |
| escalada | escalation | Cap reached without a settlement: one e-mail to the lawyer; the automation stops collecting that installment. |
| baixa | settlement (of an installment) | The record that an installment was paid: by a receipt matched by amount, or manually. |
| comprovante | receipt | The client's proof of payment (a PDF or a screenshot of a PIX transfer), read by the model. |
| retenção de cobrança | hold | Collection and escalation suspended for a case until a human reconciles an attachment that did not settle. |
| resumo de exceções | exceptions digest | The daily e-mail to the operator listing only what needs a human. |
| modo sombra | shadow mode, `DRY_RUN` | Everything runs and is recorded, nothing is sent. |
| controladoria | controllership | The team that follows every e-mail in Cc (awareness, not approval) and gets confirmations and escalations. |
| CNJ (número do processo) | case number | The national case-number format; the key that matches replies to agreements. |

## Kept in Portuguese in the e-mails

| Text | Why it stays |
|---|---|
| "Prezados, bom dia!" / "Permanecemos à disposição." | The firm's standard greeting and closing. |
| "LEMBRETE DE PAGAMENTO DA 2ª PARCELA DE ACORDO - {case} - {CLAIMANT} X {PAYER}" | The subject pattern the team and the clients know; the case number in it is what threads the replies. |
| "segunda e última parcela", "dois mil cento e sessenta e seis reais..." | Feminine ordinals and amounts spelled out, as court practice expects. |
| "ao Sr." / "à Sra." / "ao Dr." / "à Dra." | The honorific of the beneficiary, with the right preposition. |
| "Esclarecemos que, {late clause}." | The late-payment clause transcribed from the settlement, never computed. |
| "Atenciosamente, Controladoria Jurídica" | The signature is a department, never a person. |
| PIX, CPF, CNPJ, agência, conta | Brazilian banking terms in the bank details. |
