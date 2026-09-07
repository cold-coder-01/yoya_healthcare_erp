"""Doctor medication payloads: the clinical order, and nothing priced.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE.
Nothing below reads hospital.charge.line, hospital.billing.account,
hospital.charge.receipt, hospital.payer, hospital.patient.payer,
hospital.fiscal.transaction, hospital.stock.consumption,
hospital.inventory.item or hospital.inventory.batch.

THE CATALOGUE IS CLINICAL ONLY, AND HERE THAT MATTERS MORE THAN IT DID FOR
IMAGING. hospital.pharmacy.medicine carries `sale_price` directly once
hospital_pharmacy_fiscal_bridge is installed -- not merely a link to a priced
service, an actual number on the record a doctor is searching. It is never
serialized, and neither is `billing_service_id` nor `inventory_item_id`.

NO BILLING STATUS CROSSES THIS BOUNDARY AT ALL, which is a real difference from
laboratory and radiology. Both of those expose a `billing_blocked` boolean,
because both raise their charges at confirmation and a doctor genuinely needs to
know the desk is waiting on money. Medication raises no charge until the
PHARMACIST marks the dispense ready, so at the moment a doctor is looking there
is usually nothing to be blocked on, and hospital.pharmacy.dispense carries no
equivalent field (D8, deferred). Rather than invent one for a badge, the ready
state is reported coarsely and honestly as 'Ready at pharmacy'.

THE STATUS IS DERIVED FROM THE DISPENSE, NEVER FROM prescription.state.
Nothing in the runtime calls hospital.prescription.action_mark_dispensed(), so
the header sits at `confirmed` forever no matter what pharmacy does (D6/D5,
deferred). Reading prescription.state as a pharmacy status would tell a doctor
that a fully dispensed prescription is still outstanding.
"""
from .api_response import date_value, datetime_value, selection_value

# A doctor types three letters and wants the shortlist, not the formulary.
# Anything larger is a scroll nobody reads and a query nobody intended, so the
# cap is enforced in SQL and the client cannot widen it.
CATALOGUE_DEFAULT_LIMIT = 20
CATALOGUE_MAX_LIMIT = 50

# The operational vocabulary a prescriber needs, derived from REAL backend
# state. Five dispense states plus the prescription's own cancellation.
#
# NO KEY BELOW CLAIMS THE PATIENT HAS THE MEDICINE except `dispensed`, and none
# claims anything about money. 'ready_at_pharmacy' means the pharmacist has
# priced and prepared it; whether the patient has paid is the cashier's screen
# and the pharmacist's gate, not a badge on a prescriber's list.
STATUS_LABELS = {
    "draft": "Draft",
    "awaiting_pharmacy": "Awaiting pharmacy",
    "ready_at_pharmacy": "Ready at pharmacy",
    "partially_dispensed": "Partially dispensed",
    "dispensed": "Dispensed",
    "cancelled": "Cancelled",
}

# hospital.pharmacy.dispense.state -> the doctor-facing key.
_DISPENSE_STATUS = {
    "draft": "awaiting_pharmacy",
    "ready": "ready_at_pharmacy",
    "partial": "partially_dispensed",
    "dispensed": "dispensed",
    "cancelled": "cancelled",
}


def medication_status(prescription, dispense):
    """One operational status key. No money, no payer, no amount, no stock."""
    if prescription.state == "cancelled":
        return "cancelled"
    if prescription.state == "draft":
        return "draft"
    if not dispense:
        # Confirmation composes a dispense, so this is the pre-confirmation or
        # legacy shape rather than a state pharmacy is working.
        return "awaiting_pharmacy"
    return _DISPENSE_STATUS.get(dispense.state, "awaiting_pharmacy")


def correlate_dispense_lines(prescription, dispense):
    """Map prescription lines to dispense lines, ONLY where it is certain.

    THE PROBLEM THIS REFUSES TO PAPER OVER (D6, deferred).
    hospital.pharmacy.dispense.line has no prescription_line_id. It carries a
    medicine and a `sequence` copied from the prescription line it came from, so
    correlation has to be inferred -- and inference is exactly what must not
    happen quietly on a medication record. Two lines of the same medicine on one
    prescription is legitimate (two courses, two strengths of instruction), and
    guessing which of them a partial dispense filled would tell a prescriber
    that one course was supplied and another was not, on no evidence.

    THE RULE: a prescription line gets progress only when the pair
    (medicine_id, sequence) identifies exactly ONE line on each side. For every
    prescription this API writes that is always true -- create_from_consultation
    assigns 10, 20, 30... and hospital_pharmacy copies the sequence across -- so
    the normal case is fully itemised. For a prescription typed into the Odoo
    form, where every line may default to sequence 10, duplicate medicines
    collapse and those lines report no progress at all.

    Ambiguous lines are OMITTED from the result rather than guessed, and the
    serializer renders null for them. A missing number is a smaller lie than a
    wrong one.
    """
    if not dispense:
        return {}

    def _key(line, medicine_field):
        return (line[medicine_field].id, line.sequence)

    prescription_keys = {}
    for line in prescription.line_ids:
        prescription_keys.setdefault(_key(line, "medicine_id"), []).append(line)

    dispense_keys = {}
    for line in dispense.line_ids:
        dispense_keys.setdefault(_key(line, "medicine_id"), []).append(line)

    matched = {}
    for key, lines in prescription_keys.items():
        counterparts = dispense_keys.get(key, [])
        # Unique on BOTH sides, or it is not a correlation.
        if len(lines) == 1 and len(counterparts) == 1:
            matched[lines[0].id] = counterparts[0]
    return matched


