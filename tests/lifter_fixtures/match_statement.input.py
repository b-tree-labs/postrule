def classify(action):
    match action:
        case 'create':
            audit_create(action)
            return 'write'
        case 'read':
            return 'read'
        case _:
            audit_unknown(action)
            return 'unknown'
