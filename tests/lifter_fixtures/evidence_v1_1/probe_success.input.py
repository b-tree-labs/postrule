@evidence_via_probe(charge_status="api.charge_probe(req)")
def maybe_charge(req):
    response = api.charge(req)
    if response.ok:
        notify(req)
        return "charged"
    return "skipped"
