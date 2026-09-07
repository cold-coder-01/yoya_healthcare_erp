from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalStockConsumption(models.Model):
    _name = "hospital.stock.consumption"
    _description = "Hospital Stock Consumption"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "consumption_date desc, id desc"

    # ------------------------------------------------------------------
    # Source-document ownership — EXTENSIBLE HOOKS (single source of truth)
    #
    # Downstream modules (e.g. hospital_operation_theatre) register their own
    # source field + consumption type by overriding these hooks through super().
    # Every constraint, provenance lock, onchange, context sanitizer and the
    # ``is_source_generated`` computation reads ownership through them, so the
    # base module never needs to know a dependent module's fields.
    #
    # Field names are the REAL model fields (note: laboratory uses
    # ``lab_request_id``, not ``laboratory_request_id``).
    # ------------------------------------------------------------------
    def _source_field_names(self):
        """All registered source Many2one field names. Override + super() to add."""
        return [
            "admission_id",
            "procedure_id",
            "pharmacy_dispense_id",
            "nursing_round_id",
            "medication_administration_id",
            "lab_request_id",
            "radiology_request_id",
        ]

    def _source_type_map(self):
        """{consumption_type: {'allowed': set, 'context': set}}.

        ``allowed`` = source fields that may be populated for this type.
        ``context`` = allowed fields that are contextual only (e.g. the admission
        a procedure/surgery belongs to) and are NOT the single clinical primary.
        Override + super().update(...) to register a new type.
        """
        return {
            "pharmacy": {"allowed": {"pharmacy_dispense_id"}, "context": set()},
            "procedure": {
                "allowed": {"procedure_id", "admission_id"},
                "context": {"admission_id"},
            },
            "nursing": {
                "allowed": {
                    "nursing_round_id",
                    "medication_administration_id",
                    "admission_id",
                },
                "context": {"admission_id"},
            },
            "laboratory": {"allowed": {"lab_request_id"}, "context": set()},
            "radiology": {"allowed": {"radiology_request_id"}, "context": set()},
            "admission": {"allowed": {"admission_id"}, "context": set()},
            "manual": {"allowed": set(), "context": set()},
        }

    def _source_valid_types(self):
        """Consumption types that carry source ownership rules."""
        return set(self._source_type_map())

    def _source_cfg(self, consumption_type):
        return self._source_type_map().get(
            consumption_type, {"allowed": set(), "context": set()}
        )

    def _source_primary_fields(self):
        """Source fields that count as a clinical primary (context excluded)."""
        primary = set()
        for cfg in self._source_type_map().values():
            primary |= cfg["allowed"] - cfg["context"]
        return primary

    def _source_context_fields(self):
        context = set()
        for cfg in self._source_type_map().values():
            context |= cfg["context"]
        return context

    def _provenance_field_names(self):
        """Fields frozen as provenance on a source-generated consumption."""
        return ["patient_id", "consumption_type"] + list(self._source_field_names())

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
        tracking=True,
    )
    consumption_date = fields.Datetime(
        string="Consumption Date",
        default=fields.Datetime.now,
        tracking=True,
    )
    consumption_type = fields.Selection(
        [
            ("pharmacy", "Pharmacy"),
            ("procedure", "Procedure"),
            ("nursing", "Nursing"),
            ("laboratory", "Laboratory"),
            ("radiology", "Radiology"),
            ("admission", "Admission"),
            ("manual", "Manual"),
        ],
        string="Consumption Type",
        default="manual",
        required=True,
        tracking=True,
    )
    patient_id = fields.Many2one("hospital.patient", string="Patient", ondelete="set null")
    admission_id = fields.Many2one("hospital.admission", string="Admission", ondelete="set null")
    procedure_id = fields.Many2one(
        "hospital.procedure.request", string="Procedure", ondelete="set null"
    )
    pharmacy_dispense_id = fields.Many2one(
        "hospital.pharmacy.dispense", string="Pharmacy Dispense", ondelete="set null"
    )
    nursing_round_id = fields.Many2one(
        "hospital.nursing.round", string="Nursing Round", ondelete="set null"
    )
    medication_administration_id = fields.Many2one(
        "hospital.medication.administration",
        string="Medication Administration",
        ondelete="set null",
    )
    lab_request_id = fields.Many2one(
        "hospital.laboratory.request", string="Laboratory Request", ondelete="set null"
    )
    radiology_request_id = fields.Many2one(
        "hospital.radiology.request", string="Radiology Request", ondelete="set null"
    )
    department_id = fields.Many2one("hospital.department", string="Department", ondelete="set null")
    source_location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Source Location",
        ondelete="set null",
        help="Department/store location the stock is consumed from. When set, "
        "consumed batches must sit at this location.",
    )
    consumption_location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Consumption Location",
        ondelete="set null",
        default=lambda self: self.env["hospital.inventory.location"].get_default_consumption_location(),
        help="Virtual location that receives consumed/used stock.",
    )
    requested_by = fields.Many2one(
        "res.users", string="Requested By", default=lambda self: self.env.user
    )
    approved_by = fields.Many2one("res.users", string="Approved By", readonly=True, copy=False)
    approved_date = fields.Datetime(string="Approved Date", readonly=True, copy=False)
    consumed_by = fields.Many2one("res.users", string="Consumed By", readonly=True, copy=False)
    consumed_date = fields.Datetime(string="Consumed Date", readonly=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("consumed", "Consumed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "hospital.stock.consumption.line", "consumption_id", string="Consumption Lines"
    )
    movement_ids = fields.One2many(
        "hospital.stock.movement", "consumption_id", string="Stock Movements"
    )
    movement_count = fields.Integer(compute="_compute_movement_count", string="Movements")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    amount_total = fields.Monetary(
        compute="_compute_amount_total",
        string="Total Estimated Cost",
        currency_field="currency_id",
        store=True,
    )
    total_consumption_value = fields.Monetary(
        compute="_compute_amount_total",
        string="Total Consumption Value",
        currency_field="currency_id",
        store=True,
        help="Total batch-cost value of consumed stock. Informational only — "
        "no accounting entry is posted.",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)
    is_source_generated = fields.Boolean(
        compute="_compute_is_source_generated",
        store=True,
        string="Source Generated",
        help="True once a primary clinical source document is linked. Such a "
        "consumption's patient, type and source links are locked provenance — "
        "correct it by cancelling and recreating, never by re-pointing.",
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    def _compute_movement_count(self):
        for rec in self:
            rec.movement_count = len(rec.movement_ids)

    # Dynamic depends: the lambda is evaluated at registry setup and picks up
    # every registered source field, INCLUDING those added downstream (e.g.
    # surgery_id). This is why is_source_generated recomputes for extension
    # fields and is not tied to the base field list.
    @api.depends(lambda self: ["consumption_type"] + list(self._source_field_names()))
    def _compute_is_source_generated(self):
        for rec in self:
            context = rec._source_cfg(rec.consumption_type)["context"]
            rec.is_source_generated = any(
                rec[f] and f not in context for f in rec._source_field_names()
            )

    @api.depends("line_ids.subtotal")
    def _compute_amount_total(self):
        for rec in self:
            total = sum(rec.line_ids.mapped("subtotal"))
            rec.amount_total = total
            rec.total_consumption_value = total

    # ------------------------------------------------------------------
    # Helper used by all integration modules to build a consumption
    # ------------------------------------------------------------------
    @api.model
    def create_for_source(self, consumption_type, source_vals=None, line_vals=None):
        """Create a draft consumption for an operational source document.

        This is the ONE source-sanitized service every integration goes through.
        It guarantees a created consumption carries ONLY its intended source(s):

        1. strips every ``default_<source_field>`` key from the context, so a
           stale UI default from a previous screen cannot leak in via default_get;
        2. explicitly clears every sibling source field not supplied by the
           caller (so nothing is auto-filled);
        3. sets only the caller's intended primary/contextual sources.

        Patient consistency is then enforced by ``_check_source_documents`` at
        create time (sudo/forged-context/RPC cannot bypass a constraint).

        ``source_vals`` carries header values (patient_id, department_id and the
        relevant source Many2one). ``line_vals`` is a list of dicts for lines.
        """
        source_vals = dict(source_vals or {})
        source_fields = self._source_field_names()

        # (1) drop stale default_<source_field> keys from the context.
        clean_context = {
            key: value
            for key, value in self.env.context.items()
            if not (
                key.startswith("default_")
                and key[len("default_"):] in source_fields
            )
        }
        model = self.with_context(clean_context)

        vals = dict(source_vals)
        vals["consumption_type"] = consumption_type
        # (2) explicitly clear every sibling source the caller did not intend.
        for field_name in source_fields:
            vals.setdefault(field_name, False)
        if line_vals:
            vals["line_ids"] = [(0, 0, lv) for lv in line_vals]
        return model.create(vals)

    # ------------------------------------------------------------------
    # Source-document integrity
    #
    # UI domains keep users honest; these constraints keep the DATA honest.
    # @api.constrains fires on create AND write and is NOT bypassed by sudo,
    # a forged context, XML-RPC or direct ORM writes — so cross-patient and
    # cross-type links cannot be forged through any of those channels.
    # ------------------------------------------------------------------
    def _source_records(self):
        """Yield (field_name, populated_record) for each set source field."""
        self.ensure_one()
        for field_name in self._source_field_names():
            record = self[field_name]
            if record:
                yield field_name, record

    # Base source fields are listed explicitly so the constraint fires on them;
    # downstream modules add their own trigger (see hospital_operation_theatre)
    # that also calls _check_source_documents. Ownership itself is read from the
    # extensible hooks, so surgery et al. are validated with the correct rules.
    @api.constrains(
        "consumption_type",
        "patient_id",
        "admission_id",
        "procedure_id",
        "pharmacy_dispense_id",
        "nursing_round_id",
        "medication_administration_id",
        "lab_request_id",
        "radiology_request_id",
    )
    def _check_source_documents(self):
        for rec in self:
            cfg = rec._source_cfg(rec.consumption_type)
            allowed = cfg["allowed"]
            context = cfg["context"]
            populated = [f for f, _ in rec._source_records()]

            # (1) Only the source field appropriate to consumption_type.
            wrong_type = [f for f in populated if f not in allowed]
            if wrong_type:
                labels = ", ".join(rec._fields[f].string for f in wrong_type)
                raise ValidationError(
                    f"A '{rec.consumption_type}' consumption cannot link to: "
                    f"{labels}. Only the source appropriate to the consumption "
                    f"type may be set."
                )

            # (2) At most one clinical source document (context excluded).
            primary = [f for f in populated if f not in context]
            if len(primary) > 1:
                labels = ", ".join(rec._fields[f].string for f in primary)
                raise ValidationError(
                    f"Only one clinical source document may be linked, but "
                    f"several are set: {labels}."
                )

            # (3) Every source document must belong to this consumption's patient.
            for field_name, record in rec._source_records():
                if "patient_id" not in record._fields:
                    continue
                if not rec.patient_id:
                    raise ValidationError(
                        f"Set the patient before linking "
                        f"'{rec._fields[field_name].string}'."
                    )
                if record.patient_id and record.patient_id != rec.patient_id:
                    raise ValidationError(
                        f"'{rec._fields[field_name].string}' "
                        f"({record.display_name}) belongs to patient "
                        f"'{record.patient_id.display_name}', not "
                        f"'{rec.patient_id.display_name}'. The patient is "
                        f"authoritative — a source document from another patient "
                        f"cannot be linked."
                    )

    # ------------------------------------------------------------------
    # Provenance model
    #
    # A consumption becomes "source-generated" the moment a PRIMARY clinical
    # source document is saved on it. From then on its provenance — patient,
    # consumption type, primary source AND contextual admission — is frozen even
    # in Draft. The only correction path is to cancel and recreate; provenance is
    # never re-pointed, cleared, hidden by a type change, or disconnected by a
    # patient change. Field-level clearing on patient/type edits is offered ONLY
    # to genuinely manual / unsourced drafts.
    # ------------------------------------------------------------------
    def _current_value(self, field_name):
        """Comparable stored value (id for m2o, raw otherwise)."""
        self.ensure_one()
        value = self[field_name]
        if self._fields[field_name].type == "many2one":
            return value.id if value else False
        return value

    @api.onchange("consumption_type")
    def _onchange_consumption_type_clear_sources(self):
        """Clear now-inappropriate source links — only on unsourced drafts."""
        if self._origin and self._origin.is_source_generated:
            return
        allowed = self._source_cfg(self.consumption_type)["allowed"]
        for field_name in self._source_field_names():
            if field_name not in allowed and self[field_name]:
                self[field_name] = False

    @api.onchange("patient_id")
    def _onchange_patient_clear_sources(self):
        """Clear cross-patient source links — only on unsourced drafts."""
        if self._origin and self._origin.is_source_generated:
            return
        for field_name, record in list(self._source_records()):
            if (
                "patient_id" in record._fields
                and record.patient_id
                and self.patient_id
                and record.patient_id != self.patient_id
            ):
                self[field_name] = False

    @api.onchange("source_location_id")
    def _onchange_source_location_id(self):
        """Clear batches the new source location makes invalid.

        Only batches that the new location could genuinely have supplied itself
        are dropped — a batch kept because the store is empty stays put, so
        changing the location never silently wipes the operator's work.
        """
        for rec in self:
            if not rec.source_location_id:
                continue
            for line in rec.line_ids:
                if not line.batch_id:
                    continue
                if (
                    line.batch_id.location_id != rec.source_location_id
                    and line._source_location_can_serve()
                ):
                    line.batch_id = False

    @api.constrains("source_location_id")
    def _check_source_location_lines(self):
        for rec in self:
            if not rec.source_location_id:
                continue
            for line in rec.line_ids:
                line._check_batch_location_policy()

    def _reject_provenance_change(self, vals):
        """Block ANY change to provenance on a source-generated consumption."""
        self.ensure_one()
        for field_name in self._provenance_field_names():
            if field_name in vals and vals[field_name] != self._current_value(field_name):
                raise UserError(
                    "This consumption was generated from a clinical source "
                    "document. Its patient, consumption type, source document and "
                    "linked admission are locked provenance and cannot be changed "
                    "— even in Draft. To correct it, cancel this consumption and "
                    "create a new one."
                )

    def _guard_unsourced_write(self, vals):
        """Freeze provenance of an unsourced consumption once it is approved."""
        self.ensure_one()
        if self.state not in ("approved", "consumed"):
            return
        for field_name in self._provenance_field_names():
            if field_name in vals and vals[field_name] != self._current_value(field_name):
                raise UserError(
                    "Patient, consumption type and source documents cannot be "
                    "changed once a consumption is approved."
                )

    def _augment_clear_vals(self, rec_vals, vals):
        """For an unsourced draft, clear source links made invalid by this write."""
        self.ensure_one()
        new_type = vals.get("consumption_type", self.consumption_type)
        new_patient = vals.get("patient_id", self.patient_id.id)
        allowed = self._source_cfg(new_type)["allowed"]
        for field_name in self._source_field_names():
            value = rec_vals[field_name] if field_name in rec_vals else self[field_name].id
            if not value:
                continue
            if field_name not in allowed:
                rec_vals[field_name] = False
                continue
            record = self.env[self._fields[field_name].comodel_name].browse(value)
            if (
                "patient_id" in record._fields
                and record.patient_id
                and new_patient
                and record.patient_id.id != new_patient
            ):
                rec_vals[field_name] = False

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_approve(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            if not rec.line_ids:
                raise UserError("Add at least one consumption line before approving.")
            rec.write(
                {
                    "state": "approved",
                    "approved_by": self.env.user.id,
                    "approved_date": fields.Datetime.now(),
                }
            )
            rec._create_audit_log(
                action_type="state_change",
                description="Stock consumption approved.",
                old_value="State: draft",
                new_value="State: approved",
            )

    def action_consume(self):
        for rec in self:
            if rec.state == "consumed":
                raise UserError("This consumption has already been consumed.")
            if rec.state != "approved":
                raise UserError("Only an approved consumption can be consumed.")
            if not rec.line_ids:
                raise UserError("Nothing to consume — there are no consumption lines.")
            for line in rec.line_ids:
                line._check_consumable()
                batch = line.batch_id
                # The batch itself is what deduct_quantity() reduces, so the
                # batch's own location is the store that is physically drained.
                # Record THAT — falling back to the header only when the batch
                # carries no location — so a movement can never claim a store was
                # debited when its stock never sat there.
                from_location = batch.location_id or rec.source_location_id
                batch.deduct_quantity(line.quantity)
                self.env["hospital.stock.movement"].create(
                    {
                        "movement_type": "consumption",
                        "item_id": line.item_id.id,
                        "batch_id": batch.id,
                        "from_location_id": from_location.id if from_location else False,
                        "to_location_id": rec.consumption_location_id.id
                        if rec.consumption_location_id
                        else False,
                        "from_department_id": rec.department_id.id,
                        "quantity": line.quantity,
                        "unit_cost": line.unit_cost,
                        "currency_id": line.currency_id.id or rec.currency_id.id,
                        "consumption_id": rec.id,
                        "patient_id": rec.patient_id.id,
                        "admission_id": rec.admission_id.id,
                        "procedure_id": rec.procedure_id.id,
                        "nursing_round_id": rec.nursing_round_id.id,
                        "inventory_accounting_state": "pending",
                    }
                )
            rec.write(
                {
                    "state": "consumed",
                    "consumed_by": self.env.user.id,
                    "consumed_date": fields.Datetime.now(),
                }
            )
            rec._create_audit_log(
                action_type="state_change",
                description="Stock consumption consumed; movements created.",
                new_value="State: consumed",
            )

    def action_cancel(self):
        for rec in self:
            if rec.state == "consumed":
                raise UserError("A consumed consumption cannot be cancelled.")
            if rec.state in ("draft", "approved"):
                rec.write({"state": "cancelled"})
                rec._create_audit_log(
                    action_type="state_change",
                    description="Stock consumption cancelled.",
                    new_value="State: cancelled",
                )

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "cancelled"):
            rec.write({"state": "draft"})
            rec._create_audit_log(
                action_type="state_change",
                description="Stock consumption reset to draft.",
                new_value="State: draft",
            )

    def action_view_movements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Stock Movements",
            "res_model": "hospital.stock.movement",
            "view_mode": "list,form",
            "domain": [("consumption_id", "=", self.id)],
            "context": {"default_consumption_id": self.id},
        }

    # ------------------------------------------------------------------
    # ORM overrides + audit
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.stock.consumption.sequence")
                    or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Stock consumption created.",
                new_value=record._audit_summary(["name", "consumption_type", "patient_id"]),
            )
        return records

    def write(self, vals):
        if any(f in vals for f in self._provenance_field_names()):
            for rec in self:
                if rec.is_source_generated:
                    # Provenance is immutable — no clear, replace, hide or
                    # disconnect. Correction is cancel + recreate only.
                    rec._reject_provenance_change(vals)
                else:
                    rec._guard_unsourced_write(vals)

        # Unsourced DRAFTs may have source links cleared when the patient or type
        # changes, so the record stays internally consistent. Source-generated
        # records were already rejected above; approved/consumed were frozen.
        if "patient_id" in vals or "consumption_type" in vals:
            clearers = self.filtered(
                lambda r: r.state == "draft" and not r.is_source_generated
            )
            if clearers:
                result = True
                for rec in clearers:
                    rec_vals = dict(vals)
                    rec._augment_clear_vals(rec_vals, vals)
                    result = super(HospitalStockConsumption, rec).write(rec_vals) and result
                remaining = self - clearers
                if remaining:
                    result = super(HospitalStockConsumption, remaining).write(vals) and result
                return result

        return super().write(vals)

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                if rec.state == "consumed":
                    rec._create_audit_log(
                        action_type="delete_attempt",
                        description="Deletion of consumed consumption blocked.",
                        old_value=rec._audit_summary(["name", "state"]),
                    )
                    raise UserError(
                        "Consumed consumption records are part of the audit trail and "
                        "cannot be deleted. Only a System Administrator may remove them."
                    )
        return super().unlink()

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------
    def _audit_summary(self, field_names):
        values = []
        for field_name in field_names:
            if field_name in self._fields:
                value = self[field_name]
                if hasattr(value, "mapped"):
                    value = ", ".join(value.mapped("display_name"))
                values.append(f"{field_name}: {value}")
        return "; ".join(values)

    def _create_audit_log(self, action_type, description, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for rec in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=rec.patient_id.id if rec.patient_id else False,
                model_name=rec._name,
                record_id=rec.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )


