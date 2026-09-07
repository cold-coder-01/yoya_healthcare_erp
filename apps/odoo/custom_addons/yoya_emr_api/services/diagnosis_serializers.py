"""Doctor diagnosis payloads: the clinical record, and nothing else.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE.
Nothing below reads any model except hospital.patient.diagnosis and the
hospital.disease it names. There is no traversal to hospital.encounter,
hospital.billing.account, hospital.charge.line, hospital.payer or
hospital.patient.payer, and therefore no code path that could emit an amount,
a balance, a receipt, an agreement, a membership number or a payer name.

A diagnosis has no financial dimension, even though it may later justify a
claim. That is the whole reason the insurance and credit roles get no access to
this model: a claim justification is produced by a billing surface from a
diagnosis, not by handing the clinical record to the finance desk.

NO ICD CLAIM IS MADE. hospital.disease.code is a free Char with no coding
system, no format validation and no uniqueness. It is serialized as `code` --
not `icd_code` -- because calling it ICD would assert a validation the schema
does not perform. The Odoo field's own label says "ICD Code"; that label is the
vendor's, and it is not repeated here.
"""
from .api_response import date_value, selection_value

# The catalogue is capped server-side. A doctor types three letters and wants
# the shortlist, not a table dump; anything larger is a scroll nobody reads and
# a query nobody intended.
CATALOGUE_DEFAULT_LIMIT = 20
CATALOGUE_MAX_LIMIT = 50


def serialize_disease(disease):
    """One catalogue entry. Safe display fields only."""
    if not disease:
        return None
    return {
        "id": disease.id,
        "name": disease.name,
        "code": disease.code or None,
        "category": disease.category_id.name or None,
    }


def serialize_diagnosis(diagnosis, editable):
    """One recorded diagnosis.

    `editable` is resolved ONCE by the caller from the consultation's state
    rather than per row, because it is a property of the consultation, not of
    the diagnosis. The model refuses a frozen write regardless of what this
    says; this only decides whether the desk offers the control.
    """
    return {
        "id": diagnosis.id,
        "disease": serialize_disease(diagnosis.disease_id),
        "diagnosis_type": selection_value(diagnosis.diagnosis_type),
        "certainty": selection_value(diagnosis.certainty),
        "severity": selection_value(diagnosis.severity),
        "status": selection_value(diagnosis.status),
        "notes": diagnosis.notes or None,
        "diagnosis_date": date_value(diagnosis.diagnosis_date),
        "editable": bool(editable),
    }


def serialize_diagnosis_list(diagnoses, editable):
    """THE diagnosis response shape, for reads and for every mutation.

    Mutations return the WHOLE list rather than the single changed row. Adding a
    primary can be refused because another exists, and removing one frees that
    slot -- so a single-row response would leave the desk holding a list whose
    other rows it can no longer reason about. Returning the list keeps the
    browser's picture identical to the server's after every write, with no
    client-side patching of an array.
    """
    rows = [serialize_diagnosis(diagnosis, editable) for diagnosis in diagnoses]
    return {
        "diagnoses": rows,
        "editable": bool(editable),
        # Resolved server-side so the desk does not re-derive the invariant it
        # is about to be judged against.
        "has_primary": any(row["diagnosis_type"] == "primary" for row in rows),
    }
