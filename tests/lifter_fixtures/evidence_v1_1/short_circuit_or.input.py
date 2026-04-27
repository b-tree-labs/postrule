def authorize(user):
    if cheap_check(user) or expensive_db_lookup(user):
        return "allowed"
    return "denied"
