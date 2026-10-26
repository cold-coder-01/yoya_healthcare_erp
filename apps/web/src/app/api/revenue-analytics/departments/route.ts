import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/departments -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "departments", "Unable to load department performance.");
}
