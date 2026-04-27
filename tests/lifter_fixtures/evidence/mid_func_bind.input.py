def route(text):
    user = db.lookup(text)
    if user.tier == "vip":
        return "vip"
    return "standard"
