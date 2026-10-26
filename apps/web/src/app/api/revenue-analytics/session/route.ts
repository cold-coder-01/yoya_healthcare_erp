import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/session -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "session", "Unable to load the analytics session.");
}
