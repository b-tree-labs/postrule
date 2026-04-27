from dendra import Switch

class GateSwitch(Switch):

    def _evidence_text(self, text) -> object:
        return text

    def _evidence_fast_lane(self, text) -> object:
        return FEATURE_FLAGS['fast_lane']

    def _rule(self, evidence) -> str:
        if evidence.fast_lane:
            return 'fast'
        return 'slow'
