import { forwardAnalytics } from "../_forward";

/** GET /yoya-emr/api/v1/revenue-analytics/data-quality -- read-only pass-through. */
export async function GET(request: Request) {
  return forwardAnalytics(request, "data-quality", "Unable to load the data-quality checks.");
}
