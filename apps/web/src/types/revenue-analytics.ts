/**
 * Revenue Analytics payloads (hospital_revenue_analytics controllers).
 *
 * Every figure is the server's. The browser renders, formats and never
 * computes a revenue, a share or an attribution.
 */

export type AnalyticsAccessLevel = "finance" | "executive" | null;

export type AnalyticsCapabilities = {
  analytics: boolean;
  finance: boolean;
  executive: boolean;
  data_quality: boolean;
  unbilled_detail: boolean;
  patient_drilldown: boolean;
};

export type AnalyticsCategoryKey =
  | "consultation"
  | "laboratory"
  | "radiology"
  | "pharmacy"
  | "admission_fee"
  | "bed_stay"
  | "procedure"
  | "other";

export type AnalyticsSession = {
  user: { id: number; name: string; login: string };
  company: { id: number; name: string };
  access_level: AnalyticsAccessLevel;
  capabilities: AnalyticsCapabilities;
  companies: { id: number; name: string }[];
  default_company: number | null;
  timezone: string;
  default_range: { date_from: string; date_to: string };
  categories: { key: AnalyticsCategoryKey; label: string }[];
};

export type AnalyticsBasis = "recognized" | "delivered";

export type AnalyticsScope = {
  company: { id: number; name: string };
  currency: string;
  date_from: string;
  date_to: string;
  timezone: string;
  basis: AnalyticsBasis;
  payer_scope: "self_pay";
  payer_scope_label: string;
  filters: {
    doctor: number | null;
    department: number | "unassigned" | null;
    category: AnalyticsCategoryKey | null;
  };
};

export type TrendGrain = "day" | "week" | "month";

export type TrendPoint = { bucket: string; recognized: number; delivered: number };

export type AnalyticsTrend = { scope?: AnalyticsScope; grain: TrendGrain; points: TrendPoint[] };

export type TopEntry = { id: number; name: string; value: number } | null;

export type AnalyticsSummary = {
  scope: AnalyticsScope;
  access_level: AnalyticsAccessLevel;
  revenue: { gross: number; credits: number; net: number };
  patients: {
    unique: number;
    avg_revenue_per_patient: number;
    encounters: number;
    admissions: number;
    completed_consultations: number;
    consulted_patients: number;
  };
  delivered_value: number;
  unbilled: { value: number; charges: number; as_of: string };
  top: {
    doctor_by_patients: TopEntry;
    doctor_by_direct_revenue: TopEntry;
    doctor_by_episode_revenue: TopEntry;
    department: TopEntry;
    category: { key: AnalyticsCategoryKey; name: string; value: number } | null;
  };
  indicators: {
    credit_notes: number;
    credit_note_rate: number;
    cash_received: number;
    receipts: number;
    advance_refunds: number;
    advance_refund_count: number;
    advance_refund_rate: number;
  };
  excluded_scope: { sponsored_revenue: number; sponsored_encounters: number };
  trend: AnalyticsTrend;
};

export type DoctorRow = {
  doctor_id: number | null;
  doctor_name: string;
  department_name: string | null;
  patients_treated: number;
  completed_consultations: number;
  direct_revenue: number;
  episode_revenue: number;
  episode_patients: number;
  avg_episode_revenue_per_patient: number;
};

export type DoctorSort = "patients" | "direct" | "episode";

export type AnalyticsDoctors = {
  scope: AnalyticsScope;
  sort: DoctorSort;
  rows: DoctorRow[];
  totals: { direct_revenue: number; episode_revenue: number };
};

export type ServiceRow = {
  category: AnalyticsCategoryKey;
  label: string;
  orders: number;
  /** Null where the category mixes units (pharmacy, other): never summed. */
  quantity: number | null;
  value: number;
  share: number;
  service_id?: number | null;
  category_label?: string;
  uom?: string | null;
};

export type ServiceGroup = "category" | "service";

export type AnalyticsServices = {
  scope: AnalyticsScope;
  group: ServiceGroup;
  rows: ServiceRow[];
  total: number;
};

export type DepartmentRow = {
  department_id: number | null;
  department_name: string;
  patients: number;
  value: number;
  avg_revenue_per_patient: number;
  share: number;
};

export type AnalyticsDepartments = { scope: AnalyticsScope; rows: DepartmentRow[]; total: number };

export type AgingBucket = "0_7" | "8_30" | "31_90" | "over_90";

export type AnalyticsUnbilled = {
  scope: AnalyticsScope;
  as_of: string;
  value: number;
  charges: number;
  awaiting_posting_value: number;
  oldest_delivered_on: string | null;
  by_category: {
    category: AnalyticsCategoryKey;
    label: string;
    charges: number;
    value: number;
    oldest_delivered_on: string | null;
  }[];
  aging: { bucket: AgingBucket; charges: number; value: number }[];
};

export type QualityFlag = { amount: number; count: number };

export type ChargeGap = { charges: number; funded: number; cash_held: number; delivered: number };

export type Reconciliation = {
  revenue_account_count: number;
  gl_revenue_total: number;
  attributed_total: number;
  attributed_self_pay: number;
  attributed_sponsored: number;
  attributed_excluded_categories: number;
  unattributed_amount: number;
  unattributed_lines: number;
  unattributed_moves: number;
  difference: number;
};

export type LegacyAccounting = {
  discharged: number;
  scanned: number;
  truncated: boolean;
  visitless_legacy: number;
  legacy_pending: number;
  not_posted: number;
  posted: number;
  outside_scope: number;
  statuses: Record<string, number>;
};

export type AnalyticsDataQuality = {
  scope: AnalyticsScope;
  unbilled: { value: number; charges: number; awaiting_posting_value: number };
  missing_doctor: QualityFlag;
  admission_doctor_fallback: QualityFlag;
  doctor_mismatch: QualityFlag;
  missing_department: QualityFlag;
  ward_department_fallback: QualityFlag;
  sponsored_excluded: QualityFlag;
  excluded_categories_recognized: QualityFlag;
  registration_card: ChargeGap;
  consultation_gap: ChargeGap;
  legacy_manual_revenue: Reconciliation;
  legacy_accounting: LegacyAccounting;
};

export type AnalyticsFilters = {
  date_from: string;
  date_to: string;
  company: string;
  category: string;
  basis: AnalyticsBasis;
};
