"""Per-company rollout switch for the payer responsibility engine.

PHASE 1 SCOPE: THE FIELD EXISTS AND NOTHING READS IT.

It is declared now, at its safe default, so that the Phase 3 work has a
deployment control to attach to rather than inventing one under time pressure
and shipping it enabled. Nothing in this module branches on it yet:
allocate_payer() is still unimplemented and check_financial_clearance() is
untouched.

WHY res.company AND NOT hospital.billing.accounting.config
----------------------------------------------------------
That config model lives in hospital_billing_accounting, which DEPENDS ON this
module. Reading it from here would invert the dependency graph. The flag governs
operational billing behaviour that exists with or without the accounting bridge
installed, so the company is its correct home.

No view is added for it in this phase, deliberately: a switch that changes
nothing should not yet be presented to anyone as though it does. The Phase 3
change that gives it meaning is what will surface it in the UI.
"""

from odoo import fields, models

PAYER_RESPONSIBILITY_MODES = [
    ("off", "Off — legacy payer behaviour"),
    ("shadow", "Shadow — compute responsibility, do not enforce"),
    ("enforce", "Enforce — responsibility drives financial clearance"),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    payer_responsibility_mode = fields.Selection(
        PAYER_RESPONSIBILITY_MODES,
        string="Payer Responsibility Engine",
        default="off",
        required=True,
        help=(
            "Controls how sponsored (non-self-pay) visits are handled.\n\n"
            "OFF: current behaviour, unchanged. No responsibility is computed "
            "and financial clearance works exactly as it does today.\n\n"
            "SHADOW: responsibility is computed and recorded for comparison, but "
            "it does not affect financial clearance or any queue stage.\n\n"
            "ENFORCE: computed patient responsibility drives financial "
            "clearance.\n\n"
            "Phase 1 note: nothing reads this field yet. It is present at its "
            "safe default so the later rollout has a control to attach to."
        ),
    )
