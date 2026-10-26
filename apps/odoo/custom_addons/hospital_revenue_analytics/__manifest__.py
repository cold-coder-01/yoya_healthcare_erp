{
    "name": "Hospital Revenue Analytics",
    "summary": "Read-only Finance and Executive revenue intelligence built on the "
    "hospital's posted invoice provenance",
    "description": """
Hospital Revenue Analytics
==========================
A READ-ONLY reporting layer. It computes nothing the accounting bridge does not
already assert and it writes to no transactional model.

* Recognized revenue is the posted GL revenue of hospital customer invoices and
  credit notes, split back onto the charges that produced it through
  hospital.charge.invoice.allocation (the invoice provenance).
* Delivered Value is the operational value of delivered, billable care, dated
  by when the care was delivered. It is NOT revenue.
* Cash received, patient advance refunds and unbilled delivered value are
  reported beside revenue and never netted into it.
* Revenue posted to the configured revenue accounts WITHOUT charge provenance
  (legacy patient bills, manual journals) is reconciled and reported as
  unattributed; it is never assigned to a doctor, department or service.

V1 headline scope is SELF-PAY recognized revenue. Payer / sponsored revenue is
identified and counted, never mixed into the headline.
""",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "YOYA Healthcare",
    "license": "LGPL-3",
    "depends": [
        # The invoice provenance, the advance ledger and the accounting
        # configuration (revenue accounts) this module reads.
        "hospital_billing_accounting",
        # Inpatient accounting operations (posted advance refunds).
        "hospital_admission_accounting",
        # hospital.consultation: the consulting physician and completion stamp.
        "yoya_clinical_bridge",
        # The registration / card service type, reported as excluded scope.
        "yoya_reception_bridge",
        # The JSON envelope and route conventions of the EMR API.
        "yoya_emr_api",
    ],
    "data": [
        "security/revenue_analytics_security.xml",
        "security/ir.model.access.csv",
    ],
    "uninstall_hook": "uninstall_hook",
    "application": False,
    "installable": True,
    "auto_install": False,
}
