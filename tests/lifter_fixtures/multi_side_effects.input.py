def route(req):
    if req.method == 'POST':
        log_request(req)
        emit_metric(req)
        notify_audit(req)
        return 'write'
    else:
        log_request(req)
        return 'read'
