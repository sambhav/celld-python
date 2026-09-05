import asyncio
import io
import json
import urllib.error

import pytest

from celld_python import AsyncClient, Client, ClientInfo, RemoteError, RetryPolicy


class Reply(io.BytesIO):
    def __init__(self, value, status=200):
        super().__init__(json.dumps(value).encode())
        self.status = status
        self.headers = {}


class Transport:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_retries_preserve_call_identity_and_snapshot_context():
    data = {"nested": {"actor": "Sam"}}
    client = Client("http://localhost/app", context=data, client_info=ClientInfo(name="test"),
                    retries=RetryPolicy(initial_delay=0))
    data["nested"]["actor"] = "changed"
    transport = Transport(urllib.error.URLError("lost response"), Reply({"result": 42}))
    client._opener = transport
    other = client.with_context({"actor": "other"})
    assert client.answer(value=1) == 42
    first, second = (dict(r.header_items()) for r in transport.requests)
    assert first["X-celld-call-id"] == second["X-celld-call-id"]
    assert first["X-celld-attempt"] == "1" and second["X-celld-attempt"] == "2"
    assert json.loads(first["X-celld-context"])["nested"]["actor"] == "Sam"
    assert json.loads(other._context) == {"actor": "other"}
    assert json.loads(first["X-celld-client"])["name"] == "test"


def test_expected_errors_never_retry_and_expose_recovery_id():
    client = Client("http://localhost").with_call_id("known-logical-call-001")
    client._opener = Transport(Reply({"error": {"code": "denied", "message": "No"}}, 400))
    with pytest.raises(RemoteError) as error:
        client.answer()
    assert error.value.code == "denied"
    assert error.value.call_id == "known-logical-call-001"
    assert len(client._opener.requests) == 1


def test_transient_status_retries_and_exhaustion():
    client = Client("http://localhost", retries=RetryPolicy(attempts=2, initial_delay=0))
    client._opener = Transport(Reply({}, 503), Reply({}, 503))
    with pytest.raises(RemoteError, match="unavailable"):
        client.answer()
    assert len(client._opener.requests) == 2


async def test_async_dynamic_client():
    client = AsyncClient("http://localhost")
    client._opener = Transport(Reply({"result": "hello"}))
    assert await client.hello() == "hello"


@pytest.mark.parametrize("endpoint", ["file:///etc/passwd", "https://user:pass@localhost", "https://localhost/?key=a"])
def test_endpoint_validation(endpoint):
    with pytest.raises(ValueError):
        Client(endpoint)


def test_client_information_serializes_pydantic_json_values_and_unicode():
    from datetime import date
    client = Client("http://localhost", client_info=ClientInfo(name="日本語", metadata={"date": date(2026, 1, 1)}))
    assert client._client_info.isascii()
    assert json.loads(client._client_info) == {"name": "日本語", "version": "", "metadata": {"date": "2026-01-01"}}


def test_function_and_self_are_valid_remote_argument_names():
    client = Client("http://localhost")
    client._opener = Transport(Reply({"result": True}))
    assert client.echo(function="name", self="value") is True
    assert json.loads(client._opener.requests[0].data) == {"function": "name", "self": "value"}
