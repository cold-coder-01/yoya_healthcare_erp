"""Consultation linkage for the radiology request.

WHAT THIS ADDS, AND WHAT IT REFUSES TO REIMPLEMENT
--------------------------------------------------
hospital.radiology.request already models everything an imaging order IS: the
patient, the ordering physician, the appointment, the diagnosis, the priority, a
free-text `clinical_indication` and `instructions`, a multi-exam `line_ids`, a
six-state workflow and an audit trail. hospital_billing adds `encounter_id` and
overrides action_confirm_request() so confirmation raises one charge per ordered
examination, all-or-nothing, after validating every exam's billing
configuration.

None of that is touched. This module adds exactly two columns -- a consultation
anchor and an idempotency token -- plus the integrity that makes them safe, and
one service method that composes the EXISTING workflow rather than replacing it.
It is the laboratory bridge's shape, applied to the model next door, because two
ancillary services that a doctor orders the same way should not be ordered two
different ways underneath.

THE ORDERING TRANSITION IS action_confirm_request(), AND IT IS PROVEN, NOT
ASSUMED. Reading both layers:

  hospital_radiology.action_confirm_request()   draft -> requested
  hospital_billing.action_confirm_request()     super(), then
                                                _ensure_radiology_billing()

_ensure_radiology_billing() validates the whole exam set BEFORE creating any
charge, resolves the encounter from the appointment (never guessing), asserts
patient/appointment/encounter agreement, opens the billing account and raises
one charge per line. `requested` is therefore the state that means "the doctor
has really ordered these studies", and leaving a request in `draft` would mean
no charge, no clearance conversation and no imaging worklist entry -- an order
only a manual Odoo intervention could rescue.

WHERE RADIOLOGY GENUINELY DIFFERS FROM LABORATORY, AND WHY IT DOES NOT CHANGE
THIS FILE'S SHAPE. Radiology has a `scheduled` state between `requested` and
`in_progress`, and its clearance gate is action_mark_in_progress() rather than
sample collection. Both belong to the imaging department, not to the ordering
doctor: the desk places the order and stops. Scheduling is somebody else's act,
so nothing here performs it, and doctor_can_cancel() below simply follows the
base model's own transition guard, which permits cancellation through
`scheduled` and refuses everything after it.

So the Doctor Desk creates the request in draft, attaches its lines, and calls
action_confirm_request(). It creates no charge and knows nothing about billing.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# The doctor's clinical intent, and nothing else. Ownership -- patient,
# encounter, appointment, consultation, physician -- is derived server-side.
#
# `clinical_indication` and `instructions` are the radiology model's own field
# names and are used unchanged. Laboratory calls its single free-text field
# `clinical_notes`; renaming either to match the other would put a translation
# layer between the API and the model for no gain, and translation layers are
# where field drift starts.
RAD_ORDER_EDITABLE_FIELDS = ("priority", "clinical_indication", "instructions")

RAD_PRIORITIES = ("routine", "urgent", "stat")

# States in which the base model still permits action_cancel(). Restated from
# hospital.radiology.request.action_cancel() so the API can offer the control
# honestly; the MODEL remains the authority and refuses anything else.
#
# Includes `scheduled`, which laboratory has no equivalent of. A scheduled
# request has charges but no imaging, so cancelling it is legitimate -- and
# hospital_billing's action_cancel() override cancels those charges rather than
# leaving them payable for a study that will never happen.
RAD_CANCELLABLE_STATES = ("draft", "requested", "scheduled")


class HospitalRadiologyExam(models.Model):
    _inherit = "hospital.radiology.exam"

    @api.model
    def doctor_orderable_domain(self, company=None):
        """Exams a Doctor Desk order can actually complete with. AS A DOMAIN.

        WHY THIS EXISTS, AND WHY IT IS NOT OPTIONAL HERE. The Doctor Desk
        confirms on submission, and hospital_billing's confirmation override
        calls hospital.radiology.exam._assert_billable(), which refuses the
        WHOLE submission if any selected exam is unmapped or misconfigured.
        Offering such an exam in the picker therefore offers an action this same
        workflow deterministically refuses -- the doctor picks it, writes an
        indication, presses Place Order and is told no, with nothing to fix on
        their side.

        That is not hypothetical for radiology the way it was for laboratory. On
        the UAT database exactly one of six active exams carries a billing
        service, so without this domain five of the six entries in the picker
        would be traps.

        THIS IS CATALOGUE ELIGIBILITY, NOT BILLING LOGIC. The conditions below
        are a one-to-one restatement of _assert_billable's four checks:

            unmapped          billing_service_id is set
            archived          the service is active
            wrong company     the service is company-less or this company's
            not effective     today falls inside its effective window

        Nothing is added to that list and nothing is left out -- notably NOT
        service_type, which _assert_billable does not check either (a model
        constraint already guarantees it at write time, and duplicating it here
        would make the picker stricter than the gate it is predicting).

        RETURNED AS A DOMAIN so the catalogue filters in SQL. Post-filtering a
        fetched page would silently shrink it below the limit and make
        `truncated` a lie.

        _assert_billable REMAINS THE AUTHORITY. This only decides what is
        OFFERED; an exam that slips through by any other route is still refused
        at confirmation, and the transaction still rolls back.

        DELIBERATELY NOT A REPAIR. It hides misconfigured exams; it does not map
        them, and it does not archive them. Mapping an exam to a billing service
        is a pricing decision with a real tariff behind it, and a module that
        guessed one would be inventing what a scan costs.
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


