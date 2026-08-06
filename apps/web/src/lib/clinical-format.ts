/**
 * Display formatting for Odoo clinical values.
 *
 * TIMEZONE CONTRACT
 * Odoo stores datetimes as naive UTC and serialises them with
 * `value.isoformat()`, producing strings like "2026-08-05T15:00:00" with no
 * timezone designator. `new Date("2026-08-05T15:00:00")` parses a designator-
 * less date-TIME form as *local* time, so on any machine that is not UTC the
 * value silently shifts before it is ever formatted.
 *
 * Everything here therefore does two explicit things:
 *   1. attaches "Z" so the string is parsed as the UTC instant Odoo meant, and
 *   2. formats with an explicit `timeZone`, never the machine's.
 *
 * Nothing in this module mutates a value that is sent back to the API.
 */

export const HOSPITAL_TIME_ZONE = "Africa/Addis_Ababa";

/** Locale is pinned so output does not vary by machine (gives "6:00 PM"). */
const DISPLAY_LOCALE = "en-US";

const DATE_ONLY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
/** Trailing "Z", "+03:00" or "+0300" means the value already carries an offset. */
const HAS_TIME_ZONE_PATTERN = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/**
 * Parse an Odoo datetime string into the UTC instant it represents.
 * Returns null for empty or unparseable input.
 */
export function parseOdooDateTime(value: string | null | undefined): Date | null {
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }

  // Date-only values are calendar dates, not instants. Anchor them at UTC
  // midnight and render them in UTC so the day can never roll backwards.
  if (DATE_ONLY_PATTERN.test(trimmed)) {
    const dateOnly = new Date(`${trimmed}T00:00:00Z`);
    return Number.isNaN(dateOnly.getTime()) ? null : dateOnly;
  }

  const isoish = trimmed.includes("T") ? trimmed : trimmed.replace(" ", "T");
  // Only attach Z when there is no offset already, so a value is never
  // converted twice.
  const withZone = HAS_TIME_ZONE_PATTERN.test(isoish) ? isoish : `${isoish}Z`;

  const parsed = new Date(withZone);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function isDateOnly(value: string) {
  return DATE_ONLY_PATTERN.test(value.trim());
}

/** "Aug 05, 2026, 6:00 PM" in hospital time. */
export function formatHospitalDateTime(
  value: string | null | undefined,
  fallback = "-",
): string {
  const parsed = parseOdooDateTime(value);
  if (!parsed) {
    return fallback;
  }

  // A date-only value has no meaningful clock reading; render just the date.
  if (typeof value === "string" && isDateOnly(value)) {
    return formatHospitalDate(value, fallback);
  }

  return new Intl.DateTimeFormat(DISPLAY_LOCALE, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: HOSPITAL_TIME_ZONE,
  }).format(parsed);
}

/** "6:00 PM" in hospital time. */
export function formatHospitalTime(
  value: string | null | undefined,
  fallback = "-",
): string {
  const parsed = parseOdooDateTime(value);
  if (!parsed) {
    return fallback;
  }

  return new Intl.DateTimeFormat(DISPLAY_LOCALE, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: HOSPITAL_TIME_ZONE,
  }).format(parsed);
}

/** "Aug 05, 2026". Date-only input is rendered in UTC so the day never shifts. */
export function formatHospitalDate(
  value: string | null | undefined,
  fallback = "-",
): string {
  const parsed = parseOdooDateTime(value);
  if (!parsed) {
    return fallback;
  }

  const dateOnly = typeof value === "string" && isDateOnly(value);

  return new Intl.DateTimeFormat(DISPLAY_LOCALE, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: dateOnly ? "UTC" : HOSPITAL_TIME_ZONE,
  }).format(parsed);
}

/**
 * Today's date in hospital time as YYYY-MM-DD.
 *
 * `new Date().toISOString().slice(0, 10)` returns the UTC day, which is the
 * previous day in Addis Ababa between 00:00 and 03:00 local.
 */
export function hospitalToday(): string {
  // en-CA formats as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: HOSPITAL_TIME_ZONE,
  }).format(new Date());
}

const BLOOD_GROUP_LABELS: Record<string, string> = {
  a_positive: "A+",
  a_negative: "A-",
  b_positive: "B+",
  b_negative: "B-",
  ab_positive: "AB+",
  ab_negative: "AB-",
  o_positive: "O+",
  o_negative: "O-",
  unknown: "Unknown",
};

/**
 * Display label for hospital.patient.blood_group.
 * Presentation only; the stored selection key is never altered.
 */
export function formatBloodGroup(
  value: string | null | undefined,
  fallback = "-",
): string {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }

  const key = value.trim().toLowerCase();
  return BLOOD_GROUP_LABELS[key] ?? value.replaceAll("_", " ");
}
