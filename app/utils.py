import numpy as np


def safe_embedding(text: str):
    if not text:
        return np.zeros(1536).tolist()

    np.random.seed(abs(hash(text)) % (2**32))
    return np.random.rand(1536).tolist()