class HospitalRadiologyRequest(models.Model):
    _inherit = "hospital.radiology.request"

    consultation_id = fields.Many2one(
        "hospital.consultation",
        string="Consultation",
        index=True,
        ondelete="restrict",
        copy=False,
        help="The physician consultation that ordered this request. Empty for "
        "requests raised outside a consultation, including every historical row "
        "and every imaging order raised at the department.",
    )
    request_token = fields.Char(
        string="Request Token",
        copy=False,
        index=True,
        help="Client-supplied token that makes a retried submission return the "
        "existing request instead of ordering the same studies twice.",
    )

    def init(self):
        """One row per token PER CONSULTATION.

        Scoped rather than global for the reason the laboratory and diagnosis
        token indexes document: the server does not mint these tokens and cannot
        assume they are unique across clients. A global index would let one
        client's opaque string collide with another's across unrelated episodes
        of care, and the matching lookup would hand back ANOTHER PATIENT'S
        radiology request as though this submission had created it.

        Partial on both columns, so the many rows with no token and no
        consultation -- every historical request, every order raised from the
        Odoo form at the imaging department -- do not collide with each other on
        NULL.
        """
        super().init()
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                hospital_radiology_request_consultation_token_uniq
            ON hospital_radiology_request (consultation_id, request_token)
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
        """A consultation's radiology request cannot belong to anyone else's
        episode.

        THIS CARRIES MORE WEIGHT HERE THAN IT DOES ON THE LABORATORY REQUEST.
        hospital.laboratory.request ships a base constraint asserting that every
        clinical reference shares the request's patient; hospital.radiology.
        request ships no such constraint at all. So this is not merely adding
        the consultation dimension to an existing guarantee -- for a
        consultation-linked request it is the only structural check there is
        that the patient, the encounter, the visit and the ordering physician
        all describe one episode of care.

        sudo() ON THE CONSULTATION, NARROWLY, for the reason the laboratory
        bridge states: an integrity invariant is a property of the DATA, not of
        the acting user's read rights. A radiographer holds no ACL on
        hospital.consultation and must still be able to schedule a study without
        this raising AccessError. It only ever refuses, and never returns
        consultation data to the caller.

        LEGACY AND DEPARTMENT-RAISED ROWS ARE SKIPPED. A request with no
        consultation is a perfectly normal shape and stays valid forever.
        """
        for request in self:
            consultation = request.consultation_id
            if not consultation:
                continue
            authoritative = consultation.sudo()

            if request.patient_id != authoritative.patient_id:
                raise ValidationError(
                    "Radiology request %s is for %s but consultation %s belongs "
                    "to %s. An imaging order cannot be filed against a "
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
                    "Radiology request %s cites encounter %s but consultation "
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
                    "Radiology request %s cites visit %s but consultation %s "
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
                    "Radiology request %s names %s as the ordering physician, "
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

        hospital.radiology.request has an onchange that copies the patient from
        a chosen diagnosis, but no constraint that keeps them together, so
        nothing stops a diagnosis from another patient -- let alone from the
        same patient's earlier visit -- being written programmatically. Citing
        one of those as the indication for a study ordered today would attribute
        the order to a consultation that never made it.
        """
        for request in self:
            consultation = request.consultation_id
            diagnosis = request.diagnosis_id
            if not consultation or not diagnosis:
                continue
            if diagnosis.sudo().consultation_id != consultation:
                raise ValidationError(
                    "Diagnosis '%s' was not recorded in consultation %s, so it "
                    "cannot be the indication for a study ordered there."
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
            elif priority not in RAD_PRIORITIES:
                raise ValidationError(
                    "'priority' must be one of %s." % ", ".join(RAD_PRIORITIES)
                )
            else:
                clean["priority"] = priority

        for key in ("clinical_indication", "instructions"):
            if key not in values:
                continue
            text = values[key]
            if text in (None, False):
                clean[key] = False
            elif isinstance(text, str):
                clean[key] = text
            else:
                raise ValidationError("'%s' must be text." % key)
        return clean

    @api.model
    def create_from_consultation(
        self, consultation, exams, values, diagnosis=None, request_token=None
    ):
        """THE way a radiology order is placed from the Doctor Desk.

        Takes RECORDS, never ids, so the caller has already resolved each one
        through their own record rules. Every ownership field is derived from
        the consultation; none is accepted from the client.

        ONE DOCTOR ACTION, ONE AUTHORITATIVE TRANSITION. The request is created
        in draft with its lines and immediately confirmed through
        action_confirm_request(), which is where hospital_billing validates the
        billing configuration of the WHOLE exam set and raises one charge per
        line, atomically. Nothing here creates a charge, resolves coverage,
        checks clearance or touches an account.

        IDEMPOTENT ON request_token, scoped to this consultation. A
        double-clicked Place Order, or a retry after a dropped response,
        returns the request the first attempt created -- and therefore does not
        raise a second set of charges. Deliberately NOT keyed on the exam set:
        ordering the same study twice at different clinical moments is
        legitimate, and collapsing those would silently merge two real orders.
        """
        consultation.ensure_one()

        if consultation.state != "draft":
            raise UserError(
                "Consultation %s is completed. Radiology studies can only be "
                "ordered while the consultation is open." % consultation.name
            )
        if not exams:
            raise ValidationError("Select at least one radiology exam to order.")

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
                "Consultation %s has no encounter, so a radiology order has no "
                "episode of care to bill against." % consultation.name
            )
        if not consultation.doctor_id:
            raise UserError(
                "Consultation %s has no consulting physician, so a radiology "
                "order has no requesting doctor." % consultation.name
            )
        if not consultation.appointment_id:
            # RADIOLOGY IS STRICTER THAN LABORATORY HERE, AND THE MODEL IS WHY.
            # hospital_billing._resolve_encounter() falls back to the
            # appointment when encounter_id is empty and raises otherwise;
            # laboratory additionally offers action_establish_standalone_
            # encounter() for a walk-in, and radiology offers no such path. A
            # consultation always has a visit, so this is unreachable in
            # practice -- it is stated so the failure, if the invariant ever
            # breaks, names the missing visit instead of surfacing as a billing
            # error at confirmation.
            raise UserError(
                "Consultation %s has no visit, so a radiology order has no "
                "appointment to schedule against." % consultation.name
            )

        if diagnosis and diagnosis.sudo().consultation_id != consultation:
            raise ValidationError(
                "Diagnosis '%s' was not recorded in this consultation."
                % diagnosis.display_name
            )

        clean = self._validate_order_values(values)

        # De-duplicated HERE, not left to the caller. Ordering the same exam
        # twice in ONE submission is always a client mistake -- a double-added
        # row in the picker -- and it would raise two charges for one study.
        # Order of first appearance is preserved so the request reads the way
        # the doctor built it.
        unique_exams = []
        for exam in exams:
            if exam not in unique_exams:
                unique_exams.append(exam)

        # Ownership, derived. The browser decides none of this.
        vals = {
            "patient_id": consultation.patient_id.id,
            "physician_id": consultation.doctor_id.id,
            "appointment_id": consultation.appointment_id.id,
            "encounter_id": consultation.encounter_id.id,
            "consultation_id": consultation.id,
            "diagnosis_id": diagnosis.id if diagnosis else False,
            "request_token": request_token or False,
            "line_ids": [
                (0, 0, self._prepare_order_line(exam, (index + 1) * 10))
                for index, exam in enumerate(unique_exams)
            ],
        }
        vals.update(clean)

        request = self.create(vals)
        # THE authoritative transition. hospital_billing's override raises the
        # charges; this module does not and must not.
        request.action_confirm_request()
        return request

    @api.model
    def _prepare_order_line(self, exam, sequence):
        """One request line, seeded from the catalogue exactly as the form does.

        hospital.radiology.request.line._onchange_exam_id() copies body_part and
        contrast_required off the chosen exam, and an onchange does not run for
        a programmatic create. Without this, an order placed from the Doctor
        Desk would reach the imaging department with a blank body part and no
        contrast flag, while the identical order typed into the Odoo form
        carried both -- and contrast is patient preparation, not decoration.

        Copied rather than related-through so the line keeps what the catalogue
        said ON THE DAY IT WAS ORDERED, which is the shape the base model chose
        by making these writable columns instead of related fields.
        """
        return {
            "exam_id": exam.id,
            "sequence": sequence,
            "body_part": exam.body_part or False,
            "contrast_required": exam.contrast_required,
        }

    def cancel_from_consultation(self):
        """Cancel through the model's own workflow. Nothing bespoke.

        action_cancel() runs the base transition guard -- which permits
        cancellation only from draft/requested/scheduled -- and then
        hospital_billing's override cancels the operational charges, refusing
        outright if any has already been delivered. All of that is exactly the
        behaviour required here, so none of it is reimplemented or
        second-guessed.
        """
        self.ensure_one()
        if not self.consultation_id:
            raise UserError(
                "This radiology request was not ordered from a consultation "
                "and cannot be cancelled from the Doctor Desk."
            )
        if self.consultation_id.sudo().state != "draft":
            raise UserError(
                "Consultation %s is completed and its radiology orders are "
                "locked." % self.consultation_id.sudo().name
            )
        self.action_cancel()
        return self

    def doctor_can_cancel(self):
        """Affordance only. action_cancel() decides the real answer.

        The completed-consultation clause is the Slice 4 policy, applied the way
        laboratory applies it: once the consultation is signed, the DOCTOR's
        cancellation control goes away, while the imaging department's own
        workflow keeps whatever the base model still permits. The order freezes
        for the desk, not for the hospital.
        """
        self.ensure_one()
        if self.state not in RAD_CANCELLABLE_STATES:
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
        """Every radiology order of one consultation, newest first. Pure read."""
        if not consultation:
            return self.browse()
        return self.search(
            [("consultation_id", "=", consultation.id)],
            order="id desc",
        )
