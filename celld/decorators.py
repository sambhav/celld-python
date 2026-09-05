"""Module-local registration: ordinary functions, no app object required."""
import importlib
import sys

from .app import Worker


def _worker(module_name):
    module = sys.modules[module_name]
    if not hasattr(module, "__celld_worker__"):
        module.__celld_worker__ = Worker()
    return module.__celld_worker__


def route(method, path, **options):
    def register(function):
        return _worker(function.__module__).route(method, path, **options)(function)
    return register


def get(path="/", **options):
    return route("GET", path, **options)


def post(path="/", **options):
    if callable(path):
        function = path
        return route("POST", "/" + function.__name__, **options)(function)
    return route("POST", path, **options)


def put(path="/", **options):
    return route("PUT", path, **options)


def patch(path="/", **options):
    return route("PATCH", path, **options)


def delete(path="/", **options):
    return route("DELETE", path, **options)


def middleware(function):
    return _worker(function.__module__).middleware(function)


def function(handler=None, *, key=None, namespace=None):
    def register(fn):
        return _worker(fn.__module__).function(fn, key=key, namespace=namespace)
    return register(handler) if handler is not None else register


class App:
    """Decorator namespace: ``app = App``, then ``@app.function``.

    Registrations belong to the decorated function's module. ``App()`` also
    works when an explicit, independently composed Worker object is desired.
    """
    get = staticmethod(get)
    post = staticmethod(post)
    put = staticmethod(put)
    patch = staticmethod(patch)
    delete = staticmethod(delete)
    route = staticmethod(route)
    middleware = staticmethod(middleware)
    function = staticmethod(function)

    def __new__(cls):
        return Worker()


def load_worker(entrypoint):
    """Runtime entrypoint for modules using the function decorators."""
    module_name, _, attribute = entrypoint.partition(":")
    module = importlib.import_module(module_name)
    candidate = getattr(module, attribute or "app", None)
    if isinstance(candidate, Worker):
        return candidate
    if attribute and candidate is not App:
        raise TypeError(f"{entrypoint} must reference App or a Worker instance")
    worker = getattr(module, "__celld_worker__", None)
    if worker is None:
        raise ValueError(f"{module_name} has no decorated endpoints")
    return worker
