"""Slice A: payer benefit rules. Additive, and deliberately inert on upgrade.

WHAT THIS MIGRATION DOES: almost nothing, on purpose.

The benefit engine only changes behaviour for an agreement that has benefit
rules or a non-default coverage policy. Every agreement that predates this
module has neither, so the correct post-upgrade behaviour is EXACTLY what it did
yesterday: no automatic sponsor coverage, every share decided manually by an
Insurance/Credit Officer.

That is guaranteed by the column default on
hospital.payer.agreement.default_coverage_policy ('manual_authorization'), which
Odoo stamps onto every existing row when it creates the column. The UPDATE below
is a belt-and-braces guard for any row a default somehow missed, and is a no-op
on a healthy upgrade.

WHAT IS DELIBERATELY NOT DONE HERE
----------------------------------
  * No benefit rules are created. A rule is a commercial term; inventing one
    during an upgrade would grant coverage nobody negotiated.
  * No agreement is re-activated. Lifting the bounded-limit phase gate for
    'member' and 'visit' changes what MAY be activated from now on; it does not
    retroactively activate contracts a manager previously could not bring into
    force. Those stay in draft until someone decides.
  * No existing hospital.charge.responsibility row is touched. Authorized
    sponsor shares are frozen historical facts and are not re-evaluated against
    rules that did not exist when they were authorized.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: column defaults apply from the start.
        return

    cr.execute(
        """
        UPDATE hospital_payer_agreement
           SET default_coverage_policy = 'manual_authorization'
         WHERE default_coverage_policy IS NULL
        """
    )
    if cr.rowcount:
        _logger.info(
            "hospital_billing 18.0.1.11.0: defaulted default_coverage_policy to "
            "'manual_authorization' on %d agreement(s).",
            cr.rowcount,
        )

    cr.execute(
        """
        UPDATE hospital_payer_agreement
           SET benefit_period = 'agreement_term'
         WHERE benefit_period IS NULL
        """
    )
    if cr.rowcount:
        _logger.info(
            "hospital_billing 18.0.1.11.0: defaulted benefit_period to "
            "'agreement_term' on %d agreement(s).",
            cr.rowcount,
        )

    # Report, do not repair. An agreement sitting in a bounded scope was
    # un-activatable before this version; 'member' and 'visit' can now be
    # brought into force, but that is a manager's decision and not a
    # migration's.
    cr.execute(
        """
        SELECT limit_scope, count(*)
          FROM hospital_payer_agreement
         WHERE limit_scope IN ('member', 'visit')
           AND state = 'draft'
      GROUP BY limit_scope
        """
    )
    for scope, count in cr.fetchall():
        _logger.info(
            "hospital_billing 18.0.1.11.0: %d draft agreement(s) with limit "
            "scope '%s' may now be activated. None was activated automatically.",
            count,
            scope,
        )
