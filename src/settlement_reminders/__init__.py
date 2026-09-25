"""Settlement reminder pipeline: notices in, reminders and collections out, receipts settle.

The model reads documents (a settlement draft, a payment receipt); every decision that sends
an e-mail, settles an installment or closes an agreement is a deterministic rule in code.
"""

__version__ = "0.1.0"
