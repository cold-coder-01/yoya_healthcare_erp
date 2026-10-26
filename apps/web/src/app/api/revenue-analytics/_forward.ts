import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import { forwardedQuery } from "@/lib/revenue-analytics-format";

/**
 * The Revenue Analytics BFF: a READ-ONLY pass-through.
 *
 * Every route is a GET that forwards a whitelisted query to
 * /yoya-emr/api/v1/revenue-analytics/<endpoint>. Odoo validates every filter
 * and decides every role -- Finance, Executive or refused (403
 * revenue_analytics_not_authorized). Nothing here computes a figure, and
 * there is no write route to forward.
 */
export async function forwardAnalytics(request: Request, endpoint: string, failureMessage: string) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const query = forwardedQuery(new URL(request.url).search);

  try {
    return await forwardOdooResult(
      await callOdooApi<unknown>(
        session.sessionId,
        `/yoya-emr/api/v1/revenue-analytics/${endpoint}${query}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(error, "revenue_analytics_failed", failureMessage);
  }
}
