"""Registration/card service configuration.

Mirrors the consultation resolver in hospital_billing exactly: explicit
configuration wins, an unambiguous single candidate is accepted, and anything
else raises rather than guessing. A mis-billed registration card is a cash
error at the counter, so guessing is never acceptable.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalBillingService(models.Model):
    _inherit = "hospital.billing.service"

    service_type = fields.Selection(
        selection_add=[("registration", "Registration / Patient Card")],
        # Non-destructive: uninstalling this module reclassifies registration
        # services as 'other' rather than deleting priced configuration.
        ondelete={"registration": lambda records: records.write({"service_type": "other"})},
    )

    is_default_card_service = fields.Boolean(
        string="Default Patient Card Service",
        help="Used by the reception workflow to charge a new patient card. "
        "At most one per company.",
    )

    @api.constrains("is_default_card_service", "service_type", "company_id", "active")
    def _check_default_card_service(self):
        for service in self:
            if not service.is_default_card_service:
                continue
            if service.service_type != "registration":
                raise ValidationError(
                    "Service %s: only a registration-type service can be the default "
                    "patient card service." % service.name
                )
            clash = self.search(
                [
                    ("is_default_card_service", "=", True),
                    ("service_type", "=", "registration"),
                    ("company_id", "=", service.company_id.id),
                    ("id", "!=", service.id),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    "'%s' is already the default patient card service for this company. "
                    "Only one default is allowed." % clash.display_name
                )

    @api.model
    def get_default_card_service(self, company=None):
        """Resolve the registration service to charge. Never guesses.

        Same contract as get_default_consultation_service: raise on zero, raise
        on ambiguity, accept a single unambiguous candidate.
        """
        company = company or self.env.company
        domain = [
            ("service_type", "=", "registration"),
            ("company_id", "in", [company.id, False]),
        ]

        flagged = self.search(domain + [("is_default_card_service", "=", True)], limit=2)
        if len(flagged) == 1:
            return flagged
        if len(flagged) > 1:
            raise UserError(
                "More than one default patient card service is configured. "
                "Exactly one must be flagged as the default."
            )

        candidates = self.search(domain, limit=2)
        if len(candidates) == 1:
            return candidates
        if not candidates:
            raise UserError(
                "No patient card billing service is configured. Create a service with "
                "type 'Registration / Patient Card' and flag it as the default patient "
                "card service."
            )
        raise UserError(
            "Several registration services exist and none is flagged as the default. "
            "Flag exactly one so patient cards are billed deterministically."
        )
