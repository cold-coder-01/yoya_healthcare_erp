"""Laboratory encounter billing.

The laboratory lives INSIDE hospital_management (there is no separate lab module),
and hospital_billing already depends on hospital_management. So this integration
belongs here: no new module, no dependency reversal, no cycle.

No clinical event is invented. The existing laboratory workflow already provides
exactly what is needed:

    request.action_confirm_request      draft -> requested        : charge per test line
    request.action_mark_sample_collected requested -> collected   : CLEARANCE GATE, in progress
    result.action_validate               entered -> validated     : DELIVERY
    request.action_cancel                (draft/requested only)   : cancel charges

Sample collection is REQUEST-LEVEL in this system (there is no per-line collection
state), so clearance is validated across ALL of the request's charges BEFORE any
clinical state changes -- never partially.

Delivery happens at result VALIDATION, never at result entry: a draft result is not
an authoritative clinical fact. Validation delivers only the tests actually on that
result, so partial validation never delivers unfinished tests.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

LAB_EVENT = "lab_test"
SOURCE_MODEL = "hospital.laboratory.request"

# Request states in which no sample has been taken yet.
PRE_COLLECTION_STATES = ("draft", "requested")

# The ONLY state in which the ordered tests may still be edited.
#
# Charges are raised at confirmation. If a test can be added, removed or swapped after
# that, the request and its charges silently drift apart -- which is exactly what
# happened to LABREQ0188: confirmed with one test, a second test appended an hour later,
# so two tests carried one charge and the second test would have been performed free.
# The ordered set is therefore frozen once the request leaves draft.
LINE_EDITABLE_STATES = ("draft",)

# A charge that actually covers an ordered test.
LIVE_CHARGE_STATES = ("draft", "active")


class HospitalLaboratoryTestBilling(models.Model):
    """Explicit test -> billing service mapping. Never guessed from the name."""

    _inherit = "hospital.laboratory.test"

    billing_service_id = fields.Many2one(
        "hospital.billing.service",
        string="Billing Service",
        domain="[('service_type', '=', 'laboratory')]",
        help="The billable service for this test. Required before the test can be "
        "charged; the engine refuses to charge an unmapped test rather than guess.",
    )

    @api.constrains("billing_service_id")
    def _check_billing_service(self):
        for test in self:
            service = test.billing_service_id
            if not service:
                continue
            if service.service_type != "laboratory":
                raise ValidationError(
                    f"Test {test.name}: the billing service must be of type Laboratory."
                )
            if not service.active:
                raise ValidationError(
                    f"Test {test.name}: billing service '{service.name}' is archived."
                )

    def _assert_billable(self, company=None):
        """Raise listing EVERY unmapped/misconfigured test. Atomic by design:
        the caller validates the whole set before creating any charge."""
        company = company or self.env.company
        today = fields.Date.context_today(self)
        unmapped, bad = [], []
        for test in self:
            service = test.billing_service_id
            if not service:
                unmapped.append(test.display_name)
                continue
            if not service.active:
                bad.append(f"{test.display_name}: service archived")
            elif service.company_id and service.company_id != company:
                bad.append(
                    f"{test.display_name}: service belongs to {service.company_id.name}, "
                    f"not {company.name}"
                )
            elif not service.is_effective_on(today):
                bad.append(f"{test.display_name}: service not effective on {today}")
        problems = []
        if unmapped:
            problems.append(
                "No billing service is mapped for: %s" % ", ".join(sorted(unmapped))
            )
        if bad:
            problems.append("Misconfigured billing service for: %s" % "; ".join(sorted(bad)))
        if problems:
            raise UserError(
                "Laboratory billing configuration error. No charges were created.\n\n"
                + "\n".join(problems)
                + "\n\nMap each test to an active laboratory billing service and retry."
            )
        return True


class HospitalLaboratoryRequestBilling(models.Model):
    _inherit = "hospital.laboratory.request"

    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Encounter",
        ondelete="restrict",
        copy=False,
        domain="[('patient_id', '=', patient_id)]",
        help="The visit this request belongs to. Reused from the originating "
        "appointment; a standalone request must have one established explicitly.",
    )
    charge_line_ids = fields.One2many(
        "hospital.charge.line", compute="_compute_charge_line_ids",
        string="Laboratory Charges", compute_sudo=True,
    )
    charge_count = fields.Integer(compute="_compute_charge_line_ids", compute_sudo=True)
    receipt_ids = fields.Many2many(
        "hospital.charge.receipt", string="Payment Receipts",
        compute="_compute_receipts", compute_sudo=True,
    )
    receipt_count = fields.Integer(compute="_compute_receipts", compute_sudo=True)
    billing_blocked = fields.Boolean(compute="_compute_billing_blocked", compute_sudo=True)
    billing_clearance_message = fields.Char(
        compute="_compute_billing_blocked", compute_sudo=True
    )

    def _charge_domain(self):
        """Every charge ever raised from THIS request, by provenance."""
        self.ensure_one()
        return [
            ("source_model", "=", SOURCE_MODEL),
            ("source_res_id", "=", self.id or 0),
            ("source_event", "=", LAB_EVENT),
        ]

    def _compute_charge_line_ids(self):
        # active_test=False: an archived/cancelled charge is still part of this
        # request's financial history and must be counted, not quietly dropped.
        Charge = self.env["hospital.charge.line"].sudo().with_context(active_test=False)
        for request in self:
            charges = Charge.search(request._charge_domain()) if request.id else Charge
            request.charge_line_ids = charges
            request.charge_count = len(charges)

    def _compute_receipts(self):
        """Distinct receipts reached through this request's charge ALLOCATIONS.

        One consolidated 900 ETB receipt covering both tests is ONE receipt here,
        not one per allocated charge.
        """
        Alloc = self.env["hospital.charge.receipt.allocation"].sudo()
        for request in self:
            charges = request.charge_line_ids
            receipts = Alloc.search(
                [("charge_line_id", "in", charges.ids)]
            ).mapped("receipt_id") if charges else Alloc.env["hospital.charge.receipt"]
            request.receipt_ids = receipts
            request.receipt_count = len(receipts)

    def _compute_billing_blocked(self):
        engine = self.env["hospital.billing.engine"].sudo()
        for request in self:
            if request.state != "requested" or not request.encounter_id:
                request.billing_blocked = False
                request.billing_clearance_message = False
                continue
            result = engine.check_financial_clearance(
                request.encounter_id, charges=request.charge_line_ids
            )
            request.billing_blocked = not result["cleared"]
            request.billing_clearance_message = result["reason"]

    # ------------------------------------------------------------------
    # Cross-patient integrity (encounter)
    # ------------------------------------------------------------------
    @api.constrains("patient_id", "encounter_id", "appointment_id")
    def _check_encounter_patient(self):
        """An encounter belongs to exactly one patient, and to one visit.

        Server-side, so RPC/import/direct ORM cannot attach another patient's
        encounter -- or an encounter from a different visit than the appointment.
        """
        # sudo(): see _check_clinical_references_patient -- the invariant belongs to the
        # data, and clinical staff who cannot read appointments must still be able to
        # move the request through its workflow.
        for request in self.sudo():
            enc = request.encounter_id
            if not enc:
                continue
            if request.patient_id and enc.patient_id != request.patient_id:
                raise ValidationError(
                    "Encounter %s belongs to patient %s, but this laboratory request is "
                    "for %s. An encounter can never be shared across patients."
                    % (enc.name, enc.patient_id.display_name,
                       request.patient_id.display_name)
                )
            appt = request.appointment_id
            if appt and enc.appointment_id and enc.appointment_id != appt:
                raise ValidationError(
                    "Encounter %s belongs to appointment %s, but this request cites "
                    "appointment %s. The request must reuse its own visit's encounter."
                    % (enc.name, enc.appointment_id.display_name, appt.display_name)
                )

    @api.onchange("patient_id")
    def _onchange_patient_id_clear_encounter(self):
        if self.encounter_id and self.encounter_id.patient_id != self.patient_id:
            self.encounter_id = False

    def _assert_billing_integrity(self):
        """Final atomic gate, run BEFORE a single charge is created.

        The three identities that must hold for the money to land on the right
        patient's account. Anything else and we create nothing at all.
        """
        self.ensure_one()
        me = self.sudo()          # integrity check, not an access check
        enc = me.encounter_id
        appt = me.appointment_id

        if appt and appt.patient_id != me.patient_id:
            raise ValidationError(
                "Laboratory request %s is for %s but cites appointment %s, which belongs "
                "to %s. No charges were created."
                % (me.name, me.patient_id.display_name, appt.display_name,
                   appt.patient_id.display_name)
            )
        if enc and enc.patient_id != me.patient_id:
            raise ValidationError(
                "Laboratory request %s is for %s but resolved to encounter %s, which "
                "belongs to %s. No charges were created."
                % (me.name, me.patient_id.display_name, enc.name,
                   enc.patient_id.display_name)
            )
        if appt and enc and enc.appointment_id and enc.appointment_id != appt:
            raise ValidationError(
                "Laboratory request %s cites appointment %s but resolved to encounter %s "
                "of appointment %s. No charges were created."
                % (me.name, appt.display_name, enc.name,
                   enc.appointment_id.display_name)
            )
        return True

    # ------------------------------------------------------------------
    # Encounter linkage -- reuse, never guess
    # ------------------------------------------------------------------
    def _resolve_encounter(self):
        """Reuse the originating appointment's encounter. Never guesses from
        patient+date, and never silently opens a second encounter for one visit."""
        self.ensure_one()
        if self.encounter_id:
            return self.encounter_id
        engine = self.env["hospital.billing.engine"].sudo()
        if self.appointment_id:
            # Check BEFORE resolving: never create/reuse an encounter for a visit that
            # belongs to a different patient.
            if self.appointment_id.patient_id != self.patient_id:
                raise ValidationError(
                    "Laboratory request %s is for %s but cites appointment %s, which "
                    "belongs to %s. No encounter was created and no charges were made."
                    % (self.name, self.patient_id.display_name,
                       self.appointment_id.display_name,
                       self.appointment_id.patient_id.display_name)
                )
            encounter = engine.get_or_create_encounter(self.appointment_id)
            self.sudo().write({"encounter_id": encounter.id})
            return encounter
        raise UserError(
            "Laboratory request %s has no appointment, so its encounter is ambiguous.\n\n"
            "Either link the originating appointment, or use 'Establish Standalone "
            "Encounter' to open one explicitly. The system will not guess which visit "
            "this request belongs to." % self.name
        )

    def action_establish_standalone_encounter(self):
        """The explicit, controlled path for a genuine walk-in lab request."""
        self.ensure_one()
        if self.encounter_id:
            raise UserError("This request already belongs to encounter %s."
                            % self.encounter_id.name)
        if self.appointment_id:
            raise UserError(
                "This request has an appointment; its encounter must be reused, "
                "not created standalone."
            )
        encounter = self.env["hospital.encounter"].sudo().create({
            "patient_id": self.patient_id.id,
            "encounter_type": "outpatient",
            "primary_doctor_id": self.physician_id.id or False,
            "opened_at": fields.Datetime.now(),
        })
        encounter.action_start()
        self.sudo().write({"encounter_id": encounter.id})
        self._create_audit_log(
            action_type="update",
            description="Standalone encounter %s established for laboratory request."
                        % encounter.name,
        )
        return True

    # ------------------------------------------------------------------
    # Charge creation -- ATOMIC
    # ------------------------------------------------------------------
    def _ensure_laboratory_billing(self):
        """One charge per request line. Idempotent, and all-or-nothing:
        configuration is validated for EVERY line before ANY charge is created."""
        engine = self.env["hospital.billing.engine"].sudo()
        for request in self:
            lines = request.line_ids
            if not lines:
                raise UserError(
                    "Laboratory request %s has no tests to confirm." % request.name
                )
            # Validate the WHOLE set first -- so a bad line cannot leave the request
            # half-charged.
            lines.mapped("test_id").sudo()._assert_billable(request.company_id_for_billing())

            encounter = request._resolve_encounter()
            # FINAL ATOMIC GATE. Patient/appointment/encounter must agree before any
            # money is attached to anyone's account.
            request._assert_billing_integrity()
            engine.get_or_create_billing_account(encounter)
            for line in lines:
                service = line.test_id.billing_service_id
                charge = engine.create_or_update_charge(
                    encounter,
                    SOURCE_MODEL,
                    request.id,
                    LAB_EVENT,
                    line.test_id.display_name,
                    source_line_id=line.id,
                    service=service,
                    qty_requested=1.0,
                )
                # The request is CONFIRMED -- the test is really ordered.
                engine.activate_charge(charge)
            # charge_line_ids is a search-based compute with no ORM dependency on the
            # charges it finds, so creating them does not invalidate it. Without this,
            # a caller that read charge_line_ids BEFORE the charges existed (e.g. the
            # clearance banner) would keep seeing an empty set in the same transaction
            # and collection would mark nothing in progress.
            request.invalidate_recordset(["charge_line_ids", "charge_count"])
        return True

    def company_id_for_billing(self):
        self.ensure_one()
        return self.encounter_id.company_id or self.env.company

    def _line_charge(self, line):
        """The charge belonging to EXACTLY this request line."""
        return self.env["hospital.charge.line"].sudo().search([
            ("source_key", "=", "%s:%s:%s:%s" % (SOURCE_MODEL, self.id, line.id, LAB_EVENT))
        ], limit=1)

    def _clearance_error(self, clearance):
        """Explain WHICH tests are uncleared, without leaking cash to clinicians.

        Sample collection is request-level, so one uncleared test blocks the whole
        request -- the user must be told which one, or the block is unactionable.
        """
        self.ensure_one()
        may_see_cash = self.env.su or any(
            self.env.user.has_group(g) for g in (
                "hospital_management.group_hospital_receptionist",
                "hospital_management.group_hospital_accountant",
                "hospital_management.group_hospital_manager",
                "hospital_management.group_hospital_system_administrator",
            )
        )
        details = []
        for charge in self.charge_line_ids:
            if charge.charge_state not in ("draft", "active"):
                continue
            blocked_auth = charge.authorization_state == "pending"
            due = charge.amount_due_for_clearance  # read via compute_sudo on the recordset
            if not blocked_auth and due <= 0:
                continue
            test = charge.description
            code = charge.service_id.code or ""
            label = "%s%s" % (test, " [%s]" % code if code else "")
            if blocked_auth:
                details.append("%s -- payer authorization pending" % label)
            elif may_see_cash:
                details.append("%s -- unpaid, %.2f due" % (label, due))
            else:
                details.append("%s -- payment not cleared (see front desk)" % label)

        body = "\n".join("  - %s" % d for d in details) or "  - %s" % clearance["reason"]
        return UserError(
            "Samples for %s cannot be collected. The following ordered tests are not "
            "financially cleared:\n\n%s\n\n"
            "Sample collection covers the whole request, so every test must be cleared. "
            "Take payment, record payer authorization, or authorize an emergency bypass "
            "on encounter %s.\n\nNo sample and no charge has been changed."
            % (self.patient_id.display_name, body,
               self.encounter_id.name if self.encounter_id else "n/a")
        )

    # ------------------------------------------------------------------
    # Workflow overrides
    # ------------------------------------------------------------------
    def action_confirm_request(self):
        result = super().action_confirm_request()
        self.filtered(lambda r: r.state == "requested")._ensure_laboratory_billing()
        return result

    def _assert_all_lines_charged(self):
        """Every ordered test must already carry a live charge. No self-healing.

        Silently creating the missing charge here would bill a test that never went
        through confirmation -- skipping the atomic configuration check and raising a
        charge after the financial-clearance conversation had already happened. If the
        request and its charges have drifted, that is a fault to surface, not to paper
        over: refuse, and let a human decide.
        """
        self.ensure_one()
        uncharged = []
        for line in self.line_ids:
            charge = self._line_charge(line)
            if not charge or charge.charge_state not in LIVE_CHARGE_STATES:
                uncharged.append(line.test_id.display_name)
        if uncharged:
            raise ValidationError(
                "Laboratory request %s cannot proceed: %d ordered test(s) have no valid "
                "charge:\n\n%s\n\n"
                "This request is out of step with its billing (tests were most likely "
                "added after it was confirmed). No sample has been collected and no "
                "charge has been created. Cancel this request and raise a new one with "
                "the full set of tests."
                % (self.name, len(uncharged),
                   "\n".join("  - %s" % t for t in uncharged))
            )
        return True

    def action_mark_sample_collected(self):
        """CLEARANCE GATE. Collection is request-level here, so every charge on the
        request is validated BEFORE any clinical state changes. A failed check leaves
        the sample state, the charges and the audit trail completely untouched."""
        engine = self.env["hospital.billing.engine"].sudo()
        for request in self.filtered(lambda r: r.state == "requested"):
            # Every ordered test must already be charged. We do NOT self-heal.
            request._assert_all_lines_charged()
            charges = request.charge_line_ids
            clearance = engine.check_financial_clearance(
                request.encounter_id, persist=True, charges=charges
            )
            if not clearance["cleared"]:
                raise request._clearance_error(clearance)

        result = super().action_mark_sample_collected()

        for request in self.filtered(lambda r: r.state == "sample_collected"):
            encounter = request.encounter_id
            if encounter and encounter.state in ("planned", "checked_in"):
                encounter.sudo().action_start()
            for charge in request.charge_line_ids:
                # Sample taken = work commenced. NOT delivered.
                engine.mark_charge_in_progress(charge)
        return result

    def action_cancel(self):
        """Cancel the operational charges. Never deletes. The base model already
        forbids cancelling once the sample is collected, so delivered work is safe."""
        engine = self.env["hospital.billing.engine"].sudo()
        for request in self.filtered(lambda r: r.state in PRE_COLLECTION_STATES):
            for charge in request.charge_line_ids:
                if charge.charge_state in ("cancelled", "reversed"):
                    continue
                if charge.qty_delivered > 0 or charge.delivery_state in (
                    "delivered", "partially_delivered"
                ):
                    raise UserError(
                        "Test '%s' has already been delivered and cannot be cancelled. "
                        "A credit/reversal workflow is required, which is outside this "
                        "phase." % charge.description
                    )
                engine.cancel_charge(
                    charge, reason="Laboratory request %s cancelled" % request.name
                )
        return super().action_cancel()

    def action_record_payment(self):
        """ONE payment event for the whole request: the cashier sees every test's
        allocation and takes a single 900 ETB receipt, not one receipt per test."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Record Payment / Advance",
            "res_model": "hospital.charge.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_source_lab_request_id": self.id},
        }

    def action_view_receipts(self):
        """Receipts reached through this request's charge ALLOCATIONS.

        A consolidated receipt paying both tests appears ONCE, not once per charge.
        """
        self.ensure_one()
        receipts = self.receipt_ids
        action = {
            "type": "ir.actions.act_window",
            "name": "Payment Receipts",
            "res_model": "hospital.charge.receipt",
            "domain": [("id", "in", receipts.ids)],
        }
        if len(receipts) == 1:
            action.update({"view_mode": "form", "res_id": receipts.id})
        else:
            action["view_mode"] = "list,form"
        return action

    def action_view_charges(self):
        """Smart-button target.

        One charge -> open it directly. Several -> open the filtered list.
        The action carries NO sudo and NO elevated context: the user's own ACL and
        the field-level `groups=` on the cash columns of hospital.charge.line apply
        exactly as they would anywhere else. A lab technician who follows this button
        still cannot see amount_received.
        """
        self.ensure_one()
        # Resolve through the domain, NOT by reading charge_line_ids: a plain read of
        # an x2many silently drops archived records, so an archived charge would make
        # a 2-charge request look like a 1-charge request and wrongly open a form.
        charges = self.env["hospital.charge.line"].sudo().with_context(
            active_test=False
        ).search(self._charge_domain())
        action = {
            "type": "ir.actions.act_window",
            "name": "Laboratory Charges",
            "res_model": "hospital.charge.line",
            # active_test=False so an archived/cancelled charge stays reachable from
            # the button that counted it.
            "context": {"active_test": False},
            "domain": self._charge_domain(),
        }
        if len(charges) == 1:
            action.update({"view_mode": "form", "res_id": charges.id})
        else:
            action["view_mode"] = "list,form"
        return action


