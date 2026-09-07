"""Consultation linkage for the prescription.

WHAT THIS ADDS, AND WHAT IT REFUSES TO REIMPLEMENT
--------------------------------------------------
hospital.prescription already models everything a medication order IS: the
patient, the prescribing physician, the appointment, the diagnosis, a
multi-medicine `line_ids`, a four-state workflow and an audit trail.
hospital_pharmacy overrides action_confirm() so confirmation composes exactly
one linked hospital.pharmacy.dispense in draft, and action_cancel() so
withdrawing a prescription withdraws that dispense. hospital_billing raises the
medication charges later, at Mark Ready, and hospital_inventory consumes stock
later still, at Validate Dispense.

None of that is touched. This module adds exactly two columns -- a consultation
anchor and an idempotency token -- plus the integrity that makes them safe, and
service methods that COMPOSE the existing workflow rather than replacing it. It
is the laboratory and radiology bridges' shape, applied to the third ancillary
service a doctor orders the same way.

THE ORDERING TRANSITION IS action_confirm(), AND IT IS PROVEN, NOT ASSUMED.
Reading the layers:

  hospital_management.action_confirm()   draft -> confirmed
  hospital_pharmacy.action_confirm()     validates the dispense vals FIRST,
                                         then super(), then composes exactly
                                         one hospital.pharmacy.dispense in
                                         draft with one line per catalogue
                                         medicine

WHERE PHARMACY GENUINELY DIFFERS FROM LABORATORY AND RADIOLOGY, AND WHY IT
CHANGES WHAT THIS FILE PROMISES.

Confirmation here raises NO charge. Laboratory and radiology both bill at
confirmation, so for them the doctor's submission is the financial event. For
medication the financial event is the PHARMACIST's Mark Ready, using the
quantity the pharmacist intends to hand over. A prescription is therefore
clinically authoritative and financially inert, and this module must not
pretend otherwise: nothing below creates a charge, resolves an encounter,
checks clearance, touches an account or moves stock.

The consequence for the desk is that a prescription raises no cashier signal
until pharmacy acts on it. That is the workflow, not a defect, and the status
vocabulary in the serializer says so honestly.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

# The clinical fields a doctor may set on one prescribed medicine. Ownership --
# patient, appointment, physician, consultation -- is derived server-side and
# never accepted from the client.
#
# These are hospital.prescription.line's OWN field names, used unchanged. The
# model calls its free-text field `instructions` where radiology calls its
# `special_instruction`; renaming either to match the other would put a
# translation layer between the API and the model, and translation layers are
# where field drift starts.
MEDICATION_LINE_FIELDS = (
    "dosage",
    "route",
    "frequency",
    "duration",
    "instructions",
)

# States in which the base model still permits action_cancel(). Restated from
# hospital.prescription.action_cancel() so the API can offer the control
# honestly; the MODEL remains the authority and refuses anything else.
PRESCRIPTION_CANCELLABLE_STATES = ("draft", "confirmed")

# Dispense states that mean the Doctor Desk must not offer cancellation, because
# Slice 6A's hardened guards will refuse it. Restated from hospital_pharmacy's
# PRESCRIPTION_BLOCKING_DISPENSE_STATES for the reason every restatement in this
# module carries: importing it would be fine, but the affordance and the guard
# are allowed to disagree ONLY in the safe direction, and a test asserts they
# do not disagree at all.
DISPENSE_BLOCKS_DOCTOR_CANCEL = ("partial", "dispensed")

# Quantity below which a prescribed amount is not a prescription. Matches the
# tolerance hospital_billing uses when it decides whether a dispense line has a
# quantity worth billing, so a line this module accepts is never one the next
# step silently ignores.
QTY_TOLERANCE = 0.0005


class HospitalPharmacyMedicine(models.Model):
    _inherit = "hospital.pharmacy.medicine"

    @api.model
    def doctor_orderable_domain(self, company=None):
        """Medicines a Doctor Desk prescription can actually complete with.

        WHY THIS EXISTS, AND WHY IT IS STRICTER THAN LABORATORY'S OR
        RADIOLOGY'S. Those two predict a single downstream gate: billing
        configuration, checked at confirmation. Medication has to predict TWO
        gates, at two different moments, run by two different people:

            Mark Ready       hospital_billing._assert_billable() refuses the
                             whole dispense if any medicine is unmapped or
                             misconfigured
            Validate Dispense hospital_inventory._inventory_increment_lines()
                             refuses if any dispensed medicine has no valid
                             inventory item

        The second one is why this matters more than a tidy picker. A medicine
        that is billable but not inventory-mapped gets prescribed, marked ready,
        charged, AND PAID FOR, and only then fails at consumption. The patient's
        money is taken before anything refuses. Offering such a medicine is not
        a trap, it is a way to take money for medication that cannot be handed
        over.

        COMPOSED FROM THE TWO OWNING MODULES, NEVER RESTATED HERE. The billing
        conditions come from hospital_billing's own
        _doctor_orderable_billing_domain(), which sits beside _assert_billable()
        in the same file; the inventory conditions come from hospital_inventory's
        _doctor_orderable_inventory_domain(), which is mechanically derived from
        _pharmacy_inventory_item_domain(). This method contributes exactly one
        condition of its own -- `active` -- and otherwise only ANDs.

        LIVE STOCK IS DELIBERATELY NOT A CONDITION, and this is the one place
        this domain departs from "what will the next step accept". Prescribing
        is a clinical act about what the patient needs; dispensing is a supply
        act about what is on the shelf. Nothing reserves stock at prescribing,
        partial dispensing is the routine outcome when supply is short, and
        stock moves between the consultation and the counter anyway. A domain
        that hid a correctly configured medicine because the shelf happened to
        be empty this minute would make the picker lie about what the hospital
        stocks, and would silently push the doctor toward whatever was in stock
        rather than whatever was indicated.

        So: properly configured medicine, out of stock -> still offered. The
        pharmacist reconciles it to supply, and partial dispensing exists for
        exactly that.

        RETURNED AS A DOMAIN so the catalogue filters in SQL. Post-filtering a
        fetched page would shrink it below the requested limit and make
        `truncated` a lie.

        DELIBERATELY NOT A REPAIR. It hides misconfigured medicines; it does not
        map them, does not archive them and does not create the billing service
        or inventory item they lack. Mapping a medicine to a billing service is
        a pricing decision with a real tariff behind it, and mapping it to an
        inventory item is a stock-control decision; a module that guessed either
        would be inventing what a drug costs or what shelf it lives on.
        """
        domain = [("active", "=", True)]
        # Each fragment is asked of the model, so an installation without one of
        # the modules simply does not contribute that half rather than crashing.
        # Both are present in every deployment this desk runs in; the guard is
        # about honest layering, not about pretending they might be missing.
        if hasattr(self, "_doctor_orderable_billing_domain"):
            domain += self._doctor_orderable_billing_domain(company=company)
        if hasattr(self, "_doctor_orderable_inventory_domain"):
            # NOTE the asymmetry, which is intentional. The billing fragment
            # takes an explicit company because _assert_billable() does; the
            # inventory fragment reads allowed_company_ids/env.company because
            # _is_valid_pharmacy_inventory_item() does. Each half uses its own
            # authority's company rule rather than a third one invented here.
            domain += self._doctor_orderable_inventory_domain()
        return domain


class HospitalPrescription(models.Model):
    _inherit = "hospital.prescription"

    consultation_id = fields.Many2one(
        "hospital.consultation",
        string="Consultation",
        index=True,
        ondelete="restrict",
        copy=False,
        help="The physician consultation that wrote this prescription. Empty "
        "for prescriptions raised outside a consultation, including every "
        "historical row and every prescription entered at the pharmacy.",
    )
    request_token = fields.Char(
        string="Request Token",
        copy=False,
        index=True,
        help="Client-supplied token that makes a retried submission return the "
        "existing prescription instead of prescribing the same medicines twice.",
    )

    def init(self):
        """One row per token PER CONSULTATION.

        Scoped rather than global for the reason the laboratory, radiology and
        diagnosis token indexes document: the server does not mint these tokens
        and cannot assume they are unique across clients. A global index would
        let one client's opaque string collide with another's across unrelated
        episodes of care, and the matching lookup would hand back ANOTHER
        PATIENT'S prescription as though this submission had created it.

        Partial on both columns, so the many rows with no token and no
        consultation -- every historical prescription, every one entered at the
        pharmacy counter -- do not collide with each other on NULL.
        """
        super().init()
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                hospital_prescription_consultation_token_uniq
            ON hospital_prescription (consultation_id, request_token)
            WHERE consultation_id IS NOT NULL
              AND request_token IS NOT NULL
            """
        )

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------
    @api.constrains(
        "consultation_id", "patient_id", "appointment_id", "physician_id"
    )
    def _check_consultation_references(self):
        """A consultation's prescription cannot belong to anyone else's episode.

        hospital.prescription ships onchanges that copy the patient and
        physician from a chosen appointment or diagnosis, but NO constraint that
        keeps them together, so nothing stops a programmatic write from filing a
        prescription for one patient against another's visit. For a
        consultation-linked prescription this is the only structural check there
        is that the patient, the visit and the prescriber all describe one
        episode of care.

        ENCOUNTER IS ABSENT FROM THIS LIST BECAUSE IT IS ABSENT FROM THE MODEL.
        hospital.prescription has no encounter_id and must not grow one: the
        encounter is resolved onto the DISPENSE by hospital_billing's
        _prepare_pharmacy_dispense_vals(), and a second copy on the header would
        be a second place for one fact to drift.

        sudo() ON THE CONSULTATION, NARROWLY, for the reason the radiology
        bridge states: an integrity invariant is a property of the DATA, not of
        the acting user's read rights. A PHARMACIST holds an explicit
        [(0,'=',1)] rule on hospital.consultation and must still be able to mark
        a dispense ready without this raising AccessError. It only ever refuses,
        and never returns consultation data to the caller.

        LEGACY AND PHARMACY-RAISED ROWS ARE SKIPPED. A prescription with no
        consultation is a perfectly normal shape and stays valid forever.
        """
        for prescription in self:
            consultation = prescription.consultation_id
            if not consultation:
                continue
            authoritative = consultation.sudo()

            if prescription.patient_id != authoritative.patient_id:
                raise ValidationError(
                    "Prescription %s is for %s but consultation %s belongs to "
                    "%s. A medication order cannot be filed against a different "
                    "patient."
                    % (
                        prescription.name,
                        prescription.patient_id.display_name,
                        authoritative.name,
                        authoritative.patient_id.display_name,
                    )
                )

            if prescription.appointment_id != authoritative.appointment_id:
                raise ValidationError(
                    "Prescription %s cites visit %s but consultation %s "
                    "documents visit %s."
                    % (
                        prescription.name,
                        prescription.appointment_id.display_name or "(none)",
                        authoritative.name,
                        authoritative.appointment_id.display_name or "(none)",
                    )
                )

            if (
                authoritative.doctor_id
                and prescription.physician_id != authoritative.doctor_id
            ):
                raise ValidationError(
                    "Prescription %s names %s as the prescriber, but "
                    "consultation %s is conducted by %s."
                    % (
                        prescription.name,
                        prescription.physician_id.display_name,
                        authoritative.name,
                        authoritative.doctor_id.display_name,
                    )
                )

    @api.constrains("consultation_id", "diagnosis_id")
    def _check_diagnosis_belongs_to_consultation(self):
        """A cited diagnosis must come from THIS consultation.

        hospital.prescription has an onchange that copies the patient from a
        chosen diagnosis, but no constraint keeping them together, so nothing
        stops a diagnosis from another patient -- let alone the same patient's
        earlier visit -- being written programmatically. Citing one of those as
        the indication for medication prescribed today would attribute the
        prescription to a consultation that never wrote it.
        """
        for prescription in self:
            consultation = prescription.consultation_id
            diagnosis = prescription.diagnosis_id
            if not consultation or not diagnosis:
                continue
            if diagnosis.sudo().consultation_id != consultation:
                raise ValidationError(
                    "Diagnosis '%s' was not recorded in consultation %s, so it "
                    "cannot be the indication for medication prescribed there."
                    % (diagnosis.display_name, consultation.sudo().name)
                )

    # ------------------------------------------------------------------
    # Doctor Consultation Core service methods
    # ------------------------------------------------------------------
    @api.model
    def _validate_medicine_entry(self, entry, medicine):
        """One requested medicine, validated and turned into line values.

        QUANTITY IS REQUIRED HERE EVEN THOUGH THE COLUMN IS NOT.
        hospital.prescription.line.quantity is a plain Float with no
        required=True and no positivity constraint, so the schema would happily
        store a zero. The downstream workflow would not: hospital_billing's
        _ensure_pharmacy_billing() selects only lines whose quantity exceeds its
        tolerance and refuses the whole dispense when none do, so a zero-quantity
        prescription reaches pharmacy as an order that can never be marked
        ready. Refusing it at the door is the only place the doctor can still
        fix it.

        NOT DERIVED FROM FREQUENCY x DURATION, deliberately. Both are free-text
        Char columns on this model -- "twice daily", "1/7", "prn" -- and
        multiplying parsed guesses would produce a dispensed quantity nobody
        authorised. The prescriber states the quantity.
        """
        quantity = entry.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise ValidationError(
                "'quantity' must be a number for %s." % medicine.display_name
            )
        quantity = float(quantity)
        if float_compare(quantity, QTY_TOLERANCE, precision_digits=4) < 0:
            raise ValidationError(
                "'quantity' must be greater than zero for %s. The pharmacy "
                "cannot dispense against a prescription with no quantity."
                % medicine.display_name
            )

        vals = {"quantity": quantity}
        for key in MEDICATION_LINE_FIELDS:
            if key not in entry:
                continue
            value = entry[key]
            if value in (None, False, ""):
                vals[key] = False
            elif isinstance(value, str):
                vals[key] = value
            else:
                raise ValidationError(
                    "'%s' must be text for %s." % (key, medicine.display_name)
                )

        route = vals.get("route")
        if route:
            allowed = dict(self.env["hospital.prescription.line"]._fields["route"].selection)
            if route not in allowed:
                raise ValidationError(
                    "'route' must be one of %s." % ", ".join(sorted(allowed))
                )
        return vals

    @api.model
    def _prepare_prescription_line(self, medicine, entry, sequence):
        """One prescription line, seeded from the catalogue as the form does.

        WITHOUT THIS THE LINE WOULD NOT SAVE AT ALL. medicine_name is
        required=True on hospital.prescription.line, and the only thing that
        normally fills it is _onchange_medicine_id() -- which does not run for a
        programmatic create. A Doctor Desk order would fail on a NOT NULL
        violation, or, worse, on a database that still tolerates NULLs, would
        write a nameless line into the pharmacy queue.

        The same onchange seeds `dosage` from the catalogue strength and
        translates the catalogue route through _PHARMACY_ROUTE_MAP, and those
        are reproduced here for the same reason: an order placed from the desk
        should reach the counter carrying what the identical order typed into
        the Odoo form would carry.

        SNAPSHOT, NOT RELATED-THROUGH. medicine_name and dosage are writable
        columns on the line, so they keep what the catalogue said ON THE DAY IT
        WAS PRESCRIBED. That is the shape the base model chose, and a
        prescription is a legal record of what was ordered, not a live view of
        what the catalogue says today.

        THE DOCTOR'S OWN VALUES ALWAYS WIN. Seeding only fills what was left
        empty; anything the prescriber typed is passed through untouched.
        """
        Line = self.env["hospital.prescription.line"]
        vals = {
            "medicine_id": medicine.id,
            # The snapshot. Never left to the onchange that will not run.
            "medicine_name": medicine.name,
            "sequence": sequence,
        }
        vals.update(self._validate_medicine_entry(entry, medicine))

        if not vals.get("dosage") and medicine.strength:
            vals["dosage"] = medicine.strength

        if not vals.get("route") and medicine.route:
            # THE AUTHORITATIVE MAP, NOT A GUESS. The catalogue's route
            # selection and the line's are different vocabularies -- the
            # catalogue distinguishes iv/im/subcutaneous where the line says
            # `injection`, and the line has `nasal` where the catalogue has
            # nothing. hospital_pharmacy owns the translation table; where it
            # offers no safe mapping the route is simply left empty rather than
            # invented, because a wrong route on a prescription is a clinical
            # error, not a cosmetic one.
            mapped = Line._PHARMACY_ROUTE_MAP.get(medicine.route)
            if mapped:
                vals["route"] = mapped
        return vals

    @api.model
    def create_from_consultation(
        self, consultation, entries, values=None, diagnosis=None, request_token=None
    ):
        """THE way a prescription is written from the Doctor Desk.

        Takes a consultation RECORD and a list of plain medicine entries, so the
        caller has already resolved the consultation through their own record
        rules. Every ownership field is derived from the consultation; none is
        accepted from the client.

        ONE DOCTOR ACTION, ONE PRESCRIPTION, ONE AUTHORITATIVE TRANSITION. All
        the selected medicines become LINES OF A SINGLE prescription, which is
        what the domain models: hospital.prescription owns a one2many of lines
        and hospital.pharmacy.dispense carries unique(prescription_id), so one
        submission produces one prescription and exactly one dispense. Splitting
        a five-drug prescription into five prescriptions would produce five
        dispenses, five trips to the counter and five charges to clear.

        The prescription is created in draft with its lines and immediately
        confirmed through action_confirm(), which is where hospital_pharmacy
        composes the linked dispense. Nothing here creates that dispense, and
        nothing here creates a charge, resolves an encounter, checks clearance
        or touches stock -- because at this point in the workflow none of those
        exist yet.

        IDEMPOTENT ON request_token, scoped to this consultation. A
        double-clicked Prescribe, or a retry after a dropped response, returns
        the prescription the first attempt created -- and therefore does not
        create a second dispense. Deliberately NOT keyed on the medicine set:
        prescribing the same drug twice at different clinical moments is
        legitimate, and collapsing those would silently merge two real orders.
        """
        consultation.ensure_one()

        if consultation.state != "draft":
            raise UserError(
                "Consultation %s is completed. Medication can only be "
                "prescribed while the consultation is open." % consultation.name
            )
        if not entries:
            raise ValidationError("Select at least one medicine to prescribe.")

        if request_token:
            existing = self.search(
                [
                    ("consultation_id", "=", consultation.id),
                    ("request_token", "=", request_token),
                ],
                limit=1,
            )
            if existing:
                return existing

        if not consultation.doctor_id:
            raise UserError(
                "Consultation %s has no consulting physician, so a prescription "
                "has no prescriber." % consultation.name
            )
        if not consultation.appointment_id:
            # hospital_pharmacy's _prepare_pharmacy_dispense_vals() asserts the
            # appointment's patient matches, and hospital_billing resolves the
            # encounter from that appointment. A consultation always has a
            # visit, so this is unreachable in practice; it is stated so the
            # failure, if the invariant ever breaks, names the missing visit
            # instead of surfacing as a dispense error at confirmation.
            raise UserError(
                "Consultation %s has no visit, so a prescription has no "
                "appointment to bill against." % consultation.name
            )

        if diagnosis and diagnosis.sudo().consultation_id != consultation:
            raise ValidationError(
                "Diagnosis '%s' was not recorded in this consultation."
                % diagnosis.display_name
            )

        # ORDERABILITY IS RE-CHECKED SERVER-SIDE, against the same domain the
        # picker was filtered by. The picker is an affordance; a client that
        # posts an id it was never offered -- a stale tab, a scripted call --
        # must not be able to place an order the pharmacist cannot fill and the
        # patient has already paid for.
        medicine_ids = [entry["medicine_id"] for entry in entries]
        Medicine = self.env["hospital.pharmacy.medicine"]
        orderable = Medicine.search(
            [("id", "in", medicine_ids)] + Medicine.doctor_orderable_domain()
        )
        missing = set(medicine_ids) - set(orderable.ids)
        if missing:
            refused = Medicine.browse(sorted(missing)).exists()
            raise ValidationError(
                "These medicines cannot be prescribed because the pharmacy "
                "workflow cannot process them: %s. They are missing a billing "
                "service or an inventory mapping."
                % (", ".join(refused.mapped("display_name")) or "unknown medicine")
            )

        by_id = {medicine.id: medicine for medicine in orderable}
        line_vals = [
            (
                0,
                0,
                self._prepare_prescription_line(
                    by_id[entry["medicine_id"]], entry, (index + 1) * 10
                ),
            )
            for index, entry in enumerate(entries)
        ]

        clean = self._validate_header_values(values or {})

        # Ownership, derived. The browser decides none of this.
        vals = {
            "patient_id": consultation.patient_id.id,
            "physician_id": consultation.doctor_id.id,
            "appointment_id": consultation.appointment_id.id,
            "consultation_id": consultation.id,
            "diagnosis_id": diagnosis.id if diagnosis else False,
            "request_token": request_token or False,
            "line_ids": line_vals,
        }
        vals.update(clean)

        prescription = self.create(vals)
        # THE authoritative transition. hospital_pharmacy's override composes
        # the linked draft dispense; this module does not and must not.
        prescription.action_confirm()
        return prescription

    @api.model
    def _validate_header_values(self, values):
        clean = {}
        if "notes" in values:
            notes = values["notes"]
            if notes in (None, False):
                clean["notes"] = False
            elif isinstance(notes, str):
                clean["notes"] = notes
            else:
                raise ValidationError("'notes' must be text.")
        return clean

    def cancel_from_consultation(self):
        """Cancel through the model's own workflow. Nothing bespoke.

        action_cancel() runs the base transition guard and then Slice 6A's
        hardened hospital_pharmacy override, which refuses outright when the
        linked dispense is partial or dispensed, cancels the dispense when it is
        draft or ready, and lets hospital_billing cancel the medication charges
        on the way through. All of that is exactly the behaviour required here,
        so none of it is reimplemented or second-guessed -- and its refusal
        reaches the doctor with its own wording, because that sentence is the
        only thing that says WHY.
        """
        self.ensure_one()
        if not self.consultation_id:
            raise UserError(
                "This prescription was not written in a consultation and "
                "cannot be cancelled from the Doctor Desk."
            )
        if self.consultation_id.sudo().state != "draft":
            raise UserError(
                "Consultation %s is completed and its prescriptions are locked."
                % self.consultation_id.sudo().name
            )
        self.action_cancel()
        return self

    def doctor_can_cancel(self):
        """Affordance only. action_cancel() decides the real answer.

        The completed-consultation clause is the Slice 4 policy, applied the way
        laboratory and radiology apply it: once the consultation is signed the
        DOCTOR's cancellation control goes away, while pharmacy keeps whatever
        its own workflow still permits. The order freezes for the desk, not for
        the hospital.

        The dispense clause predicts Slice 6A's guard rather than restating its
        reasoning. A partial or dispensed record has medication in the patient's
        hand, and offering a control that the model will certainly refuse is
        the picker-trap problem moved to a button.
        """
        self.ensure_one()
        if self.state not in PRESCRIPTION_CANCELLABLE_STATES:
            return False
        if not self.consultation_id or self.consultation_id.sudo().state != "draft":
            return False
        dispenses = self.sudo().pharmacy_dispense_ids
        if any(
            dispense.state in DISPENSE_BLOCKS_DOCTOR_CANCEL for dispense in dispenses
        ):
            return False
        return True

    @api.model
    def for_consultation(self, consultation):
        """Every prescription of one consultation, newest first. Pure read."""
        if not consultation:
            return self.browse()
        return self.search(
            [("consultation_id", "=", consultation.id)],
            order="id desc",
        )
