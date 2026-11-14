"""Indexes behind the Doctor Desk's dateless reads.

The Inpatients lane and the "search all my visits" mode read across every date,
so they lean on two access paths no module indexed:

  hospital_appointment (doctor_id, appointment_date DESC)
      a doctor's visits, newest first -- the dateless search and the dated
      worklist both filter on the doctor and order or bound by the visit date.
  hospital_admission (physician_id, appointment_id)
      the doctor admission rule and the Inpatients lane join an admission to
      its admitting physician and its visit.

Created here, with IF-NOT-EXISTS semantics (tools.create_index), so upgrading
yoya_emr_api is enough: the owning modules' field definitions stay untouched.
"""

from odoo import models, tools


class HospitalAppointmentDoctorDeskIndex(models.Model):
    _inherit = "hospital.appointment"

    def init(self):
        super().init()
        tools.create_index(
            self.env.cr,
            "hospital_appointment_doctor_date_idx",
            self._table,
            ["doctor_id", "appointment_date DESC"],
        )


class HospitalAdmissionDoctorDeskIndex(models.Model):
    _inherit = "hospital.admission"

    def init(self):
        super().init()
        tools.create_index(
            self.env.cr,
            "hospital_admission_physician_appointment_idx",
            self._table,
            ["physician_id", "appointment_id"],
        )
