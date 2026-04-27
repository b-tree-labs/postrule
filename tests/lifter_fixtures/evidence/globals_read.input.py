def gate(text):
    if FEATURE_FLAGS["fast_lane"]:
        return "fast"
    return "slow"
