"""JSON shaping for the revenue analytics API.

The service returns plain Python structures with dates and recordset-free
values; this module turns them into the JSON the BFF receives. It adds no
figure and changes none.

DEFENCE IN DEPTH. No v1 endpoint returns a patient identifier -- every payload
is an aggregate. serialize_payload() still strips any patient / encounter /
admission identifier from an EXECUTIVE payload, so a future change to the
service cannot leak one to that role by accident.
"""
from datetime import date, datetime

PATIENT_IDENTIFIER_KEYS = frozenset({
    "patient_id",
    "patient_name",
    "patient_ids",
    "encounter_id",
    "encounter_ids",
    "admission_id",
    "admission_ids",
    "charge_id",
    "charge_ids",
    "move_id",
    "invoice_id",
})


def _to_json(value, strip):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            key: _to_json(item, strip)
            for key, item in value.items()
            if not (strip and key in PATIENT_IDENTIFIER_KEYS)
        }
    if isinstance(value, (list, tuple)):
        return [_to_json(item, strip) for item in value]
    return value


def serialize_payload(data, access_level):
    """JSON-safe payload; identifiers stripped unless the caller is Finance."""
    return _to_json(data, strip=access_level != "finance")


def serialize_session(data, user, company):
    payload = _to_json(data, strip=False)
    payload["user"] = {"id": user.id, "name": user.name, "login": user.login}
    payload["company"] = {"id": company.id, "name": company.name}
    return payload
