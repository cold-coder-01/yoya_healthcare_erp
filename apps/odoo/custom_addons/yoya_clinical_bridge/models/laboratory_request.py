"""Consultation linkage for the laboratory request.

WHAT THIS ADDS, AND WHAT IT REFUSES TO REIMPLEMENT
--------------------------------------------------
hospital.laboratory.request already models everything a lab order IS: the
patient, the ordering physician, the appointment, the diagnosis, the priority,
free-text `clinical_notes`, a multi-test `line_ids`, a six-state workflow and
an audit trail. hospital_billing adds `encounter_id` and overrides
action_confirm_request() so confirmation raises one charge per ordered test,
all-or-nothing, after validating every test's billing configuration.

None of that is touched. This module adds exactly two columns -- a consultation
anchor and an idempotency token -- plus the integrity that makes them safe, and
one service method that composes the EXISTING workflow rather than replacing it.

THE ORDERING TRANSITION IS action_confirm_request(), AND IT IS PROVEN, NOT
ASSUMED. Reading both layers:

  hospital_management.action_confirm_request()  draft -> requested
  hospital_billing.action_confirm_request()     super(), then
                                                _ensure_laboratory_billing()

_ensure_laboratory_billing() validates the whole test set BEFORE creating any
charge, resolves the encounter from the appointment (never guessing), asserts
patient/appointment/encounter agreement, opens the billing account and raises
one charge per line. `requested` is therefore the state that means "the doctor
has really ordered these tests", and leaving a request in `draft` would mean no
charge, no clearance conversation and no lab worklist entry -- an order only a
manual Odoo intervention could rescue.

So the Doctor Desk creates the request in draft, attaches its lines, and calls
action_confirm_request(). It creates no charge itself and knows nothing about
billing.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# The doctor's clinical intent, and nothing else. Ownership -- patient,
# encounter, appointment, consultation, physician -- is derived server-side.
LAB_ORDER_EDITABLE_FIELDS = ("priority", "clinical_notes")

LAB_PRIORITIES = ("routine", "urgent", "stat")

# States in which the base model still permits action_cancel(). Restated from
# hospital.laboratory.request._STATE_TRANSITIONS so the API can offer the
# control honestly; the MODEL remains the authority and refuses anything else,
# including a request whose charges have already been delivered.
LAB_CANCELLABLE_STATES = ("draft", "requested")


class HospitalLaboratoryTest(models.Model):
    _inherit = "hospital.laboratory.test"

    @api.model
    def doctor_orderable_domain(self, company=None):
        """Tests a Doctor Desk order can actually complete with. AS A DOMAIN.

        WHY THIS EXISTS. The Doctor Desk confirms on submission, and
        hospital_billing's confirmation override calls
        hospital.laboratory.test._assert_billable(), which refuses the WHOLE
        submission if any selected test is unmapped or misconfigured. Offering
        such a test in the picker therefore offers an action this same workflow
        deterministically refuses -- the doctor picks it, writes an indication,
        presses Place Order and is told no, with nothing to fix on their side.

        THIS IS CATALOGUE ELIGIBILITY, NOT BILLING LOGIC. The conditions below
        are a one-to-one restatement of _assert_billable's four checks:

            unmapped          billing_service_id is set
            archived          the service is active
            wrong company     the service is company-less or this company's
            not effective     today falls inside its effective window

        Nothing is added to that list and nothing is left out -- notably NOT
        service_type, which _assert_billable does not check either (a database
        constraint already guarantees it at write time, and duplicating it here
        would make the picker stricter than the gate it is predicting).

        RETURNED AS A DOMAIN so the catalogue filters in SQL. Post-filtering a
        fetched page would silently shrink it below the limit and make
        `truncated` a lie.

        _assert_billable REMAINS THE AUTHORITY. This only decides what is
        OFFERED; a test that slips through by any other route is still refused
        at confirmation, and the transaction still rolls back.
        """
        company = company or self.env.company
        today = fields.Date.context_today(self)
        return [
            ("billing_service_id", "!=", False),
            ("billing_service_id.active", "=", True),
            "|",
            ("billing_service_id.company_id", "=", False),
            ("billing_service_id.company_id", "=", company.id),
            "|",
            ("billing_service_id.effective_date_start", "=", False),
            ("billing_service_id.effective_date_start", "<=", today),
            "|",
            ("billing_service_id.effective_date_end", "=", False),
            ("billing_service_id.effective_date_end", ">=", today),
        ]


class HospitalLaboratoryRequest(models.Model):
    _inherit = "hospital.laboratory.request"

    consultation_id = fields.Many2one(
        "hospital.consultation",
        string="Consultation",
        index=True,
        ondelete="restrict",
        copy=False,
        help="The physician consultation that ordered this request. Empty for "
        "requests raised outside a consultation, including every historical row "
        "and every walk-in laboratory order.",
    )
    request_token = fields.Char(
        string="Request Token",
        copy=False,
        index=True,
        help="Client-supplied token that makes a retried submission return the "
        "existing request instead of ordering the same tests twice.",
    )

    def init(self):
        """One row per token PER CONSULTATION.

        Scoped rather than global for the reason the diagnosis token index
        documents: the server does not mint these tokens and cannot assume they
        are unique across clients. A global index would let one client's opaque
        string collide with another's across unrelated episodes of care, and the
        matching lookup would hand back ANOTHER PATIENT'S lab request as though
        this submission had created it.

        Partial on both columns, so the many rows with no token and no
        consultation -- every historical request, every walk-in order raised
        from the Odoo form -- do not collide with each other on NULL.
        """
        super().init()
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                hospital_laboratory_request_consultation_token_uniq
            ON hospital_laboratory_request (consultation_id, request_token)
            WHERE consultation_id IS NOT NULL
              AND request_token IS NOT NULL
            """
        )

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------
    @api.constrains(
        "consultation_id", "patient_id", "encounter_id", "appointment_id",
        "physician_id",
    )
    def _check_consultation_references(self):
        """A consultation's lab request cannot belong to anyone else's episode.

        The base model already enforces that every clinical reference shares the
        request's patient. This adds the consultation dimension: patient,
        encounter, appointment and ordering physician must all match the
        consultation the request claims to come from.

        sudo() ON THE CONSULTATION, NARROWLY, for the reason
        _check_clinical_references_patient states in the base model: an
        integrity invariant is a property of the DATA, not of the acting user's
        read rights. A laboratory technician holds no ACL on
        hospital.consultation and must still be able to collect a sample
        without this raising AccessError. It only ever refuses, and never
        returns consultation data to the caller.

        LEGACY AND WALK-IN ROWS ARE SKIPPED. A request with no consultation is
        a perfectly normal shape and stays valid forever.
        """
        for request in self:
            consultation = request.consultation_id
            if not consultation:
                continue
            authoritative = consultation.sudo()

            if request.patient_id != authoritative.patient_id:
                raise ValidationError(
                    "Laboratory request %s is for %s but consultation %s belongs "
                    "to %s. A laboratory order cannot be filed against a "
                    "different patient."
                    % (
                        request.name,
                        request.patient_id.display_name,
                        authoritative.name,
                        authoritative.patient_id.display_name,
                    )
                )

            if request.encounter_id != authoritative.encounter_id:
                # Exact equality, including the missing case. encounter_id is
                # what every charge resolves against, so an unlinked request
                # would bill against nothing -- or, worse, be resolved later to
                # a different episode of care.
                raise ValidationError(
                    "Laboratory request %s cites encounter %s but consultation "
                    "%s documents encounter %s."
                    % (
                        request.name,
                        request.encounter_id.name or "(none)",
                        authoritative.name,
                        authoritative.encounter_id.name or "(none)",
                    )
                )

            if request.appointment_id != authoritative.appointment_id:
                raise ValidationError(
                    "Laboratory request %s cites visit %s but consultation %s "
                    "documents visit %s."
                    % (
                        request.name,
                        request.appointment_id.display_name or "(none)",
                        authoritative.name,
                        authoritative.appointment_id.display_name or "(none)",
                    )
                )

            if (
                authoritative.doctor_id
                and request.physician_id != authoritative.doctor_id
            ):
                raise ValidationError(
                    "Laboratory request %s names %s as the ordering physician, "
                    "but consultation %s is conducted by %s."
                    % (
                        request.name,
                        request.physician_id.display_name,
                        authoritative.name,
                        authoritative.doctor_id.display_name,
                    )
                )

    @api.constrains("consultation_id", "diagnosis_id")
    def _check_diagnosis_belongs_to_consultation(self):
        """A cited diagnosis must come from THIS consultation.

        The base model already refuses a diagnosis belonging to another
        PATIENT. That is not enough here: the same patient can have diagnoses
        from earlier visits, and citing one of those as the indication for a
        test ordered today would attribute the order to a consultation that
        never made it.
        """
        for request in self:
            consultation = request.consultation_id
            diagnosis = request.diagnosis_id
            if not consultation or not diagnosis:
                continue
            if diagnosis.sudo().consultation_id != consultation:
                raise ValidationError(
                    "Diagnosis '%s' was not recorded in consultation %s, so it "
                    "cannot be the indication for a test ordered there."
                    % (diagnosis.display_name, consultation.sudo().name)
                )

    # ------------------------------------------------------------------
    # Doctor Consultation Core service methods
    # ------------------------------------------------------------------
    @api.model
    def _validate_order_values(self, values):
        clean = {}
        if "priority" in values:
            priority = values["priority"]
            if priority in (None, False, ""):
                clean["priority"] = "routine"
            elif priority not in LAB_PRIORITIES:
                raise ValidationError(
                    "'priority' must be one of %s." % ", ".join(LAB_PRIORITIES)
                )
            else:
                clean["priority"] = priority

        if "clinical_notes" in values:
            note = values["clinical_notes"]
            if note in (None, False):
                clean["clinical_notes"] = False
            elif isinstance(note, str):
                clean["clinical_notes"] = note
            else:
                raise ValidationError("'clinical_notes' must be text.")
        return clean

    @api.model
    def create_from_consultation(
        self, consultation, tests, values, diagnosis=None, request_token=None
    ):
        """THE way a lab order is placed from the Doctor Desk.

        Takes RECORDS, never ids, so the caller has already resolved each one
        through their own record rules. Every ownership field is derived from
        the consultation; none is accepted from the client.

        ONE DOCTOR ACTION, ONE AUTHORITATIVE TRANSITION. The request is created
        in draft with its lines and immediately confirmed through
        action_confirm_request(), which is where hospital_billing validates the
        billing configuration of the WHOLE test set and raises one charge per
        line, atomically. Nothing here creates a charge, resolves coverage,
        checks clearance or touches an account.

        IDEMPOTENT ON request_token, scoped to this consultation. A
        double-clicked Place Order, or a retry after a dropped response,
        returns the request the first attempt created -- and therefore does not
        raise a second set of charges. Deliberately NOT keyed on the test set:
        ordering the same test twice at different clinical moments is
        legitimate, and collapsing those would silently merge two real orders.
        """
        consultation.ensure_one()

        if consultation.state != "draft":
            raise UserError(
                "Consultation %s is completed. Laboratory tests can only be "
                "ordered while the consultation is open." % consultation.name
            )
        if not tests:
            raise ValidationError("Select at least one laboratory test to order.")

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

        if not consultation.encounter_id:
            raise UserError(
                "Consultation %s has no encounter, so a laboratory order has no "
                "episode of care to bill against." % consultation.name
            )
        if not consultation.doctor_id:
            raise UserError(
                "Consultation %s has no consulting physician, so a laboratory "
                "order has no requesting doctor." % consultation.name
            )

        if diagnosis and diagnosis.sudo().consultation_id != consultation:
            raise ValidationError(
                "Diagnosis '%s' was not recorded in this consultation."
                % diagnosis.display_name
            )

        clean = self._validate_order_values(values)

        # De-duplicated HERE, not left to the caller. Ordering the same test
        # twice in ONE submission is always a client mistake -- a double-added
        # row in the picker -- and it would raise two charges for one test.
        # Order of first appearance is preserved so the request reads the way
        # the doctor built it.
        unique_tests = []
        for test in tests:
            if test not in unique_tests:
                unique_tests.append(test)

        # Ownership, derived. The browser decides none of this.
        vals = {
            "patient_id": consultation.patient_id.id,
            "physician_id": consultation.doctor_id.id,
            "appointment_id": consultation.appointment_id.id or False,
            "encounter_id": consultation.encounter_id.id,
            "consultation_id": consultation.id,
            "diagnosis_id": diagnosis.id if diagnosis else False,
            "request_token": request_token or False,
            "line_ids": [
                (0, 0, {"test_id": test.id, "sequence": (index + 1) * 10})
                for index, test in enumerate(unique_tests)
            ],
        }
        vals.update(clean)

        request = self.create(vals)
        # THE authoritative transition. hospital_billing's override raises the
        # charges; this module does not and must not.
        request.action_confirm_request()
        return request

    def cancel_from_consultation(self):
        """Cancel through the model's own workflow. Nothing bespoke.

        action_cancel() runs the base transition guard -- which permits
        cancellation only from draft/requested and refuses a request carrying a
        validated or released result -- and then hospital_billing's override
        cancels the operational charges, refusing outright if any has already
        been delivered. All of that is exactly the behaviour required here, so
        none of it is reimplemented or second-guessed.
        """
        self.ensure_one()
        if not self.consultation_id:
            raise UserError(
                "This laboratory request was not ordered from a consultation "
                "and cannot be cancelled from the Doctor Desk."
            )
        if self.consultation_id.sudo().state != "draft":
            raise UserError(
                "Consultation %s is completed and its laboratory orders are "
                "locked." % self.consultation_id.sudo().name
            )
        self.action_cancel()
        return self

    def doctor_can_cancel(self):
        """Affordance only. action_cancel() decides the real answer."""
        self.ensure_one()
        if self.state not in LAB_CANCELLABLE_STATES:
            return False
        if not self.consultation_id or self.consultation_id.sudo().state != "draft":
            return False
        # A delivered charge makes cancellation impossible however early the
        # clinical state looks, so the desk should not offer the control.
        return not any(
            charge.qty_delivered > 0
            or charge.delivery_state in ("delivered", "partially_delivered")
            for charge in self.sudo().charge_line_ids
        )

    @api.model
    def for_consultation(self, consultation):
        """Every lab order of one consultation, newest first. Pure read."""
        if not consultation:
            return self.browse()
        return self.search(
            [("consultation_id", "=", consultation.id)],
            order="id desc",
        )
