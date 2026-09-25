# Microsoft 365 / Graph setup

The pipeline reads one mailbox and sends from it through Microsoft Graph with app-only authentication (client credentials, no interactive login). The demo needs none of this.

## App registration

1. Entra ID → App registrations → New registration (single tenant). Note the **Tenant ID** and the **Application (client) ID**.
2. Certificates & secrets → New client secret. Note the value once; it expires: put the renewal date in a calendar and update `MS_CLIENT_SECRET` in the `.env` when it does.
3. API permissions → Microsoft Graph → **Application** permissions:
   - `Mail.Send`: reminders, collections, confirmations, digests;
   - `Mail.Read`: the mailbox sweep and locating the notice's conversation in the Sent Items;
   - `Mail.ReadWrite` (optional): replying inside the notice's conversation. Without it the sender falls back to new messages by itself.
   - `Sites.Read.All` (optional): only when `SOURCES` includes `sharepoint`.
4. Grant admin consent.

## Restrict the app to one mailbox

Application permissions are tenant-wide by default. Restrict the app with an Exchange Online **Application Access Policy** so it can only touch the automation's mailbox (a mail-enabled security group containing that account):

```powershell
Connect-ExchangeOnline
New-ApplicationAccessPolicy -AppId <client id> -PolicyScopeGroupId sg-automation@lawfirm.example `
    -AccessRight RestrictAccess -Description "settlement reminders: automation mailbox only"
Test-ApplicationAccessPolicy -Identity someone.else@lawfirm.example -AppId <client id>   # expect Denied
```

The policy takes up to an hour to propagate. A denied call answers 403 with `ErrorAccessDenied`, which the sender treats as "no permission" and works around.

## The mailbox

- `INGEST_MAILBOX` and `MS_SENDER` are the same account: notices and drafts come in, reminders go out, replies with receipts come back.
- The automation never modifies the mailbox; the "already processed" control is the SQLite store.
- If another process shares the mailbox (a triage bot reading group traffic, say), keep the golden rules: this pipeline only handles messages addressed to it (except replies about known cases), it never copies group mailboxes, and its subjects never start with `RE:`/`FW:`.

## Environment

```
MS_TENANT_ID=...
MS_CLIENT_ID=...
MS_CLIENT_SECRET=...
MS_SENDER=automation@lawfirm.example
INGEST_MAILBOX=automation@lawfirm.example
```

Start with `DRY_RUN=true`: the whole routine runs and records what it would send; nothing leaves. Switch to `DRY_RUN=false` when the digest looks right for a few days; a simulation never consumes the real send.
