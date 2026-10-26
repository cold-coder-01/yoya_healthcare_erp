import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/doctors -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "doctors", "Unable to load doctor performance.");
}
