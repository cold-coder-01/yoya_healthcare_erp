"""The reporting facts: two read-only SQL views, one attribution rule.

    hospital_revenue_charge_fact   one row per charge line: WHERE, WHEN and FOR
                                   WHOM the care was delivered, and its
                                   operational Delivered Value.
    hospital_revenue_fact          one row per POSTED invoice / credit-note
                                   provenance allocation: the recognized
                                   revenue, joined to the charge fact for its
                                   attribution.

WHY TWO VIEWS. The attribution (category, direct doctor, episode doctor,
episode department, payer scope, the inpatient boundary) is defined ONCE, on
the charge, and both bases read it: recognized revenue through the
allocations, Delivered Value straight from the charge. The two bases can never
attribute the same charge differently.

RECOGNIZED REVENUE -- THE ONLY FORMULA
--------------------------------------
    revenue = -move_line.balance * allocation.quantity / move_line.quantity

The invoice engine merges charges that share product, price, discount, taxes
and account into ONE invoice line, so the line alone has lost the charge. Every
allocation on a line shares that line's price terms (that is what the group key
guarantees), so splitting the line's posted GL balance by quantity is exact and
the allocations of a line sum back to it. balance is the company-currency GL
revenue: net of discount, VAT-exclusive (VAT sits on its own tax line), and
positive (a debit) on a credit-note line -- so a posted credit allocation is
negative revenue with no special case. Only POSTED moves contribute; draft and
cancelled documents are reservations, not revenue.

Rows are deliberately NOT rounded here: an aggregate is rounded once, by the
service, so the sum of a line's allocations ties to the GL to the cent.

THREE VIEWS, ONE RULE
---------------------
    hospital_revenue_charge_class   the CHEAP per-charge facts: category,
                                    exclusion, payer scope, service time,
                                    delivered value. Plain joins only.
    hospital_revenue_charge_fact    class + the attribution (admission
                                    boundary, episode doctor / department,
                                    direct doctor). Per-row lookups.
    hospital_revenue_fact           posted allocations + the charge fact.
A figure that needs no doctor or department (a delivered total, the unbilled
backlog) reads the class view and does not pay for attribution.

THE INPATIENT BOUNDARY
----------------------
An admission ADOPTS the outpatient visit's encounter, so the encounter alone
cannot say whether care was outpatient or inpatient, and encounter_type is
rewritten to 'inpatient' at admission. The boundary is therefore TIME:

    service_at   bed day k      admission_date + k days (the period start; the
                                charge itself may be posted days later, at a
                                transfer or the discharge)
                 admission fee  admission_date
                 anything else  delivered_at (first delivery), else create_date
    inpatient    a stay charge of an admission, or any charge of the
                 encounter whose service_at lies inside an admitted /
                 transferred / discharged admission's [admission_date,
                 discharge_date]

    episode doctor      outpatient: encounter.primary_doctor_id
                        inpatient:  admission.physician_id, else (FALLBACK,
                                    flagged) encounter.primary_doctor_id
    episode department  outpatient: encounter.department_id
                        inpatient:  the department of the ward the patient was
                                    in at service_at (transfer history), else
                                    (FALLBACK, flagged) encounter.department_id
    direct doctor       consultation charges of an appointment only: the
                        consultation's physician, else the appointment doctor.
                        Nothing else is ever direct doctor revenue.

EXCLUDED, NEVER SERVICE REVENUE
-------------------------------
    deposit       the inpatient advance deposit charge: money, not care
    registration  the patient card: its delivery semantics are not fixed yet
"""
from odoo import fields, models, tools

CATEGORY_SELECTION = [
    ("consultation", "Consultation"),
    ("laboratory", "Laboratory"),
    ("radiology", "Radiology"),
    ("pharmacy", "Pharmacy"),
    ("admission_fee", "Admission Fee"),
    ("bed_stay", "Bed / Stay"),
    ("procedure", "Procedure"),
    ("other", "Other"),
    # Excluded scope: present in the facts so they can be counted, never
    # reported as service revenue.
    ("deposit", "Inpatient Deposit (excluded)"),
    ("registration", "Registration / Card (excluded)"),
]
EXCLUDED_CATEGORIES = ("deposit", "registration")

