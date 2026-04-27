from dendra import Switch

class ClassifySwitch(Switch):

    def _evidence_input(self, action) -> object:
        return action

    def _rule(self, evidence) -> str:
        action = evidence.input
        match action:
            case 'create':
                return 'write'
            case 'read':
                return 'read'
            case _:
                return 'unknown'

    def _on_write(self, action):
        audit_create(action)

    def _on_unknown(self, action):
        audit_unknown(action)
