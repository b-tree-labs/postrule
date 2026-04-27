def route_request(method, path, headers):
    if method == 'POST' and path.startswith('/api'):
        record_api_call(method, path)
        return 'api'
    elif path.startswith('/admin'):
        return 'admin'
    else:
        return 'ui'
