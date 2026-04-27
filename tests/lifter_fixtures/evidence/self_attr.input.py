def check(self, request):
    if self.cache_state == "warm":
        return "cached"
    return "miss"