PAYER_SCOPE_SELECTION = [("self_pay", "Self-pay"), ("sponsored", "Payer / Sponsored")]

CHARGE_CLASS_VIEW = "hospital_revenue_charge_class"
CHARGE_FACT_VIEW = "hospital_revenue_charge_fact"
REVENUE_FACT_VIEW = "hospital_revenue_fact"

# Admission states in which the patient is, or was, actually in a bed.
STAY_STATES_SQL = "('admitted', 'transferred', 'discharged')"

CHARGE_CLASS_SQL = """
CREATE VIEW {class_view} AS
SELECT
    c.id                                            AS id,
    c.id                                            AS charge_id,
    c.company_id                                    AS company_id,
    c.currency_id                                   AS currency_id,
    c.billing_account_id                            AS billing_account_id,
    c.encounter_id                                  AS encounter_id,
    c.patient_id                                    AS patient_id,
    c.service_id                                    AS service_id,
    s.service_type                                  AS service_type,
    cat.category                                    AS category,
    CASE WHEN cat.category IN ('deposit', 'registration')
         THEN cat.category END                      AS excluded_reason,
    CASE WHEN COALESCE(ba.payer_type, 'self_pay') = 'self_pay'
          AND COALESCE(c.responsibility_state, 'self_pay') = 'self_pay'
         THEN 'self_pay' ELSE 'sponsored' END       AS payer_scope,
    c.charge_state                                  AS charge_state,
    (c.charge_state NOT IN ('cancelled', 'reversed')) AS is_live,
    c.source_model                                  AS source_model,
    c.source_res_id                                 AS source_res_id,
    c.source_line_id                                AS source_line_id,
    c.source_event                                  AS source_event,
    c.uom_id                                        AS uom_id,
    c.qty_delivered                                 AS qty_delivered,
    c.qty_billable                                  AS qty_billable,
    c.price_unit_net                                AS price_unit_net,
    CASE WHEN c.charge_state NOT IN ('cancelled', 'reversed')
         THEN COALESCE(c.qty_billable, 0) * COALESCE(c.price_unit_net, 0)
         ELSE 0 END                                 AS delivered_value,
    COALESCE(c.allow_reinvoice_after_credit, FALSE) AS allow_reinvoice_after_credit,
    c.delivered_at                                  AS delivered_at,
    svc.service_at                                  AS service_at,
    -- Base-column twins of service_at, for PRE-FILTERING: for every charge
    -- that is not a stay charge, service_at IS operational_at.
    COALESCE(c.delivered_at, c.create_date)         AS operational_at,
    (c.source_model = 'hospital.admission'
        AND c.source_event IN ('bed_day', 'admission_fee', 'inpatient_deposit'))
                                                    AS is_stay_source,
    sa.id                                           AS stay_admission_id
FROM hospital_charge_line c
JOIN hospital_billing_account ba ON ba.id = c.billing_account_id
LEFT JOIN hospital_billing_service s ON s.id = c.service_id
LEFT JOIN LATERAL (
    SELECT CASE
        WHEN c.source_model = 'hospital.admission' AND c.source_event = 'inpatient_deposit'
            THEN 'deposit'
        WHEN c.source_model = 'hospital.admission' AND c.source_event = 'admission_fee'
            THEN 'admission_fee'
        WHEN c.source_model = 'hospital.admission' AND c.source_event = 'bed_day'
            THEN 'bed_stay'
        WHEN s.service_type IN ('consultation', 'laboratory', 'radiology', 'pharmacy',
                                'procedure', 'registration')
            THEN s.service_type
        WHEN s.service_type = 'admission' THEN 'bed_stay'
        ELSE 'other'
    END AS category
) cat ON TRUE
LEFT JOIN hospital_admission sa
       ON c.source_model = 'hospital.admission'
      AND c.source_event IN ('bed_day', 'admission_fee', 'inpatient_deposit')
      AND sa.id = c.source_res_id
LEFT JOIN LATERAL (
    SELECT CASE
        WHEN sa.id IS NOT NULL AND c.source_event = 'bed_day' AND sa.admission_date IS NOT NULL
            THEN sa.admission_date + COALESCE(c.source_line_id, 0) * INTERVAL '1 day'
        WHEN sa.id IS NOT NULL AND c.source_event = 'admission_fee' AND sa.admission_date IS NOT NULL
            THEN sa.admission_date
        ELSE COALESCE(c.delivered_at, c.create_date)
    END AS service_at
) svc ON TRUE
"""

