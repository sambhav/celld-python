import numpy as np
from celld import App, Error

app = App

@app.function
def mean(values: list[float]) -> float:
    if not values:
        raise Error("Provide at least one value")
    return float(np.mean(values))
