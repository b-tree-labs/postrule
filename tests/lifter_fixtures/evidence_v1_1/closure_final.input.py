from typing import Final


def make_gate(threshold: Final[int]):
    def gate(score):
        if score > threshold:
            return "high"
        return "low"
    return gate