class HospitalLaboratoryRequestLineBilling(models.Model):
    """The ordered set of tests is frozen once the request leaves draft.

    Charges are raised at confirmation, one per line. Allowing the lines to change
    afterwards lets the request and its charges drift apart silently -- a test gets
    performed with no charge behind it (LABREQ0188), or a charge survives for a test
    nobody ordered. Enforced in the MODEL, so RPC, import and direct ORM writes are
    covered, not just the form.
    """

    _inherit = "hospital.laboratory.request.line"

    @staticmethod
    def _locked_message(request, verb):
        return (
            "Laboratory request %s is '%s'. Requested tests cannot be %s once the "
            "request has been confirmed -- the charges were already raised from the "
            "tests as they stood.\n\n"
            "Cancel this request and raise a new one with the tests you want."
            % (request.name, request.state, verb)
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            request = self.env["hospital.laboratory.request"].browse(
                vals.get("request_id")
            )
            if request.exists() and request.state not in LINE_EDITABLE_STATES:
                raise UserError(self._locked_message(request, "added"))
        return super().create(vals_list)

    def write(self, vals):
        # Re-pointing a line at a different test after confirmation would leave the
        # charge describing (and pricing) a test that is no longer ordered.
        if "test_id" in vals or "request_id" in vals:
            for line in self:
                request = line.request_id
                if request and request.state not in LINE_EDITABLE_STATES:
                    raise UserError(self._locked_message(request, "changed"))
        return super().write(vals)

    def unlink(self):
        engine = self.env["hospital.billing.engine"].sudo()
        for line in self:
            request = line.request_id
            if not request:
                continue
            if request.state not in LINE_EDITABLE_STATES:
                raise UserError(self._locked_message(request, "removed"))
            # In draft no charge exists yet, but stay defensive: if one somehow does,
            # cancel exactly that test's charge -- never a sibling's.
            charge = request._line_charge(line)
            if not charge or charge.charge_state in ("cancelled", "reversed"):
                continue
            if charge.qty_delivered > 0 or charge.delivery_state in (
                "delivered", "partially_delivered"
            ):
                raise UserError(
                    "Test '%s' has already been delivered; its line cannot be removed. "
                    "A credit/reversal workflow is required." % charge.description
                )
            engine.cancel_charge(charge, reason="Test removed from request %s" % request.name)
        return super().unlink()


class HospitalLaboratoryResultBilling(models.Model):
    _inherit = "hospital.laboratory.result"

    def _resolve_request_line(self, result_line):
        """The EXACT ordered test this result line reports on.

        Never silently picks the first test_id match: with the same test ordered
        twice, that would deliver a sibling's charge. Lineage is authoritative;
        the fallback exists only for result lines created before request_line_id
        existed, and it refuses to guess when the match is ambiguous.
        """
        result_line.ensure_one()
        if result_line.request_line_id:
            return result_line.request_line_id

        request = self.request_id
        candidates = request.line_ids.filtered(
            lambda l: l.test_id == result_line.test_id
        )
        if len(candidates) == 1:
            # Controlled fallback: exactly one ordered line matches, so the link is
            # unambiguous. Record it so the record becomes traceable from now on.
            result_line.sudo().write({"request_line_id": candidates.id})
            return candidates
        if not candidates:
            raise UserError(
                "Result line '%s' does not correspond to any test ordered on laboratory "
                "request %s. It cannot be validated against a charge."
                % (result_line.test_id.display_name, request.display_name)
            )
        raise UserError(
            "Ambiguous result line: '%s' was ordered %d times on laboratory request %s, "
            "and this historical result line does not record which occurrence it reports "
            "on.\n\nSet 'Request Line' on the result line to the exact ordered test, then "
            "validate again. The system will not guess which charge to deliver."
            % (result_line.test_id.display_name, len(candidates), request.display_name)
        )

    def action_validate(self):
        """The authoritative completion event. Delivery happens HERE.

        Never blocked for payment: the sample was already taken and the test run.
        Only the tests actually on THIS result are delivered, so partial validation
        cannot deliver an unfinished sibling. Idempotent: re-validating an
        already-delivered charge is a no-op.

        Lineage is resolved BEFORE the clinical transition, so an ambiguous historical
        line aborts validation cleanly rather than half-delivering.
        """
        engine = self.env["hospital.billing.engine"].sudo()

        # Resolve every line first -- all-or-nothing. An ambiguous historical line
        # aborts here, before any clinical state or charge has moved.
        plan = {}
        for res in self:
            request = res.request_id
            if not request:
                continue
            plan[res.id] = (
                request,
                [res._resolve_request_line(rl) for rl in res.line_ids],
            )

        result = super().action_validate()

        for res in self.filtered(lambda r: r.state == "validated"):
            if res.id not in plan:
                continue
            request, req_lines = plan[res.id]
            for req_line in req_lines:
                charge = request._line_charge(req_line)
                if not charge or charge.charge_state in ("cancelled", "reversed"):
                    continue
                if charge.delivery_state == "delivered":
                    continue  # already delivered -- idempotent
                engine.mark_charge_delivered(charge, qty_delivered=1.0)
        return result
