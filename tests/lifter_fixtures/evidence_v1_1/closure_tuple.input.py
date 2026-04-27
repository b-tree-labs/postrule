def make_router(allowed_tags: tuple):
    def route(item):
        if item.tag in allowed_tags:
            return "permitted"
        return "blocked"
    return route