CHARGE_FACT_SQL = """
CREATE VIEW {charge_view} AS
SELECT
    k.*,
    adm.id                                          AS admission_id,
    (adm.id IS NOT NULL)                            AS is_inpatient,
    CASE WHEN adm.id IS NULL THEN enc.primary_doctor_id
         ELSE COALESCE(adm.physician_id, enc.primary_doctor_id) END
                                                    AS episode_doctor_id,
    (adm.id IS NOT NULL AND adm.physician_id IS NULL) AS episode_doctor_fallback,
    (adm.id IS NOT NULL AND adm.physician_id IS NOT NULL
        AND enc.primary_doctor_id IS NOT NULL
        AND adm.physician_id <> enc.primary_doctor_id) AS episode_doctor_mismatch,
    wa.ward_id                                      AS episode_ward_id,
    CASE WHEN adm.id IS NULL THEN enc.department_id
         ELSE COALESCE(w.department_id, enc.department_id) END
                                                    AS episode_department_id,
    (adm.id IS NOT NULL AND w.department_id IS NULL) AS episode_department_fallback,
    CASE WHEN k.category = 'consultation' AND k.source_model = 'hospital.appointment'
         THEN COALESCE(con.doctor_id, ap.doctor_id) END
                                                    AS direct_doctor_id
FROM {class_view} k
LEFT JOIN hospital_encounter enc ON enc.id = k.encounter_id
LEFT JOIN LATERAL (
    -- The admission the care was delivered INSIDE. LIMIT 1 is a hard
    -- guarantee: even overlapping (corrupt) admissions can never duplicate a
    -- charge, and so never duplicate revenue.
    SELECT a.id
      FROM hospital_admission a
     WHERE k.stay_admission_id IS NULL
       AND k.encounter_id IS NOT NULL
       AND a.encounter_id = k.encounter_id
       AND a.state IN {stay_states}
       AND a.admission_date IS NOT NULL
       AND a.admission_date <= k.service_at
       AND (a.discharge_date IS NULL OR k.service_at <= a.discharge_date)
     ORDER BY a.admission_date DESC, a.id DESC
     LIMIT 1
) ea ON TRUE
LEFT JOIN hospital_admission adm ON adm.id = COALESCE(k.stay_admission_id, ea.id)
LEFT JOIN LATERAL (
    -- The ward the patient was in at service time, from the transfer history.
    SELECT COALESCE(
        (SELECT tr.to_ward_id
           FROM hospital_admission_transfer tr
          WHERE tr.admission_id = adm.id AND tr.transfer_date <= k.service_at
          ORDER BY tr.transfer_date DESC, tr.id DESC LIMIT 1),
        (SELECT tr.from_ward_id
           FROM hospital_admission_transfer tr
          WHERE tr.admission_id = adm.id
          ORDER BY tr.transfer_date ASC, tr.id ASC LIMIT 1),
        adm.ward_id
    ) AS ward_id
) wa ON adm.id IS NOT NULL
LEFT JOIN hospital_ward w ON w.id = wa.ward_id
LEFT JOIN hospital_appointment ap
       ON k.source_model = 'hospital.appointment' AND ap.id = k.source_res_id
LEFT JOIN hospital_consultation con
       ON k.source_model = 'hospital.appointment'
      AND k.encounter_id IS NOT NULL
      AND con.encounter_id = k.encounter_id
"""

