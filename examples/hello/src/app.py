from celld_python import Worker
from pydantic import BaseModel, Field

app = Worker()


class Greeting(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.get("/")
def hello() -> dict:
    return {"message": "Hello from Python on celld!"}


@app.post("/greet")
def greet(body: Greeting) -> dict:
    return {"message": f"Hello, {body.name}!"}
