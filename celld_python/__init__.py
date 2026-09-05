"""Small typed HTTP workers, with optional per-key durable state."""
from .app import Depends, Error, HTTPError, Invocation, Request, Response, State, Worker
from .decorators import App, delete, get, load_worker, middleware, patch, post, put, route

__all__ = ["App", "Depends", "Error", "Invocation", "Client", "AsyncClient", "RemoteError", "HTTPError", "Request", "Response", "State", "Worker",
           "get", "post", "put", "patch", "delete", "route", "middleware"]


def __getattr__(name):
    if name in {"Client", "AsyncClient", "RemoteError"}:
        from . import client
        return getattr(client, name)
    raise AttributeError(name)
