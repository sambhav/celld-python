"""Function calls for native Python callers; HTTP stays inside the transport."""
import asyncio
import json
import re
import urllib.error
import urllib.request

from pydantic import TypeAdapter


class RemoteError(Exception):
    def __init__(self, code, message):
        self.code, self.detail = code, message
        super().__init__(f"{code}: {message}")


class Client:
    def __init__(self, endpoint: str, *, metadata=None, timeout=60):
        self.endpoint = endpoint.rstrip("/")
        self.metadata = dict(metadata or {})
        self.timeout = timeout

    def call(self, function: str, **arguments):
        if not re.fullmatch(r"[A-Za-z_]\w*", function):
            raise ValueError("Invalid function name")
        request = urllib.request.Request(self.endpoint + "/" + function,
            data=TypeAdapter(dict).dump_json(arguments),
            headers={"content-type": "application/json", "x-celld-metadata": json.dumps(self.metadata)}, method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read())
            if "error" in payload:
                raise RemoteError(payload["error"]["code"], payload["error"]["message"])
            if response.status >= 400 or "result" not in payload:
                raise RemoteError("execution_error", payload.get("detail", "Invalid worker response"))
            return payload["result"]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **arguments: self.call(name, **arguments)


class AsyncClient(Client):
    async def call(self, function: str, **arguments):
        return await asyncio.to_thread(super().call, function, **arguments)
