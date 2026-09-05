"""Typed Python functions with optional per-key durable state."""
from .app import ClientInfo, Context, Depends, Error, HTTPError, Invocation, Request, Response, State, Worker
from .decorators import App, delete, get, load_worker, middleware, patch, post, put, route

__all__ = ["App", "ClientInfo", "Context", "Depends", "Error", "Invocation", "Client", "AsyncClient", "RemoteError", "HTTPError", "Request", "Response", "State", "Worker",
           "get", "post", "put", "patch", "delete", "route", "middleware"]


def __getattr__(name):
    if name in {"Client", "AsyncClient", "RetryPolicy", "RemoteError"}:
        from . import client
        return getattr(client, name)
    raise AttributeError(name)
