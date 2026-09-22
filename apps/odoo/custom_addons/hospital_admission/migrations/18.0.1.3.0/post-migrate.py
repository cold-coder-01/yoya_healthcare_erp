"""Admissions Slice 3: backfill stay rate snapshots for existing stays.

Every admission confirmed, and every transfer made, from this version on
records the rate in force as its stay segment opens. Rows that already existed
have no such record. Without one they would be priced from the live catalogue
on every read -- the retroactive-repricing defect this version fixes -- and
flagged `rate_snapshot_missing` forever.

WHAT IS FROZEN, AND WHY IT IS HONEST
------------------------------------
The catalogue rate in force NOW, at upgrade time, resolved by the same
precedence the legacy bill used (bed > room > ward). That is exactly the
figure the pre-upgrade code would have billed, so no existing stay changes
price at the moment of upgrade; it simply stops changing afterwards. Each row
is labelled rate_snapshot_origin = 'upgrade_backfill' so nobody mistakes it for
a rate recorded when the patient moved.

Only stays that STARTED are touched (admitted, transferred, discharged). Draft
and cancelled rows have no segment to price.

Raw SQL on purpose: the model guards refuse any write to a snapshot or to
transfer history outside the workflow, and an upgrade is not the workflow.
Nothing but the snapshot columns is written.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.hospital_admission.models.admission_authority import (
    resolve_location_rate,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Admission = env["hospital.admission"].with_context(active_test=False)
    Transfer = env["hospital.admission.transfer"]

    admissions = Admission.search(
        [
            ("state", "in", ["admitted", "transferred", "discharged"]),
            ("rate_snapshot_taken", "=", False),
        ]
    )
    for admission in admissions:
        first = Transfer.search(
            [("admission_id", "=", admission.id)], order="transfer_date asc, id asc", limit=1
        )
        if first:
            ward, room, bed = first.from_ward_id, first.from_room_id, first.from_bed_id
        else:
            ward, room, bed = admission.ward_id, admission.room_id, admission.bed_id
        daily_rate, basis = resolve_location_rate(ward, room, bed)
        cr.execute(
            """
            UPDATE hospital_admission
               SET rate_snapshot_taken = TRUE,
                   rate_snapshot_origin = 'upgrade_backfill',
                   rate_snapshot_basis = %s,
                   rate_snapshot_daily_rate = %s,
                   rate_snapshot_admission_fee = %s
             WHERE id = %s
            """,
            (basis, daily_rate, ward.admission_fee if ward else 0.0, admission.id),
        )

    transfers = Transfer.search([("rate_snapshot_taken", "=", False)])
    for transfer in transfers:
        daily_rate, basis = resolve_location_rate(
            transfer.to_ward_id, transfer.to_room_id, transfer.to_bed_id
        )
        cr.execute(
            """
            UPDATE hospital_admission_transfer
               SET rate_snapshot_taken = TRUE,
                   rate_snapshot_origin = 'upgrade_backfill',
                   to_rate_basis = %s,
                   to_daily_rate = %s
             WHERE id = %s
            """,
            (basis, daily_rate, transfer.id),
        )

    env.invalidate_all()
    _logger.info(
        "hospital_admission 18.0.1.3.0: backfilled stay rate snapshots on %s admission(s) "
        "and %s transfer(s) from the catalogue in force at upgrade.",
        len(admissions),
        len(transfers),
    )
