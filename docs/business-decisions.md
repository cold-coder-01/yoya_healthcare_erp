# Business decisions

Decisions agreed with the business that shape how YOYA modules behave or how
data is handled. Each entry records the date it was agreed. When a decision
changes, add a new entry that supersedes the old one instead of editing it.

| # | Date | Area | Decision |
|---|---|---|---|
| 1 | 2026-11-13 | Reception / billing | Patient card fees are not refundable once the card is issued. A payment error made before the card is issued may be corrected. |
| 2 | 2026-11-13 | Billing / accounting | Repairs of legacy card charges post on the repair date. The original issue date is kept for reference. |
| 3 | 2026-11-13 | Inpatient | When discharge is blocked by an open consultation, the Admissions Desk shows which doctor holds that consultation. |
| 4 | 2026-11-13 | Laboratory / data migration | 166 stale July 2026 lab requests (pre-go-live UAT cleanup): requested requests with no result, money or delivery are cancelled; requests with validated results are released to completion; the rest are left for the lab team. Done through audited server actions. Status: approach agreed, execution deferred. |
| 5 | 2026-11-13 | Billing / accounting | Self-pay overpayment is allowed. The excess becomes a patient advance (a liability), never revenue. |
| 6 | 2026-11-13 | Radiology | The radiologist reports, validates and releases the result. The requesting doctor acknowledges the released result. |
| 7 | 2026-11-13 | Accounting | Registration and card income goes to a dedicated account, 401108 Registration & Card Revenue. Registration and nursing each get their own accounting-mapping category. |
| 8 | 2026-11-13 | Doctor Desk | Doctor Desk Inpatients lane lists admissions whose visit is booked to the doctor; managers see all. |
| 9 | 2026-11-13 | Doctor Desk | Known limitation: an admitting physician who is not the visit's booked doctor does not see the patient on the Doctor Desk; a covering-doctor model is deferred. Until then a manager reassigns the booked doctor or acts directly. |
