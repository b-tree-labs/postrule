def chain(req):
    if first_check(req) or second_check(req) or third_check(req):
        return "yes"
    return "no"
