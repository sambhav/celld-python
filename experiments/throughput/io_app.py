"""Quote service: typed inputs/output, dependency injection, middleware, real I/O.

The benchmark replaces UPSTREAM_URL with a local controlled pricing service.
Deploy the same function with the URL of your own HTTP service.
"""
import json
from typing import Annotated
from celld import App, Context, Depends
from pydantic import BaseModel, Field
from pyodide.http import pyfetch

app = App
UPSTREAM_URL = "__UPSTREAM_URL__"

class Caller(BaseModel):
    pass

class Price(BaseModel):
    customer: str
    unit_price_cents: int
    currency: str
    stock: int

class Quote(BaseModel):
    customer: str
    total_cents: int
    currency: str
    trace: str

def pricing_url() -> str:
    return UPSTREAM_URL

@app.middleware
async def trace(call, next):
    call.context.local["trace"] = call.context.call_id
    return await next(call)

@app.function
async def hello(name: Annotated[str, Field(min_length=1, max_length=128)],
                context: Context[Caller], url: Annotated[str, Depends(pricing_url)],
                quantity: Annotated[int, Field(ge=1, le=10)] = 2) -> Quote:
    response = await pyfetch(url, method="POST", headers={"content-type":"application/json"}, body=json.dumps({"name":name}))
    response.raise_for_status()
    price = Price.model_validate(await response.json())
    if price.customer != name or price.stock < quantity:
        raise ValueError("Invalid pricing response or insufficient stock")
    return Quote(customer=name, total_cents=price.unit_price_cents*quantity,
                 currency=price.currency, trace=context.local["trace"])