REVENUE_FACT_SQL = """
CREATE VIEW {revenue_view} AS
SELECT
    a.id                                            AS id,
    a.id                                            AS allocation_id,
    a.company_id                                    AS company_id,
    a.currency_id                                   AS currency_id,
    a.move_id                                       AS move_id,
    a.move_line_id                                  AS move_line_id,
    m.date                                          AS move_date,
    m.move_type                                     AS move_type,
    l.account_id                                    AS account_id,
    a.allocation_type                               AS allocation_type,
    CASE WHEN a.allocation_type = 'credit' THEN -a.quantity ELSE a.quantity END
                                                    AS quantity,
    CASE WHEN COALESCE(l.quantity, 0) = 0
         -- Guard only: posting refuses a line whose provenance quantity does
         -- not match it, so a zero-quantity provenance line cannot be posted.
         THEN (CASE WHEN a.allocation_type = 'credit' THEN -1 ELSE 1 END)
              * COALESCE(a.amount_untaxed_snapshot, 0)
         ELSE -l.balance * a.quantity / l.quantity
    END                                             AS revenue,
    cf.charge_id                                    AS charge_id,
    cf.billing_account_id                           AS billing_account_id,
    cf.encounter_id                                 AS encounter_id,
    cf.patient_id                                   AS patient_id,
    cf.service_id                                   AS service_id,
    cf.service_type                                 AS service_type,
    cf.category                                     AS category,
    cf.excluded_reason                              AS excluded_reason,
    cf.payer_scope                                  AS payer_scope,
    cf.source_model                                 AS source_model,
    cf.source_res_id                                AS source_res_id,
    cf.source_event                                 AS source_event,
    cf.uom_id                                       AS uom_id,
    cf.delivered_at                                 AS delivered_at,
    cf.operational_at                               AS operational_at,
    cf.is_stay_source                               AS is_stay_source,
    cf.service_at                                   AS service_at,
    cf.admission_id                                 AS admission_id,
    cf.is_inpatient                                 AS is_inpatient,
    cf.direct_doctor_id                             AS direct_doctor_id,
    cf.episode_doctor_id                            AS episode_doctor_id,
    cf.episode_doctor_fallback                      AS episode_doctor_fallback,
    cf.episode_doctor_mismatch                      AS episode_doctor_mismatch,
    cf.episode_ward_id                              AS episode_ward_id,
    cf.episode_department_id                        AS episode_department_id,
    cf.episode_department_fallback                  AS episode_department_fallback
FROM hospital_charge_invoice_allocation a
JOIN account_move m ON m.id = a.move_id AND m.state = 'posted'
JOIN account_move_line l ON l.id = a.move_line_id
-- OFFSET 0 is a deliberate planner fence. Without it the charge view is
-- flattened into this query and, misled by its computed predicates, the
-- planner attributes EVERY charge line before joining the few allocations in
-- the requested period (measured: 6.6 s at 205k charges). Fenced, the
-- attribution runs once per posted allocation of the period, by primary key.
JOIN LATERAL (
    SELECT * FROM {charge_view} x WHERE x.id = a.charge_id OFFSET 0
) cf ON TRUE
"""


# The ONE index this module adds, on a table it does not own. Justified by
# measurement: the Delivered Value basis selects charges by when they were
# delivered, and without it every request seq-scans the (wide) charge table --
# 0.4 s at 213k charges, linear beyond. Dropped again by the uninstall hook.
OPERATIONAL_INDEX = "hospital_revenue_charge_operational_at_idx"


def create_operational_index(cr):
    cr.execute(
        "CREATE INDEX IF NOT EXISTS %s ON hospital_charge_line "
        "((COALESCE(delivered_at, create_date)))" % OPERATIONAL_INDEX
    )


def create_revenue_views(cr):
    """(Re)create both views. Idempotent; called from either model's init()
    so the dependent view is always rebuilt after the one it reads."""
    tools.drop_view_if_exists(cr, REVENUE_FACT_VIEW)
    tools.drop_view_if_exists(cr, CHARGE_FACT_VIEW)
    tools.drop_view_if_exists(cr, CHARGE_CLASS_VIEW)
    cr.execute(CHARGE_CLASS_SQL.format(class_view=CHARGE_CLASS_VIEW))
    cr.execute(CHARGE_FACT_SQL.format(
        charge_view=CHARGE_FACT_VIEW, class_view=CHARGE_CLASS_VIEW, stay_states=STAY_STATES_SQL
    ))
    cr.execute(REVENUE_FACT_SQL.format(revenue_view=REVENUE_FACT_VIEW, charge_view=CHARGE_FACT_VIEW))


