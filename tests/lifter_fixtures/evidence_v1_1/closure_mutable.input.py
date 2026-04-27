def make_router(flags):
    def route(text):
        if flags["fast_lane"]:
            return "fast"
        return "normal"
    return route
