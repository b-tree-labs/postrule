def gate(user):
    if has_account(user) and is_active(user):
        return "ok"
    return "blocked"
