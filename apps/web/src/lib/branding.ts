/**
 * Visible hospital branding, in one place.
 *
 * TEMPORARY DEMO BRANDING. The workstation shells normally show the Odoo
 * company name (res.company.name via /reception/session). For the demo the
 * display name is overridden here, in the UI only: no Odoo record, module,
 * route, API or technical identifier is renamed.
 *
 * To revert: set DEMO_HOSPITAL_NAME_OVERRIDE to null (shells go back to the
 * Odoo company name) and restore the YOYA strings below.
 */
export const DEMO_HOSPITAL_NAME_OVERRIDE: string | null = "AWASH GENERAL HOSPITAL";

/** Static hospital name for pages with no session (landing, login). */
export const HOSPITAL_DISPLAY_NAME = DEMO_HOSPITAL_NAME_OVERRIDE ?? "YOYA General Hospital";

/** Eyebrow on the login hero. */
export const CLINICAL_SYSTEM_DISPLAY_NAME = "AWASH CLINICAL SYSTEM";

/** Brand line for a workstation shell, given the session's company name. */
export function hospitalBrand(companyName: string | null | undefined): string {
  return DEMO_HOSPITAL_NAME_OVERRIDE ?? companyName ?? HOSPITAL_DISPLAY_NAME;
}

/** Logo on the landing and login pages (YoyaLogo). Revert: "/images/yoya-hospital-logo.png", "YOYA Hospital". */
export const HOSPITAL_LOGO_SRC = "/images/awash-hospital-logo.png";
export const HOSPITAL_LOGO_ALT = "Awash General Hospital";
