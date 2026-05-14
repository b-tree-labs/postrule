# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""WebhookVerdictSource — pull verdicts from an external HTTP endpoint.

Run: `python examples/13_webhook_verdicts.py`

The pattern: a downstream system (ticketing tool, fraud
detector, payment processor, CRM) can report outcomes but
doesn't push them. You pull. Each classification resolves to a
verdict by POSTing to the external system's verdict endpoint
and parsing the response.

Contract the external endpoint must honor:

    POST <endpoint>
    body: {"input": <classifier input>, "label": <classified label>}
    response (200): {"outcome": "correct" | "incorrect" | "unknown"}

Failure modes (network error, timeout, non-2xx, malformed JSON,
unknown outcome value) all collapse to :class:`Verdict.UNKNOWN`
— external outages must not break the caller's audit loop.

This example runs against a local in-process HTTP mock to keep
the walkthrough self-contained. Swap in a real HTTPS endpoint +
auth headers for production:

    src = WebhookVerdictSource(
        "https://crm.example.com/api/v1/ticket-verdicts",
        headers={"X-API-Key": os.environ["CRM_API_KEY"]},
        timeout=10.0,
        name="crm",
    )
"""

from __future__ import annotations

from postrule import Verdict
from postrule.verdicts import WebhookVerdictSource


def main() -> None:
    src = WebhookVerdictSource(
        "http://localhost/verdict-endpoint",
        timeout=2.0,
        name="example-crm",
    )

    # For the walkthrough, stub the HTTP call with a deterministic
    # fake. The real WebhookVerdictSource carries an httpx client
    # that would hit the endpoint.
    class _FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _fake_post(url, *, json, **kwargs):
        # Pretend the external system knows: "crash" tickets are
        # bugs, "request" tickets are features.
        title = str(json["input"].get("title", "")).lower()
        label = json["label"]
        if "crash" in title and label == "bug":
            return _FakeResponse({"outcome": Verdict.CORRECT.value})
        if "request" in title and label == "feature_request":
            return _FakeResponse({"outcome": Verdict.CORRECT.value})
        return _FakeResponse({"outcome": Verdict.INCORRECT.value})

    src._httpx.post = _fake_post  # type: ignore[attr-defined]

    print(f"source stamp: {src.source_name}\n")
    cases = [
        ({"title": "app crashes at startup"}, "bug"),
        ({"title": "feature request: dark mode"}, "feature_request"),
        ({"title": "billing question"}, "bug"),  # mismatched; CRM says nope
    ]
    for ticket, classified in cases:
        v = src.judge(ticket, classified)
        print(f"  {ticket['title']:35s} classified={classified:18s} -> {v.value}")


if __name__ == "__main__":
    main()