class HospitalStockConsumptionLine(models.Model):
    _name = "hospital.stock.consumption.line"
    _description = "Hospital Stock Consumption Line"
    _order = "consumption_id, id"

    consumption_id = fields.Many2one(
        "hospital.stock.consumption",
        string="Consumption",
        required=True,
        ondelete="cascade",
    )
    item_id = fields.Many2one(
        "hospital.inventory.item", string="Item", required=True, ondelete="restrict"
    )
    # Not required at field level so integration modules can prefill draft
    # lines from a source document before a batch is chosen. Consuming a line
    # without a batch is blocked in `_check_consumable` at consume time.
    batch_id = fields.Many2one(
        "hospital.inventory.batch",
        string="Batch / Lot",
        ondelete="restrict",
        domain="[('item_id', '=', item_id)]",
    )
    quantity = fields.Float(string="Quantity", required=True, default=1.0)
    available_quantity = fields.Float(
        related="batch_id.available_quantity",
        string="Available Qty",
        readonly=True,
    )
    unit_cost = fields.Monetary(string="Unit Cost", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    subtotal = fields.Monetary(
        compute="_compute_subtotal",
        string="Subtotal",
        currency_field="currency_id",
        store=True,
    )
    line_value = fields.Monetary(
        compute="_compute_subtotal",
        string="Line Value",
        currency_field="currency_id",
        store=True,
    )
    notes = fields.Text()

    @api.depends("quantity", "unit_cost")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.unit_cost
            line.line_value = line.quantity * line.unit_cost

    @api.onchange("batch_id")
    def _onchange_batch_id(self):
        if self.batch_id:
            if not self.item_id:
                self.item_id = self.batch_id.item_id
            if not self.unit_cost:
                self.unit_cost = self.batch_id.unit_cost
            if self.batch_id.currency_id:
                self.currency_id = self.batch_id.currency_id

    @api.onchange("item_id")
    def _onchange_item_id(self):
        if self.batch_id and self.batch_id.item_id != self.item_id:
            self.batch_id = False

    # ------------------------------------------------------------------
    # Batch eligibility — SINGLE SOURCE OF TRUTH
    #
    # The picker (view domain), the constrains and the consume-time gate all
    # resolve eligibility through these two helpers, so what the operator can
    # SEE, what they can SAVE and what they can CONSUME cannot drift apart.
    #
    # Location policy is deliberately enforced here on the server and NOT in
    # the view domain. A One2many line does not reliably carry ``consumption_id``
    # while the form is being edited, so a parent-dependent picker domain is
    # unreliable in the UI — but ``consumption_id`` is always populated by the
    # time any server-side check runs.
    # ------------------------------------------------------------------
    def _eligible_batch_domain(self):
        """Item-level eligibility: what may be consumed at all, anywhere."""
        self.ensure_one()
        return [
            ("item_id", "=", self.item_id.id),
            ("active", "=", True),
            ("state", "not in", ("expired", "blocked", "depleted")),
            ("is_expired", "=", False),
            ("available_quantity", ">", 0),
        ]

    def _source_location_can_serve(self):
        """True when the consumption's source location holds usable stock of this item.

        This is what makes the location rule enforceable without ever dead-ending
        the operator: the department store is BINDING when it can actually serve
        the item, and yields when it is genuinely empty (so lab/radiology are not
        blocked from working while their store is unstocked). The batch's real
        location is still what gets deducted and recorded on the movement either
        way, so provenance stays truthful in both branches.
        """
        self.ensure_one()
        location = self.consumption_id.source_location_id
        if not location or not self.item_id:
            return False
        return bool(
            self.env["hospital.inventory.batch"].search_count(
                self._eligible_batch_domain() + [("location_id", "=", location.id)]
            )
        )

    def _check_batch_location_policy(self, error=ValidationError):
        """Reject stock from another store when the source location can serve."""
        self.ensure_one()
        location = self.consumption_id.source_location_id
        batch = self.batch_id
        if not location or not batch or batch.location_id == location:
            return
        if not self._source_location_can_serve():
            return
        raise error(
            "Batch '%s' is at '%s', but '%s' holds usable stock of '%s'. "
            "Consume from your own store, or change the Source Location on this "
            "consumption if you genuinely need to draw from another store."
            % (
                batch.display_name,
                batch.location_id.display_name if batch.location_id else "no location",
                location.display_name,
                self.item_id.display_name,
            )
        )

    @api.onchange("batch_id")
    def _onchange_batch_location_warning(self):
        """Warn the moment cross-store stock is picked — before it is saved."""
        location = self.consumption_id.source_location_id
        if not location or not self.batch_id or self.batch_id.location_id == location:
            return
        return {
            "warning": {
                "title": "Stock from another location",
                "message": (
                    "Batch '%s' is at '%s', not this consumption's source location "
                    "'%s'. The movement will record '%s' as the store that was "
                    "debited."
                    % (
                        self.batch_id.display_name,
                        self.batch_id.location_id.display_name
                        if self.batch_id.location_id
                        else "no location",
                        location.display_name,
                        self.batch_id.location_id.display_name
                        if self.batch_id.location_id
                        else "no location",
                    )
                ),
            }
        }

    @api.constrains("batch_id", "item_id", "consumption_id")
    def _check_batch_eligibility(self):
        for line in self:
            if not line.batch_id:
                continue
            if line.batch_id.item_id != line.item_id:
                raise ValidationError(
                    "The selected batch does not belong to the chosen item."
                )
            batch = line.batch_id
            if not batch.active:
                raise ValidationError("The selected batch is not active.")
            if batch.state in ("expired", "blocked", "depleted") or batch.is_expired:
                raise ValidationError(
                    f"Batch '{batch.name}' is {batch.state} and cannot be selected."
                )
            if batch.available_quantity <= 0:
                raise ValidationError(
                    f"Batch '{batch.name}' has no available quantity."
                )
            line._check_batch_location_policy()

    @api.constrains("quantity")
    def _check_quantity(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError("Consumption quantity must be greater than zero.")

    @api.constrains("batch_id", "item_id")
    def _check_batch_item(self):
        for line in self:
            if line.batch_id and line.batch_id.item_id != line.item_id:
                raise ValidationError(
                    "The selected batch does not belong to the chosen item."
                )

    def _check_consumable(self):
        """Validate a line is safe to consume (called at consume time)."""
        self.ensure_one()
        if self.quantity <= 0:
            raise UserError("Consumption quantity must be greater than zero.")
        if not self.batch_id:
            raise UserError(
                f"Select a batch for item '{self.item_id.display_name}' before consuming."
            )
        if self.batch_id.item_id != self.item_id:
            raise UserError("The selected batch does not belong to the chosen item.")
        batch = self.batch_id
        if batch.state in ("expired", "blocked", "depleted") or batch.is_expired:
            raise UserError(
                f"Batch '{batch.name}' is {batch.state} and cannot be consumed."
            )
        if batch.available_quantity < self.quantity:
            raise UserError(
                f"Insufficient stock in batch '{batch.name}'. "
                f"Available: {batch.available_quantity}, requested: {self.quantity}."
            )
        # Same location policy the picker and the constrains apply — re-checked
        # here because stock may have moved between saving and consuming.
        self._check_batch_location_policy(error=UserError)
