"""Inpatient estimate revision history: the BASELINE row.

Until this version the estimate lived only on the admission (amount, reason,
doctor, time, revision number), overwritten by each revision. From now on
every revision is also an immutable hospital.admission.estimate.revision row.

WHAT THIS DOES
--------------
For each admission that carries an estimate (estimate_revision > 0) and has no
history row yet, it copies the CURRENTLY STORED estimate into one row, keeping
its own revision number, amount, reason, doctor and time, marked
is_baseline = TRUE. Re-running it inserts nothing twice.

WHAT THIS DOES NOT DO
--------------------
It does not reconstruct earlier revisions. Their reasons were never stored, so
any row made for them would be incomplete evidence manufactured after the
fact. The audit log keeps what it recorded and is not touched. Nothing on the
admission, its deposit charge or its receipts changes.

Plain SQL, deliberately: the model refuses every create outside the doctor's
estimate action, and a baseline is not an estimate action.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: no estimate has been given yet.
        return
    cr.execute(
        """
        INSERT INTO hospital_admission_estimate_revision
            (admission_id, revision, amount, reason, estimated_by_id, estimated_at,
             is_baseline, create_uid, create_date, write_uid, write_date)
        SELECT a.id, a.estimate_revision, COALESCE(a.estimated_amount, 0.0),
               a.estimate_reason, a.estimated_by_id, a.estimated_at,
               TRUE, 1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC'
          FROM hospital_admission a
         WHERE a.estimate_revision > 0
           AND NOT EXISTS (
               SELECT 1 FROM hospital_admission_estimate_revision r
                WHERE r.admission_id = a.id
           )
        RETURNING admission_id, revision
        """
    )
    rows = cr.fetchall()
    _logger.info(
        "Estimate history: %s baseline revision(s) recorded from stored estimates %s.",
        len(rows), sorted(rows),
    )