def serialize_medicine(medicine):
    """One catalogue entry. Clinical and reference fields only.

    `generic_name` and `brand_name` are what a prescriber actually recognises a
    drug by, and `strength` with `dosage_form` is what distinguishes two
    otherwise identically named entries. All are on the medicine already; none
    is billing, and none is stock.
    """
    if not medicine:
        return None
    return {
        "id": medicine.id,
        "name": medicine.name,
        "code": medicine.code or None,
        "generic_name": medicine.generic_name or None,
        "brand_name": medicine.brand_name or None,
        "dosage_form": selection_value(medicine.dosage_form),
        "strength": medicine.strength or None,
        "route": selection_value(medicine.route),
        "category": medicine.category or None,
    }


def serialize_prescribed_medicine(line, dispense_line):
    """One prescribed medicine as its prescriber needs to see it.

    `dispensed_quantity` and `remaining_quantity` ARE clinical facts, not
    operational trivia: "you prescribed 30, the patient received 10" is what a
    prescriber needs to manage the rest of the course, and it is the one place
    medication legitimately needs more than radiology's has_result boolean.
    They are null when correlation is not certain (see correlate_dispense_lines).

    NOT `inventory_consumed_quantity`, which is a stock fact about a shelf, and
    NOT `billing_delivered_quantity`, which is a money fact about a charge. Both
    track the same number in the ordinary case and neither is the clinician's.
    """
    medicine = line.medicine_id
    prescribed = line.quantity or 0.0
    if dispense_line is not None:
        dispensed = dispense_line.dispensed_quantity or 0.0
        remaining = max(prescribed - dispensed, 0.0)
    else:
        dispensed = None
        remaining = None

    return {
        "id": line.id,
        "medicine_id": medicine.id,
        # The LINE's snapshot, falling back to the catalogue. The line carries
        # its own copy -- written when the prescription was issued -- so a
        # medicine renamed next year does not rewrite what was prescribed today.
        "name": line.medicine_name or medicine.name,
        "code": medicine.code or None,
        "dosage_form": selection_value(medicine.dosage_form),
        "strength": medicine.strength or None,
        "dosage": line.dosage or None,
        "route": selection_value(line.route),
        "frequency": line.frequency or None,
        "duration": line.duration or None,
        "quantity": prescribed,
        "instructions": line.instructions or None,
        "dispensed_quantity": dispensed,
        "remaining_quantity": remaining,
    }


def serialize_prescription_diagnosis(diagnosis):
    """The cited indication, if any. Reuses the diagnosis's own disease."""
    if not diagnosis:
        return None
    disease = diagnosis.disease_id
    return {
        "id": diagnosis.id,
        "name": disease.name or diagnosis.display_name,
        "code": disease.code or None,
    }


def serialize_prescription(prescription):
    """One prescription as a clinician needs to see it.

    THE DISPENSE IS READ FOR TWO THINGS ONLY -- its state, and its per-line
    dispensed quantity. Nothing else on it crosses this boundary: not the
    pharmacist, not the encounter, not the charges it will eventually raise, not
    the consumption it will eventually record.

    sudo() ON THE DISPENSE READ, narrowly. A doctor holds a read rule on the
    dispense, but it is scoped, and a visit reassigned mid-episode can leave a
    prescription readable while its dispense is not. Falling back to "no
    progress" in that case would report a dispensed prescription as awaiting
    pharmacy, which is worse than the elevation: the two fields taken are the
    ones this payload already promises, and no wider dispense data is returned.
    """
    dispense = prescription.sudo().pharmacy_dispense_ids[:1]
    dispense = dispense[0] if dispense else None
    status = medication_status(prescription, dispense)
    matched = correlate_dispense_lines(prescription, dispense)

    return {
        "id": prescription.id,
        "prescription_code": prescription.name,
        "medicines": [
            serialize_prescribed_medicine(line, matched.get(line.id))
            for line in prescription.line_ids
        ],
        "diagnosis": serialize_prescription_diagnosis(prescription.diagnosis_id),
        "notes": prescription.notes or None,
        "status": status,
        "status_label": STATUS_LABELS.get(status, "Draft"),
        "ordered_at": date_value(prescription.prescription_date),
        "created_at": datetime_value(prescription.create_date),
        # Whether every prescribed medicine could be matched to its dispense
        # line. False means the numbers above are null for at least one medicine
        # and the client should say so rather than render an empty cell.
        "progress_itemised": bool(dispense) and len(matched) == len(prescription.line_ids),
        # The prescribed set freezes when the prescription leaves draft, and the
        # Doctor Desk confirms on submission -- so a prescription is never
        # editable here. Stated explicitly rather than omitted, so the client is
        # not left inferring it.
        "editable": False,
        "cancellable": prescription.doctor_can_cancel(),
    }


def serialize_prescriptions(prescriptions, can_order):
    """THE medication response shape, for reads and for every mutation.

    Mutations return the WHOLE list rather than the single changed prescription:
    writing one and cancelling one both change what the rest of the list means
    to the prescriber, and a single-row response would leave the desk patching
    an array it cannot fully reason about.
    """
    return {
        "prescriptions": [
            serialize_prescription(prescription) for prescription in prescriptions
        ],
        # The consultation is open, so new prescriptions may be written.
        # Distinct from per-prescription `cancellable`, which is about one
        # record's own workflow.
        "can_order": bool(can_order),
    }