class _AttributionFields(models.AbstractModel):
    """The attribution columns shared by both facts."""

    _name = "hospital.revenue.attribution.mixin"
    _description = "Revenue Attribution Columns"

    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    charge_id = fields.Many2one("hospital.charge.line", readonly=True)
    billing_account_id = fields.Many2one("hospital.billing.account", readonly=True)
    encounter_id = fields.Many2one("hospital.encounter", readonly=True)
    patient_id = fields.Many2one("hospital.patient", readonly=True)
    service_id = fields.Many2one("hospital.billing.service", readonly=True)
    service_type = fields.Char(readonly=True)
    category = fields.Selection(CATEGORY_SELECTION, readonly=True)
    excluded_reason = fields.Selection(
        [("deposit", "Inpatient Deposit"), ("registration", "Registration / Card")],
        readonly=True,
    )
    payer_scope = fields.Selection(PAYER_SCOPE_SELECTION, readonly=True)
    source_model = fields.Char(readonly=True)
    source_res_id = fields.Integer(readonly=True)
    source_event = fields.Char(readonly=True)
    uom_id = fields.Many2one("uom.uom", readonly=True)
    delivered_at = fields.Datetime(readonly=True)
    operational_at = fields.Datetime(readonly=True)
    is_stay_source = fields.Boolean(readonly=True)
    service_at = fields.Datetime(
        readonly=True, help="When the care was delivered: the operational date."
    )
    admission_id = fields.Many2one("hospital.admission", readonly=True)
    is_inpatient = fields.Boolean(readonly=True)
    direct_doctor_id = fields.Many2one("hospital.doctor", readonly=True)
    episode_doctor_id = fields.Many2one("hospital.doctor", readonly=True)
    episode_doctor_fallback = fields.Boolean(readonly=True)
    episode_doctor_mismatch = fields.Boolean(readonly=True)
    episode_ward_id = fields.Many2one("hospital.ward", readonly=True)
    episode_department_id = fields.Many2one("hospital.department", readonly=True)
    episode_department_fallback = fields.Boolean(readonly=True)


class HospitalRevenueChargeFact(models.Model):
    _name = "hospital.revenue.charge.fact"
    _inherit = "hospital.revenue.attribution.mixin"
    _description = "Revenue Analytics: Charge Attribution and Delivered Value"
    _auto = False
    _table = CHARGE_FACT_VIEW
    _order = "id"

    charge_state = fields.Char(readonly=True)
    is_live = fields.Boolean(readonly=True)
    source_line_id = fields.Integer(readonly=True)
    qty_delivered = fields.Float(readonly=True, digits=(16, 3))
    qty_billable = fields.Float(readonly=True, digits=(16, 3))
    price_unit_net = fields.Float(readonly=True, digits=(16, 2))
    delivered_value = fields.Float(
        readonly=True,
        help="Eligible delivered quantity at the net unit price, before tax. "
        "Operational value, NOT revenue.",
    )
    allow_reinvoice_after_credit = fields.Boolean(readonly=True)

    def init(self):
        create_operational_index(self.env.cr)
        create_revenue_views(self.env.cr)


class HospitalRevenueFact(models.Model):
    _name = "hospital.revenue.fact"
    _inherit = "hospital.revenue.attribution.mixin"
    _description = "Revenue Analytics: Recognized Revenue (posted invoice provenance)"
    _auto = False
    _table = REVENUE_FACT_VIEW
    _order = "move_date desc, id desc"

    allocation_id = fields.Many2one("hospital.charge.invoice.allocation", readonly=True)
    move_id = fields.Many2one("account.move", readonly=True)
    move_line_id = fields.Many2one("account.move.line", readonly=True)
    move_date = fields.Date(string="Accounting Date", readonly=True)
    move_type = fields.Char(readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    allocation_type = fields.Selection(
        [("invoice", "Invoice"), ("credit", "Credit Note")], readonly=True
    )
    quantity = fields.Float(readonly=True, digits=(16, 3), help="Signed: credits are negative.")
    revenue = fields.Float(
        readonly=True,
        help="Posted GL revenue of the invoice line, split by provenance quantity. "
        "Net of discount, VAT-exclusive, negative on a credit note.",
    )

    def init(self):
        create_revenue_views(self.env.cr)
