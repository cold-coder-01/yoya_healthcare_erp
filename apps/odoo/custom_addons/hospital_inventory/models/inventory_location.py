from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HospitalInventoryLocation(models.Model):
    _name = "hospital.inventory.location"
    _description = "Hospital Inventory Location"
    _inherit = ["mail.thread"]
    _order = "location_type, name"

    # Location types that represent a real, department-owned store. For these we
    # recommend (and softly require) a department link so stock can be traced.
    DEPARTMENT_LOCATION_TYPES = (
        "department_store",
        "pharmacy_store",
        "laboratory_store",
        "radiology_store",
        "procedure_store",
        "ward_store",
    )

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True, tracking=True)
    location_type = fields.Selection(
        [
            ("central_store", "Central Store"),
            ("transit", "Transit / Internal Transfer"),
            ("department_store", "Department Store"),
            ("pharmacy_store", "Pharmacy Store"),
            ("laboratory_store", "Laboratory Store"),
            ("radiology_store", "Radiology Store"),
            ("procedure_store", "Procedure Store"),
            ("ward_store", "Ward / Nursing Store"),
            ("adjustment", "Adjustment / Counterpart"),
            ("virtual_consumption", "Virtual Consumption"),
            ("other", "Other"),
        ],
        string="Location Type",
        required=True,
        default="department_store",
        tracking=True,
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Department",
        ondelete="set null",
        tracking=True,
    )
    responsible_user_id = fields.Many2one("res.users", string="Responsible")
    is_default = fields.Boolean(
        string="Default for Type",
        help="When set, this is the default location used for its type "
        "(central store, transit or virtual consumption) in automatic flows.",
    )
    is_consumption_location = fields.Boolean(
        string="Consumption Location",
        default=False,
        help="Virtual location that receives stock when it is consumed for a "
        "patient/service. Stock here represents used/consumed inventory.",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    # Convenience stock visibility (computed from batches sitting here).
    batch_count = fields.Integer(
        compute="_compute_stock_stats", string="Batches"
    )
    total_inventory_value = fields.Monetary(
        compute="_compute_stock_stats",
        string="Inventory Value",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    _sql_constraints = [
        ("code_uniq", "unique(code)", "The location code must be unique."),
    ]

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    def _compute_stock_stats(self):
        Batch = self.env["hospital.inventory.batch"]
        for rec in self:
            if not rec.id:
                rec.batch_count = 0
                rec.total_inventory_value = 0.0
                continue
            batches = Batch.search(
                [("location_id", "=", rec.id), ("active", "=", True)]
            )
            rec.batch_count = len(batches)
            rec.total_inventory_value = sum(batches.mapped("inventory_value"))

    # A non-blocking hint shown in the UI: department-level stores are clearer
    # when linked to a department, but it is not hard-required so baseline
    # locations can be created before departments are configured.
    department_recommended = fields.Boolean(
        compute="_compute_department_recommended",
        string="Department Recommended",
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.depends("location_type", "department_id")
    def _compute_department_recommended(self):
        for rec in self:
            rec.department_recommended = (
                rec.location_type in self.DEPARTMENT_LOCATION_TYPES
                and not rec.department_id
            )

    @api.constrains("is_default", "location_type")
    def _check_single_default(self):
        # Only one default per "singleton" type (central store / transit /
        # virtual consumption) so automatic flows resolve deterministically.
        singleton_types = ("central_store", "transit", "virtual_consumption")
        for rec in self:
            if rec.is_default and rec.location_type in singleton_types:
                dup = self.search_count(
                    [
                        ("id", "!=", rec.id),
                        ("location_type", "=", rec.location_type),
                        ("is_default", "=", True),
                    ]
                )
                if dup:
                    raise ValidationError(
                        "There is already a default "
                        f"{dict(self._fields['location_type'].selection).get(rec.location_type)} "
                        "location. Only one default is allowed per type."
                    )

    # ------------------------------------------------------------------
    # Default location helpers (used by opening stock / consumption flows)
    # ------------------------------------------------------------------
    @api.model
    def _get_default_of_type(self, location_type, consumption=False):
        """Return the best default location for ``location_type``.

        Prefers a record flagged ``is_default``; otherwise the first active one.
        Returns an empty recordset when none exist (callers must tolerate this).
        """
        domain = [("location_type", "=", location_type), ("active", "=", True)]
        if consumption:
            domain.append(("is_consumption_location", "=", True))
        default = self.search(domain + [("is_default", "=", True)], limit=1)
        if default:
            return default
        return self.search(domain, limit=1)

    @api.model
    def get_default_central_store(self):
        return self._get_default_of_type("central_store")

    @api.model
    def get_default_transit(self):
        return self._get_default_of_type("transit")

    @api.model
    def get_default_consumption_location(self):
        return self._get_default_of_type("virtual_consumption", consumption=True)

    @api.model
    def get_default_pharmacy_store(self):
        return self._get_default_of_type("pharmacy_store")

    @api.model
    def get_default_laboratory_store(self):
        return self._get_default_of_type("laboratory_store")

    @api.model
    def get_default_radiology_store(self):
        return self._get_default_of_type("radiology_store")

    @api.depends("name", "code")
    def _compute_display_name(self):
        for rec in self:
            if rec.code:
                rec.display_name = f"[{rec.code}] {rec.name or ''}".strip()
            else:
                rec.display_name = rec.name or ""
