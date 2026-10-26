import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/summary -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "summary", "Unable to load the executive summary.");
}
