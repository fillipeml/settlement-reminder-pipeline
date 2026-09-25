# A complete example notice (fictional data)

> A reference of form and tone. The data below are invented: never copy amounts, accounts or names from this example into a real notice.

**Subject:** `ACORDO PACTUADO - 0001234-74.2099.5.99.0001 - FULANO DE TAL X EXEMPLO ENGENHARIA LTDA`

**To:** automation@lawfirm.example · **Cc:** controllership@lawfirm.example

---

Prezados, bom dia!

Servimo-nos do presente para informar acerca do acordo pactuado na Ação Judicial em destaque.

Abaixo, esclarecimentos e considerações do caso.

**Número do processo:** 0001234-74.2099.5.99.0001
**Cliente:** EXEMPLO ENGENHARIA LTDA | Polo Passivo
**Parte Contrária:** FULANO DE TAL | Polo Ativo
**Juízo:** 1ª Vara do Trabalho | Cidade Exemplo - UF
**Valor da Causa:** R$ 50.000,00

**1. Termos do Acordo:**

**A) Crédito do Reclamante:**

No ato, a empresa contraiu a obrigação de efetuar o pagamento em favor do Autor no importe de R$ 9.000,00 (nove mil reais), que será efetivado da seguinte forma:

1ª parcela, no valor de R$ 3.000,00 (três mil reais), até o dia 22/09/2026;

2ª parcela, no valor de R$ 3.000,00 (três mil reais), até o dia 22/10/2026;

3ª parcela, no valor de R$ 3.000,00 (três mil reais), até o dia 22/11/2026.

**2. Forma de Pagamento:**

Os pagamentos serão feitos diretamente ao Reclamante, por meio de depósito ou transferência na seguinte conta bancária:

FULANO DE TAL
CPF: 000.000.000-00
Banco: 000 - Banco Exemplo
Agência: 0001
Conta Corrente: 12345-6
Chave PIX: 000.000.000-00

**3. Baixa da CTPS:**

A empresa reconhece que a dispensa se deu na modalidade sem justa causa, oportunidade em que se compromete, até o dia 22/09/2026, a dar baixa na CTPS Digital do Reclamante.

---

Esclarecemos que, em caso de atraso no pagamento das parcelas do acordo, incidirá multa de 50% (cinquenta por cento), aplicável a partir do 5º (quinto) dia de mora, sobre a parcela vencida e não quitada.

Por fim, colocamo-nos à disposição para maiores esclarecimentos.

Atenciosamente,
Contencioso Trabalhista | Example Law Firm

— automation data, internal use —

```
=== AUTOMATION-DATA v1 ===
OPERATION: REGISTER
CASE: 0001234-74.2099.5.99.0001
CLIENT: EXEMPLO ENGENHARIA LTDA
CLIENT_SIDE: DEFENDANT
COUNTERPARTY: FULANO DE TAL
CLIENT_EMAILS: finance@exemplo-engenharia.example; board@exemplo-engenharia.example
LAWYER: lawyer@lawfirm.example
COURT: 1ª Vara do Trabalho | Cidade Exemplo - UF
TOTAL_AMOUNT: 9000.00
LATE_CLAUSE: em caso de atraso no pagamento das parcelas do acordo, incidirá multa de 50% (cinquenta por cento), aplicável a partir do 5º (quinto) dia de mora, sobre a parcela vencida e não quitada
OBLIGATION: 1 | crédito do reclamante | beneficiary=Fulano de Tal | honorific=Sr.
PAYMENT: 1 | depósito ou transferência | Banco 000 - Banco Exemplo; Ag 0001; CC 12345-6; CPF 000.000.000-00; PIX 000.000.000-00
INSTALLMENT: 1 | 1 | 2026-09-22 | 3000.00
INSTALLMENT: 1 | 2 | 2026-10-22 | 3000.00
INSTALLMENT: 1 | 3 | 2026-11-22 | 3000.00
DUTY: baixa da CTPS Digital até 2026-09-22
=== END AUTOMATION-DATA ===
```

---

## Variations that appear in real documents (how to handle them)

| In the document | Handling |
|---|---|
| Two obligations (the credit + attorney fees), distinct accounts | `OBLIGATION: 1` and `OBLIGATION: 2`, each with its own `PAYMENT` and its own `INSTALLMENT` lines |
| "1st installment on 15/06 and the others on the same day of the following months" | Expand every date, keep the literal day of the month (no business-day adjustment) and confirm the list with the lawyer |
| A payment through a court order / a deposit withdrawal | NOT an installment; record it in `NO_COLLECTION` |
| Labour card update, severance fund, unemployment forms, payroll filings | Record them in `DUTY` with the deadline |
| The client on the plaintiff side (an out-of-court settlement where the client pays) | `CLIENT_SIDE: PLAINTIFF`; the payer is still the client |
| A final installment with a different amount | Transcribe it exactly; the sum must match the declared total |
