# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-25

First public release: the English, rebranded port of a settlement reminder pipeline built for a law firm's controllership, with fictional demo data.

### Added

- Registration of agreements from the lawyer's notice e-mail through a versioned `AUTOMATION-DATA` block parsed deterministically (no model call), with corrections, test mode, soft doubts that leave the agreement pending, and a fallback path that reads a forwarded PDF draft with the model and matches the client directory.
- Reminder cycle: a preventive reminder N business days before each due date (national and state holidays, the original date moved to the next business day and explained in the e-mail), a catch-up window for missed runs, collections every N business days up to a cap with at most one collection per agreement per day, and escalation to the lawyer when the cap is reached.
- Settlement by receipt: attachments of the client's replies read by the model (PDF or image), matched to the oldest open installment by exact amount; unmatched or unreadable attachments, failed readings and an unavailable mailbox put the collection on hold ("never collect from someone who may have paid").
- Idempotent send history and agreement store in one SQLite file: sent, simulated and failed sends with the run day, processed messages, settlements, holds, notice threads; a dry-run simulation never consumes the real send.
- Portuguese client e-mails (amount in words, feminine ordinals, honorific with the right preposition, late clause transcribed) rendered from Jinja2 templates with a brand mark, sent through Microsoft Graph as replies in the notice's conversation with a fallback to new messages; controllership, team and per-payer copies.
- Operator tooling: daily exceptions digest, management CLI (list, installments, settle, unsettle, close, reopen, set-emails, holds, release-hold, link-threads), audit of the real sends against the store, template previews, a Windows scheduled-task installer and a Dockerfile.
- Demo mode with a fixture mailbox of eight messages, recorded readings and an outbox on disk, pinned to 2026-09-17 and movable with `--date`; 227 offline tests; CI with lint, tests, reproducible fixtures, the demo run twice and along the walk to the escalation, and a secret scan.
- The `settlement-notice` assistant skill that writes and sends the notice, with a fictional example.

[0.1.0]: https://github.com/fillipeml/settlement-reminder-pipeline/releases/tag/v0.1.0
