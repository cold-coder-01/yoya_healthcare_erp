import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/services -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "services", "Unable to load service performance.");
}
