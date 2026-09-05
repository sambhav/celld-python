from typing import Annotated

from celld_python import App, Depends, Error, Invocation, State
from pydantic import BaseModel, Field

app = App

class Counter(BaseModel):
    total: int = 0


@app.middleware
async def trace(invocation, call_next):
    invocation.context["traced"] = True
    return await call_next(invocation)


def current_user(invocation: Invocation) -> str:
    # Caller metadata is untrusted; replace this demonstration with real auth.
    user = invocation.metadata.get("user")
    if not user:
        raise Error("Provide user metadata", code="unauthorized")
    return user


@app.function(key="user_id", namespace="counters")
async def increment(user_id: str, counter: State[Counter],
                    user: Annotated[str, Depends(current_user)],
                    amount: Annotated[int, Field(ge=1, le=100)] = 1) -> Counter:
    if user != user_id:
        raise Error("This counter belongs to another user", code="forbidden")
    counter.value.total += amount
    return counter.value


@app.function(key="user_id", namespace="counters")
def read(user_id: str, counter: State[Counter]) -> Counter:
    return counter.value
