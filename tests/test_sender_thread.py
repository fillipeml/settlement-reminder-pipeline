"""One thread per agreement: the notice opens the conversation, reminders/collections reply.

Replying requires Mail.ReadWrite on the Graph app; without the permission (403) everything
falls back to a new message: a send never fails because of the thread.
"""

from __future__ import annotations

from settlement_reminders.graph import GraphError
from settlement_reminders.mailer import EmailSender
from tests.factories import CASE_PAYER_RULE


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeGraph:
    """Records the calls; simulates the conversation and, optionally, the 403."""

    def __init__(self, *, conversation=None, sent_items=None, no_readwrite=False):
        self.calls: list[tuple] = []
        self.conversation = (
            conversation
            if conversation is not None
            else [
                {"id": "m-root", "sentDateTime": "2026-09-04T14:34:00Z"},
                {"id": "m-client", "receivedDateTime": "2026-09-21T18:57:00Z"},
            ]
        )
        self.sent_items = sent_items or []
        self.no_readwrite = no_readwrite

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if "sentitems" in path:
            return {"value": self.sent_items}
        return {"value": self.conversation}

    def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        if path.endswith("/createReply") or path.endswith("/messages"):
            if self.no_readwrite:
                raise GraphError(f"POST {path} -> 403: ErrorAccessDenied")
            return _Resp({"id": "draft-1", "conversationId": "conv-1"})
        return _Resp({})

    def patch(self, path, json):
        self.calls.append(("PATCH", path, json))
        return _Resp({})


_IMG = {
    "name": "mark.png",
    "contentType": "image/png",
    "contentId": "brand-mark",
    "content": b"\x89PNG",
}


def _sender(graph):
    return EmailSender(graph, "automation@lawfirm.example", dry_run=False)


def test_reply_in_thread_replies_to_the_most_recent_message_with_our_subject():
    graph = _FakeGraph()
    ok = _sender(graph).reply_in_thread(
        conversation_id="conv-1",
        to=["client@x.com"],
        cc=["ctrl@lawfirm.example"],
        subject="PAGAMENTO EM ABERTO - 1ª PARCELA",
        html="<p>oi</p>",
        inline_images=[_IMG],
    )

    assert ok is True
    methods = [(c[0], c[1].rsplit("/", 1)[-1]) for c in graph.calls]
    # locates the conversation, replies to the most recent (the client's), edits, attaches, sends
    assert methods == [
        ("GET", "messages"),
        ("POST", "createReply"),
        ("PATCH", "draft-1"),
        ("POST", "attachments"),
        ("POST", "send"),
    ]
    assert "/messages/m-client/createReply" in graph.calls[1][1]
    patch = graph.calls[2][2]
    assert patch["subject"] == "PAGAMENTO EM ABERTO - 1ª PARCELA"
    assert patch["toRecipients"] == [{"emailAddress": {"address": "client@x.com"}}]
    assert patch["ccRecipients"] == [{"emailAddress": {"address": "ctrl@lawfirm.example"}}]
    assert graph.calls[3][2]["isInline"] is True
    assert not any(c[1].endswith("/sendMail") for c in graph.calls)


def test_without_mail_readwrite_falls_back_to_a_new_message_and_does_not_insist():
    graph = _FakeGraph(no_readwrite=True)
    s = _sender(graph)

    ok = s.reply_in_thread(conversation_id="conv-1", to=["client@x.com"], subject="A", html="<p/>")
    assert ok is False
    assert graph.calls[-1][1].endswith("/sendMail")  # the e-mail WENT OUT, as a new one

    # second time: does not even try the thread (remembered the 403)
    before = len(graph.calls)
    s.reply_in_thread(conversation_id="conv-1", to=["client@x.com"], subject="B", html="<p/>")
    new_calls = graph.calls[before:]
    assert [c[1].rsplit("/", 1)[-1] for c in new_calls] == ["sendMail"]


def test_empty_conversation_or_generic_error_falls_back_to_a_new_message():
    graph = _FakeGraph(conversation=[])
    ok = _sender(graph).reply_in_thread(
        conversation_id="conv-x", to=["c@x.com"], subject="A", html="<p/>"
    )
    assert ok is False
    assert graph.calls[-1][1].endswith("/sendMail")


def test_send_tracked_returns_the_conversation_and_sends_the_draft():
    graph = _FakeGraph()
    conversation = _sender(graph).send_tracked(
        to=["client@x.com"],
        cc=["ctrl@lawfirm.example"],
        subject="ACORDO PACTUADO - X",
        html="<p/>",
        inline_images=[_IMG],
    )
    assert conversation == "conv-1"
    assert [c[1].rsplit("/", 1)[-1] for c in graph.calls] == ["messages", "send"]
    draft = graph.calls[0][2]
    assert draft["subject"] == "ACORDO PACTUADO - X"
    assert draft["attachments"][0]["contentId"] == "brand-mark"


def test_send_tracked_without_readwrite_sends_as_new_and_returns_none():
    graph = _FakeGraph(no_readwrite=True)
    conversation = _sender(graph).send_tracked(to=["c@x.com"], subject="A", html="<p/>")
    assert conversation is None
    assert graph.calls[-1][1].endswith("/sendMail")


def test_find_thread_picks_the_oldest_notice_and_ignores_tests():
    graph = _FakeGraph(
        sent_items=[
            {
                "subject": f"[TEST] ACORDO PACTUADO - {CASE_PAYER_RULE}",
                "conversationId": "c-test",
                "sentDateTime": "2026-09-03T12:00:00Z",
            },
            {
                "subject": f"ACORDO PACTUADO - {CASE_PAYER_RULE} - A X B",
                "conversationId": "c-copy",
                "sentDateTime": "2026-09-09T18:38:00Z",
            },
            {
                "subject": f"ACORDO PACTUADO - {CASE_PAYER_RULE} - A X B",
                "conversationId": "c-root",
                "sentDateTime": "2026-09-04T14:34:00Z",
            },
        ]
    )
    assert _sender(graph).find_thread(CASE_PAYER_RULE) == "c-root"
    assert (
        f"startswith(subject, 'ACORDO PACTUADO - {CASE_PAYER_RULE}')"
        in graph.calls[0][2]["$filter"]
    )


def test_dry_run_does_not_touch_graph():
    graph = _FakeGraph()
    s = EmailSender(graph, "automation@lawfirm.example", dry_run=True)
    assert s.reply_in_thread(conversation_id="c", to=["a@x.com"], subject="A", html="<p/>") is True
    assert s.send_tracked(to=["a@x.com"], subject="A", html="<p/>") is None
    assert graph.calls == []
