"""Native Python transport for generated and dynamic function clients."""
from __future__ import annotations

import asyncio
import copy
import http.client
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Self

from pydantic import BaseModel, TypeAdapter

from .app import ClientInfo

_JSON = TypeAdapter(Any)
_MAX_RESPONSE = 1024 * 1024


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries share one call ID and one total timeout."""
    attempts: int = 3
    initial_delay: float = 0.1
    max_delay: float = 2.0

    def __post_init__(self):
        if not 1 <= self.attempts <= 100 or not 0 <= self.initial_delay <= self.max_delay <= 60:
            raise ValueError("Invalid retry policy")


class RemoteError(Exception):
    def __init__(self, code: str, message: Any, *, call_id: str = "", status: int | None = None):
        self.code, self.detail, self.call_id, self.status = code, message, call_id, status
        super().__init__(f"{code}: {message}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, endpoint: str, *, context: BaseModel | dict | None = None,
                 token: str | None = None, client_info: ClientInfo | None = None,
                 timeout: float = 60, retries: RetryPolicy | None = None):
        url = urllib.parse.urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError("Endpoint must be an HTTP(S) URL without credentials, query or fragment")
        if not 0 < timeout <= 3600:
            raise ValueError("Timeout must be between 0 and 3600 seconds")
        if token is not None and any(c in token for c in "\r\n"):
            raise ValueError("Invalid token")
        self.endpoint = endpoint.rstrip("/")
        self.timeout, self.retries, self._token = timeout, retries or RetryPolicy(), token
        self._context = self._encode_context(context)
        self._client_info = json.dumps((client_info or ClientInfo()).model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"))
        if len(self._client_info) > 4096:
            raise ValueError("Client information exceeds 4 KiB")
        self._call_id = None
        # Never forward credentials through redirects; application redirects are
        # not part of the function protocol. No global urllib opener is changed.
        self._opener = urllib.request.build_opener(_NoRedirect())

    @staticmethod
    def _encode_context(context):
        value = _JSON.dump_python(context if context is not None else {}, mode="json", by_alias=True)
        if not isinstance(value, dict):
            raise ValueError("Context must be a model or object")
        result = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
        if len(result) > 16384:
            raise ValueError("Context exceeds 16 KiB")
        return result

    def with_context(self, context: BaseModel | dict) -> Self:
        """An independent client with a snapshot of this call context."""
        result = copy.copy(self)
        result._context = self._encode_context(context)
        return result

    def with_call_id(self, call_id: str) -> Self:
        """Reuse an ID for one logical call, including recovery after a timeout."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,128}", call_id):
            raise ValueError("Call IDs require 16–128 URL-safe characters")
        result = copy.copy(self)
        result._call_id = call_id
        return result

    def call(self, function: str, /, **arguments):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", function):
            raise ValueError("Invalid function name")
        data = _JSON.dump_json(arguments, by_alias=True)
        if len(data) > _MAX_RESPONSE:
            raise ValueError("Arguments exceed 1 MiB")
        call_id = self._call_id or str(uuid.uuid4())
        deadline = time.monotonic() + self.timeout
        last_error = None
        for attempt in range(1, self.retries.attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            headers = {"content-type": "application/json", "x-celld-context": self._context,
                       "x-celld-call-id": call_id, "x-celld-request-id": call_id,
                       "x-celld-attempt": str(attempt), "x-celld-client": self._client_info}
            if self._token:
                headers["authorization"] = "Bearer " + self._token
            request = urllib.request.Request(self.endpoint + "/" + function, data=data, headers=headers, method="POST")
            delay = 0.0
            try:
                try:
                    response = self._opener.open(request, timeout=remaining)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    raw = response.read(_MAX_RESPONSE + 1)
                    if len(raw) > _MAX_RESPONSE:
                        raise RemoteError("protocol_error", "Worker response exceeds 1 MiB", call_id=call_id)
                    status = response.status
                    try:
                        payload = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        payload = None
                    if status in {429, 502, 503, 504}:
                        last_error = RemoteError("unavailable", f"Worker unavailable ({status})", call_id=call_id, status=status)
                        try:
                            delay = min(float(response.headers.get("Retry-After", "0")), self.retries.max_delay)
                        except ValueError:
                            pass
                    elif isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                        error = payload["error"]
                        raise RemoteError(error.get("code", "execution_error"), error.get("message", "Worker failed"), call_id=call_id, status=status)
                    elif status == 200 and isinstance(payload, dict) and "result" in payload:
                        return payload["result"]
                    else:
                        raise RemoteError("protocol_error", f"Invalid worker response ({status})", call_id=call_id, status=status)
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as error:
                last_error = RemoteError("transport_error", str(error), call_id=call_id)
            if attempt < self.retries.attempts:
                backoff = min(self.retries.max_delay, self.retries.initial_delay * 2 ** (attempt - 1))
                delay = max(delay, random.uniform(0, backoff))
                if delay >= deadline - time.monotonic():
                    break
                time.sleep(delay)
        raise last_error or RemoteError("timeout", "Call deadline exceeded", call_id=call_id)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **arguments: self.call(name, **arguments)


class AsyncClient(Client):
    async def call(self, function: str, /, **arguments):
        # Cancellation stops awaiting; it cannot undo an already submitted call.
        return await asyncio.to_thread(super().call, function, **arguments)
