import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/trend -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "trend", "Unable to load the revenue trend.");
}
