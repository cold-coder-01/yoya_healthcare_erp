"""Task 31C — extensible provenance / leakage / surgery / migration tests.

TransactionCase rolls every test back automatically, so nothing persists. Tests
use existing (cloned UAT) records and skip gracefully when a fixture is absent,
so they are safe to run on the disposable CI clone.
"""

import importlib.util
import os

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "hospital_31c")
class TestProvenance31C(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Consumption = cls.env["hospital.stock.consumption"]
        cls.Patient = cls.env["hospital.patient"]
        cls.Lab = cls.env["hospital.laboratory.request"]

    def _lab_for(self, patient):
        return self.Lab.search([("patient_id", "=", patient.id)], limit=1)

    def _two_patients_with_labs(self):
        for pa in self.Patient.search([], limit=20):
            la = self._lab_for(pa)
            if not la:
                continue
            for pb in self.Patient.search([("id", "!=", pa.id)], limit=20):
                lb = self._lab_for(pb)
                if lb:
                    return pa, la, pb, lb
        self.skipTest("need two patients each with a lab request (cloned UAT data)")

    # ------------------------------------------------------------------
    # 1. Extensible ownership hooks
    # ------------------------------------------------------------------
    def test_hooks_register_surgery(self):
        model = self.Consumption
        self.assertIn("surgery_id", model._source_field_names())
        smap = model._source_type_map()
        self.assertIn("surgery", smap)
        self.assertEqual(smap["surgery"]["allowed"], {"surgery_id", "admission_id"})
        self.assertEqual(smap["surgery"]["context"], {"admission_id"})
        # surgery_id is a provenance field now that it is registered.
        self.assertIn("surgery_id", model._provenance_field_names())

    # ------------------------------------------------------------------
    # 2. Forged cross-patient blocked (constraint, sudo cannot bypass)
    # ------------------------------------------------------------------
    def test_cross_patient_blocked_even_sudo(self):
        pa, la, pb, lb = self._two_patients_with_labs()
        with self.assertRaises(ValidationError):
            self.Consumption.sudo().create({
                "consumption_type": "laboratory",
                "patient_id": pa.id,
                "lab_request_id": lb.id,
            })

    # ------------------------------------------------------------------
    # 3. Stale default_* context does NOT leak into a created consumption
    # ------------------------------------------------------------------
    def test_default_context_leakage_blocked(self):
        pa, la, pb, lb = self._two_patients_with_labs()
        # Forge stale defaults for foreign/irrelevant sources, incl. cross-patient.
        polluted = self.Consumption.with_context(
            default_lab_request_id=lb.id,          # cross-patient, wrong type
            default_procedure_id=999999,           # stale garbage
            default_radiology_request_id=888888,
        )
        cons = polluted.create_for_source(
            consumption_type="laboratory",
            source_vals={"patient_id": pa.id, "lab_request_id": la.id},
        )
        # Only the intended source survives; every sibling is empty.
        self.assertEqual(cons.lab_request_id, la)
        self.assertFalse(cons.procedure_id)
        self.assertFalse(cons.radiology_request_id)
        self.assertFalse(cons.pharmacy_dispense_id)
        self.assertTrue(cons.is_source_generated)

    # ------------------------------------------------------------------
    # 4. Surgery integration: valid + source-locked
    # ------------------------------------------------------------------
    def test_surgery_consumption_valid_and_locked(self):
        Surgery = self.env["hospital.surgery.request"]
        surgery = Surgery.search([("patient_id", "!=", False)], limit=1)
        if not surgery:
            self.skipTest("no surgery request available")
        cons = self.Consumption.create_for_source(
            consumption_type="surgery",
            source_vals={
                "patient_id": surgery.patient_id.id,
                "surgery_id": surgery.id,
                "admission_id": surgery.admission_id.id if surgery.admission_id else False,
            },
        )
        self.assertEqual(cons.consumption_type, "surgery")
        self.assertEqual(cons.surgery_id, surgery)
        self.assertTrue(cons.is_source_generated)
        # No sibling leaked.
        self.assertFalse(cons.lab_request_id)
        self.assertFalse(cons.procedure_id)
        # Provenance is frozen even in Draft: clearing / re-pointing / hiding blocked.
        with self.assertRaises(UserError):
            cons.write({"surgery_id": False})
        with self.assertRaises(UserError):
            cons.write({"consumption_type": "manual"})
        other_patient = self.Patient.search([("id", "!=", surgery.patient_id.id)], limit=1)
        if other_patient:
            with self.assertRaises(UserError):
                cons.write({"patient_id": other_patient.id})

    # ------------------------------------------------------------------
    # 5. Migration idempotency: re-running remediation is a no-op
    # ------------------------------------------------------------------
    def _load_migration(self, module_dir):
        path = os.path.join(
            module_dir, "migrations", "18.0.1.1.0", "post-migration.py"
        )
        spec = importlib.util.spec_from_file_location("mig_31c_%s" % id(path), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_migration_idempotent(self):
        target = self.Consumption.search([("name", "=", "STCON00021")])
        if not target:
            self.skipTest("legacy record STCON00021 not present")
        Archive = self.env["hospital.inventory.provenance.archive"]
        # After the upgrade this record is already remediated.
        self.assertFalse(target.lab_request_id, "expected already-remediated state")
        self.assertTrue(target.pharmacy_dispense_id, "primary must be retained")
        snaps_before = Archive.search_count([])

        import odoo.addons.hospital_inventory as inv_pkg
        inv_dir = os.path.dirname(inv_pkg.__file__)
        mig = self._load_migration(inv_dir)
        mig.migrate(self.env.cr, "18.0.1.0.0")  # second run

        # No change, no duplicate snapshots.
        target.invalidate_recordset()
        self.assertFalse(target.lab_request_id)
        self.assertTrue(target.pharmacy_dispense_id)
        self.assertEqual(Archive.search_count([]), snaps_before)
