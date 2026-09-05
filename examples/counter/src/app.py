from typing import Annotated

from celld import App, Context, Depends, Invocation, State
from pydantic import BaseModel, Field

app = App


class Counter(BaseModel):
    total: int = 0
    last_actor: str = ""


class Caller(BaseModel):
    actor: str


@app.middleware
async def trace(invocation: Invocation, call_next):
    invocation.context.local["traced"] = True
    return await call_next(invocation)


def actor(ctx: Context[Caller]) -> str:
    # Context is caller data. Authentication belongs in your host adapter.
    return ctx.data.actor


@app.function(key="counter_id", namespace="counters")
async def increment(counter_id: str, counter: State[Counter],
                    caller: Annotated[str, Depends(actor)],
                    amount: Annotated[int, Field(ge=1, le=100)] = 1) -> Counter:
    counter.value.total += amount
    counter.value.last_actor = caller
    return counter.value


@app.function(key="counter_id", namespace="counters")
def read(counter_id: str, counter: State[Counter]) -> Counter:
    return counter.value


class CallDetails(BaseModel):
    actor: str
    scope: str
    call_id: str
    attempt: int
    client: str
    host: dict
    traced: bool


@app.function
def details(ctx: Context[Caller]) -> CallDetails:
    """Show the caller data and the separate host-supplied context."""
    return CallDetails(actor=ctx.data.actor, scope=ctx.scope, call_id=ctx.call_id,
                       attempt=ctx.attempt, client=ctx.client.name, host=ctx.host,
                       traced=ctx.local["traced"])
