from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HospitalPatientConsent(models.Model):
    _name = "hospital.patient.consent"
    _description = "Patient Consent"
    _order = "date_given desc, consent_code desc"

    consent_code = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        tracking=True,
        ondelete="restrict",
    )
    consent_type = fields.Selection(
        [
            ("general_treatment", "General Treatment"),
            ("surgery", "Surgery"),
            ("laboratory_test", "Laboratory Test"),
            ("radiology", "Radiology"),
            ("data_sharing", "Data Sharing"),
            ("insurance_processing", "Insurance Processing"),
            ("research", "Research"),
        ],
        required=True,
        tracking=True,
    )
    purpose = fields.Text(
        required=True,
        help="Explain why the consent is collected and how it will be used.",
    )
    date_given = fields.Date(default=fields.Date.context_today, tracking=True)
    expiry_date = fields.Date(tracking=True)
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("expired", "Expired"),
            ("withdrawn", "Withdrawn"),
        ],
        required=True,
        default="draft",
        tracking=True,
    )
    given_by = fields.Char(
        help="Name of patient, guardian, or legal representative who gave consent."
    )
    withdrawn_date = fields.Date(readonly=True, tracking=True)
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("consent_code", "patient_id", "consent_type")
    def _compute_display_name(self):
        consent_labels = dict(self._fields["consent_type"].selection)
        for consent in self:
            patient_name = consent.patient_id.display_name or "Patient"
            consent_type = consent_labels.get(consent.consent_type, "Consent")
            if consent.consent_code and consent.consent_code != "New":
                consent.display_name = (
                    f"{consent.consent_code} - {patient_name} - {consent_type}"
                )
            else:
                consent.display_name = f"{patient_name} - {consent_type}"

    @api.constrains("patient_id", "consent_type", "purpose", "date_given", "expiry_date")
    def _check_consent_values(self):
        for consent in self:
            if not consent.patient_id:
                raise ValidationError("Patient is required for consent.")
            if not consent.consent_type:
                raise ValidationError("Consent type is required.")
            if not consent.purpose:
                raise ValidationError("Consent purpose is required.")
            if (
                consent.expiry_date
                and consent.date_given
                and consent.expiry_date < consent.date_given
            ):
                raise ValidationError("Expiry date cannot be earlier than date given.")

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        today = fields.Date.context_today(self)
        for vals in vals_list:
            if not vals.get("consent_code") or vals.get("consent_code") == "New":
                vals["consent_code"] = sequence.next_by_code(
                    "hospital.patient.consent.sequence"
                ) or "New"
            if vals.get("status") == "withdrawn" and not vals.get("withdrawn_date"):
                vals["withdrawn_date"] = today
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("status") == "withdrawn" and not vals.get("withdrawn_date"):
            vals["withdrawn_date"] = fields.Date.context_today(self)
        return super().write(vals)

    def action_activate(self):
        consents = self.filtered(lambda record: record.status == "draft")
        consents.write({"status": "active"})
        consents._create_audit_log("Patient consent activated.")

    def action_withdraw(self):
        consents = self.filtered(lambda record: record.status in ("draft", "active"))
        consents.write(
            {
                "status": "withdrawn",
                "withdrawn_date": fields.Date.context_today(self),
            }
        )
        consents._create_audit_log("Patient consent withdrawn.")

    def action_mark_expired(self):
        consents = self.filtered(lambda record: record.status in ("draft", "active"))
        consents.write({"status": "expired"})
        consents._create_audit_log("Patient consent marked as expired.")

    def action_reset_to_draft(self):
        consents = self.filtered(lambda record: record.status in ("expired", "withdrawn"))
        consents.write(
            {
                "status": "draft",
                "withdrawn_date": False,
            }
        )
        consents._create_audit_log("Patient consent reset to draft.")

    def _create_audit_log(self, description):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for consent in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=consent.patient_id.id,
                model_name=consent._name,
                record_id=consent.id,
                action_type="consent_change",
                description=description,
                new_value=f"Status: {consent.status}",
            )
