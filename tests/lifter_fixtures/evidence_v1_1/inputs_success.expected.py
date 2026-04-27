from dendra import Switch

class RouteSwitch(Switch):

    def _evidence_text(self, text: str, kind: str) -> object:
        return text

    def _evidence_kind(self, text: str, kind: str) -> object:
        return kind

    def _evidence_handler_priority(self, text: str, kind: str) -> object:
        return getattr(self, f'handle_{kind}').priority

    def _rule(self, evidence) -> str:
        if evidence.handler_priority > 5:
            return 'high'
        return 'low'
