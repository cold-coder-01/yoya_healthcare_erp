from . import res_users
from . import patient
from . import patient_evaluation
from . import consultation
# After consultation: both of these reference hospital.consultation.
from . import hospital_appointment
from . import patient_diagnosis
from . import laboratory_request
from . import radiology_request
from . import prescription
# Decision 3: an open consultation blocks discharge (hooks hospital_admission).
from . import admission_consultation_gate
