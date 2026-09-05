from celld_python import App

app = App


@app.function
def hello(name: str = "world") -> str:
    return f"Hello, {name}"
