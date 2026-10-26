import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/unbilled -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "unbilled", "Unable to load the unbilled delivered value.");
}
