"""What an agreement PERMITS a sponsor to carry, per service.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
A rule table answering "does this contract cover this service, and up to what".
It is the coverage master data whose absence hospital.charge.responsibility
documents at length -- until now the system could only RECORD a manually decided
sponsor share, because nothing existed to CALCULATE one from.

It does NOT replace hospital.charge.responsibility, and must never be read as
though it had. The split of these two objects is the load-bearing decision of
this slice:

    benefit rule          what the AGREEMENT PERMITS   (policy, editable, current)
    charge responsibility what an OFFICER AUTHORIZED   (fact, frozen, historical)

A rule edited next year must not retroactively change what a sponsor accepted
for a visit last year. That is why the evaluator is read-only and why an
authorized responsibility row keeps its own frozen snapshot. Deriving the
historical split from today's rules would reintroduce exactly the class of bug
FROZEN_PRICING_FIELDS and AUTHORIZED_FROZEN_FIELDS exist to prevent.

WHY service_type IS THE CATEGORY TIER
-------------------------------------
hospital.billing.service.service_type is already a required 8-value Selection on
every service, and hospital_billing_accounting._source_config already joins its
accounting configuration on it. It is the category abstraction this repository
already has. Adding a second taxonomy would mean two groupings of the same
services that could disagree -- and the one nobody maintains is the one that
silently decides coverage.

DELIBERATELY ABSENT IN THIS SLICE
---------------------------------
  * No deductibles, family pools, lifetime maxima or monthly/quarterly periods.
  * No agreement-wide (organisation) pool. limit_scope='agreement' stays blocked
    at activation; only 'member' and 'visit' are finished here.
  * No claim, sponsor invoice, receivable or settlement.
  * No negotiated per-service tariff. tariff_mode='agreement_price' still blocks
    activation; a rule says what SHARE is covered, never what the price is.
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .charge_line import AMOUNT_TOLERANCE
from .payer_agreement import (
    DRAFTING_GROUPS,
    PAYER_COMMERCIAL_READ,
)

# Editing coverage policy is the same duty as drafting the agreement it belongs
# to, so it reuses DRAFTING_GROUPS verbatim rather than inventing a parallel
# tuple. The Cashier and the Front Desk Nurse are absent from it, and this slice
# does not widen a single ACL.
BENEFIT_RULE_AUTHORITY = DRAFTING_GROUPS

COVERAGE_TYPES = [
    ("percentage", "Percentage Of Charge"),
    ("fixed_sponsor", "Fixed Sponsor Amount"),
    ("patient_copay", "Fixed Patient Copay"),
    ("excluded", "Not Covered"),
]


class HospitalPayerBenefitRule(models.Model):
    _name = "hospital.payer.benefit.rule"
    _description = "Payer Agreement Benefit Rule"
    # Sequence then id: the tie-break is deterministic even when an operator
    # leaves every sequence at its default, which is what stops two equally
    # specific rules from being resolved by insertion luck.
    _order = "agreement_id, sequence, id"

    agreement_id = fields.Many2one(
        "hospital.payer.agreement",
        required=True,
        ondelete="cascade",
        index=True,
        string="Agreement",
    )
    sequence = fields.Integer(default=10)

    # Context mirrors, stored so constraints stay cheap and rules are searchable
    # per company. The same shape charge_responsibility and receipt allocation
    # already use.
    company_id = fields.Many2one(
        related="agreement_id.company_id", store=True, readonly=True, index=True,
    )
    currency_id = fields.Many2one(
        related="agreement_id.currency_id", store=True, readonly=True,
    )
    agreement_state = fields.Selection(
        related="agreement_id.state", store=True, readonly=True, index=True,
    )

    # ------------------------------------------------------------------
    # TARGETING. Exactly one tier per rule.
    # ------------------------------------------------------------------
    service_id = fields.Many2one(
        "hospital.billing.service",
        ondelete="restrict",
        index=True,
        string="Service",
        help="Targets ONE service. Beats a category rule for the same service.",
    )
    service_type = fields.Selection(
        # Not restated: read off the service model so the two vocabularies
        # cannot drift. A value added there is targetable here immediately.
        selection=lambda self: self.env["hospital.billing.service"]
        ._fields["service_type"].selection,
        index=True,
        string="Service Category",
        help="Targets every service of this category. Overridden by a rule "
        "naming a specific service.",
    )

    coverage_type = fields.Selection(
        COVERAGE_TYPES,
        required=True,
        default="percentage",
        help="How the sponsor's eligible share is derived from the charge.",
    )

    # Commercial terms: same read protection as the agreement's own ceiling.
    # A Cashier or Front Desk Nurse holding a read ACL on this model would
    # otherwise see contract rates, because ir.rule filters rows, never columns.
    coverage_percent = fields.Float(
        string="Coverage (%)",
        digits=(5, 2),
        groups=PAYER_COMMERCIAL_READ,
        help="Percentage of the charge the sponsor carries. 0-100.",
    )
    sponsor_amount = fields.Monetary(
        currency_field="currency_id",
        groups=PAYER_COMMERCIAL_READ,
        help="Flat sponsor contribution. Capped at the charge amount -- a "
        "contribution larger than the charge pays the patient, which is not a "
        "benefit.",
    )
    patient_copay_amount = fields.Monetary(
        string="Patient Copay",
        currency_field="currency_id",
        groups=PAYER_COMMERCIAL_READ,
        help="Flat amount the patient always pays; the sponsor carries the "
        "remainder. Floored at zero when the charge is smaller than the copay.",
    )

    authorization_required = fields.Boolean(
        help="The evaluator REPORTS this. It does not enforce it in this slice: "
        "acting on it belongs to the Insurance/Credit workflow.",
    )

    notes = fields.Text(groups=PAYER_COMMERCIAL_READ)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "benefit_rule_percent_range",
            "CHECK(coverage_percent >= 0 AND coverage_percent <= 100)",
            "Coverage percentage must be between 0 and 100.",
        ),
        (
            "benefit_rule_sponsor_amount_non_negative",
            "CHECK(sponsor_amount >= 0)",
            "A sponsor amount cannot be negative.",
        ),
        (
            "benefit_rule_copay_non_negative",
            "CHECK(patient_copay_amount >= 0)",
            "A patient copay cannot be negative.",
        ),
    ]

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    @api.depends("service_id", "service_type", "coverage_type")
    def _compute_display_name(self):
        labels = dict(COVERAGE_TYPES)
        for rule in self:
            target = rule.service_id.display_name or (
                dict(
                    self.env["hospital.billing.service"]
                    ._fields["service_type"].selection
                ).get(rule.service_type)
                if rule.service_type
                else "Unmatched"
            )
            rule.display_name = "%s: %s" % (target, labels.get(rule.coverage_type, ""))

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    # agreement_id is in the trigger list on purpose, and it is load-bearing.
    # Odoo validates only the fields PRESENT in create() vals, so a rule created
    # with neither service_id nor service_type would never fire a constraint
    # listing only those two -- the empty-target case would slip through exactly
    # when it matters most. agreement_id is required, so it is always present.
    @api.constrains("service_id", "service_type", "agreement_id")
    def _check_exactly_one_target(self):
        """A rule targets a service OR a category, never both and never neither.

        Allowing both would make the precedence question unanswerable: the row
        would sit in two tiers at once and the winner would depend on which
        query found it first.
        """
        for rule in self:
            if rule.service_id and rule.service_type:
                raise ValidationError(
                    "Benefit rule on %s targets both service '%s' and category "
                    "'%s'. Choose one: a specific service rule already overrides "
                    "its category."
                    % (
                        rule.agreement_id.display_name,
                        rule.service_id.display_name,
                        rule.service_type,
                    )
                )
            if not rule.service_id and not rule.service_type:
                raise ValidationError(
                    "Benefit rule on %s targets nothing. Choose a service or a "
                    "service category." % rule.agreement_id.display_name
                )

    @api.constrains("service_id", "service_type", "agreement_id", "active")
    def _check_no_ambiguous_duplicate(self):
        """Two ACTIVE rules on the same tier for the same target is ambiguous.

        Prevented at source rather than resolved by sequence, because a
        duplicate is almost always a data-entry mistake and silently applying
        the lower sequence hides it. Archived rules are exempt: history stays.
        """
        for rule in self.filtered("active"):
            domain = [
                ("agreement_id", "=", rule.agreement_id.id),
                ("id", "!=", rule.id),
                ("active", "=", True),
            ]
            if rule.service_id:
                domain.append(("service_id", "=", rule.service_id.id))
                label = rule.service_id.display_name
            else:
                domain.append(("service_type", "=", rule.service_type))
                label = rule.service_type
            if self.sudo().search_count(domain):
                raise ValidationError(
                    "Agreement %s already has an active benefit rule for '%s'. "
                    "Edit or archive the existing rule instead of adding a "
                    "second one." % (rule.agreement_id.display_name, label)
                )

    @api.constrains("coverage_type", "coverage_percent")
    def _check_percentage(self):
        for rule in self.sudo():
            if rule.coverage_type != "percentage":
                continue
            if not 0.0 <= rule.coverage_percent <= 100.0:
                raise ValidationError(
                    "Benefit rule on %s: coverage percentage must be between 0 "
                    "and 100." % rule.agreement_id.display_name
                )

    @api.constrains("coverage_type", "sponsor_amount", "patient_copay_amount")
    def _check_amounts(self):
        for rule in self.sudo():
            if rule.coverage_type == "fixed_sponsor" and (
                rule.sponsor_amount < -AMOUNT_TOLERANCE
            ):
                raise ValidationError(
                    "Benefit rule on %s: a sponsor amount cannot be negative."
                    % rule.agreement_id.display_name
                )
            if rule.coverage_type == "patient_copay" and (
                rule.patient_copay_amount < -AMOUNT_TOLERANCE
            ):
                raise ValidationError(
                    "Benefit rule on %s: a patient copay cannot be negative."
                    % rule.agreement_id.display_name
                )

    @api.constrains("service_id", "company_id")
    def _check_company(self):
        for rule in self:
            service_company = rule.service_id.company_id
            if service_company and rule.company_id and service_company != rule.company_id:
                raise ValidationError(
                    "Benefit rule on %s targets service '%s' from company %s, but "
                    "the agreement belongs to %s."
                    % (
                        rule.agreement_id.display_name,
                        rule.service_id.display_name,
                        service_company.display_name,
                        rule.company_id.display_name,
                    )
                )

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------
    @api.model
    def _assert_authority(self, action):
        if self.env.su:
            return
        if not any(
            self.env.user.has_group(group) for group in BENEFIT_RULE_AUTHORITY
        ):
            raise AccessError(
                "You are not authorized to %s. Benefit rules decide how much of a "
                "charge a sponsor carries, so they are restricted to the "
                "Insurance/Credit Officer, Accountant, Hospital Manager and "
                "System Administrator roles." % action
            )

    # ------------------------------------------------------------------
    # THE VERSION FREEZE, ENFORCED HERE AND NOT ON THE AGREEMENT.
    #
    # hospital.payer.agreement.write() freezes commercial terms by intersecting
    # vals with FROZEN_TERM_FIELDS. That works only for terms that are COLUMNS
    # ON THE AGREEMENT. A benefit rule is a row on a different model, so writing
    # one never calls the parent's write() and the column allowlist never runs.
    # Manual UAT proved it: AMG-2026-001 was active and its consultation
    # coverage could still be edited from 80%.
    #
    # The boundary therefore has to live on the object being written, which is
    # the same conclusion encounter_payer.py reached about its own guard. The
    # parent's STATE is the authority; this model asks it on every path.
    #
    # The freeze is TOTAL rather than per-field. Every column here is a
    # commercial term or decides which term applies: coverage_type, the three
    # amounts, both targeting columns, authorization_required, active (a
    # disabled rule silently changes what a charge is covered by) and sequence
    # (the _order is agreement_id, sequence, id, and match_benefit_rule takes
    # [:1] -- so sequence IS precedence). There is no field left that would be
    # safe to edit in place, so listing exceptions would only invite one.
    # ------------------------------------------------------------------
    def _assert_agreement_amendable(self, action, agreement=None):
        agreement = agreement if agreement is not None else self.agreement_id
        for target in agreement:
            if target.state == "draft":
                continue
            raise UserError(
                "Agreement %s is %s, so its benefit rules are frozen and cannot "
                "be %s.\n\n"
                "Coverage terms are part of the contract: changing them after "
                "activation would rewrite what a sponsor agreed to for visits "
                "that already happened. Use 'Create Amendment' to issue a new "
                "version, which copies these rules and leaves every historical "
                "visit under the terms that applied at the time."
                % (target.display_name, target.state, action)
            )

    @api.model_create_multi
    def create(self, vals_list):
        self._assert_authority("create a benefit rule")
        # Read the TARGET agreement out of vals: self is empty on create, so
        # there is no record whose parent could be inspected instead.
        agreement_ids = {
            vals.get("agreement_id") for vals in vals_list if vals.get("agreement_id")
        }
        if agreement_ids:
            self._assert_agreement_amendable(
                "added",
                self.env["hospital.payer.agreement"].sudo().browse(sorted(agreement_ids)),
            )
        return super().create(vals_list)

    def write(self, vals):
        self._assert_authority("change a benefit rule")
        self._assert_agreement_amendable("changed")
        if "agreement_id" in vals:
            # Reassignment is two operations: it removes a term from one
            # contract and adds it to another. Both ends must be draft.
            self._assert_agreement_amendable(
                "reassigned to",
                self.env["hospital.payer.agreement"].sudo().browse(vals["agreement_id"]),
            )
        return super().write(vals)

    def unlink(self):
        self._assert_authority("delete a benefit rule")
        self._assert_agreement_amendable("removed")
        return super().unlink()

    # ------------------------------------------------------------------
    # Evaluation primitive
    # ------------------------------------------------------------------
    def _sponsor_eligible_for(self, charge_amount):
        """The sponsor share this rule permits for a charge of ``charge_amount``.

        Pure arithmetic on ONE rule. It caps at the charge amount and floors at
        zero, so no caller can produce a sponsor share larger than the charge or
        a negative one -- the two invariants that would corrupt the patient
        residual, which is derived as charge minus sponsor.

        The remaining limit cap is applied by the evaluator, not here: a rule
        knows its own terms and nothing about consumption.
        """
        self.ensure_one()
        amount = max(0.0, charge_amount or 0.0)
        rule = self.sudo()  # commercial terms are group-protected; compare only

        if rule.coverage_type == "excluded":
            return 0.0
        if rule.coverage_type == "percentage":
            eligible = amount * (rule.coverage_percent or 0.0) / 100.0
        elif rule.coverage_type == "fixed_sponsor":
            eligible = rule.sponsor_amount or 0.0
        elif rule.coverage_type == "patient_copay":
            eligible = amount - (rule.patient_copay_amount or 0.0)
        else:
            eligible = 0.0

        return max(0.0, min(eligible, amount))
