import {
  formatAuthorizationState,
  formatChargeCategory,
  formatEtb,
  formatPaymentState,
} from "@/lib/reception-format";
import type { ReceptionChargeLine } from "@/types/reception";

const TH = "px-3 py-2 text-left font-semibold";
const TD = "px-3 py-2 align-top";
const NUM = "px-3 py-2 align-top text-right tabular-nums whitespace-nowrap";

export default function ChargeLinesTable({
  lines,
}: {
  lines: ReceptionChargeLine[];
}) {
  if (lines.length === 0) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500">
        No charges have been raised for this visit yet.
      </section>
    );
  }

  const totalOutstanding = lines.reduce(
    (total, line) => total + (line.outstanding ?? 0),
    0,
  );

  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-100 bg-slate-50 px-4 py-2.5">
        <h2 className="text-sm font-semibold text-slate-900">Charge lines</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <caption className="sr-only">
            All charges raised against this visit, with funding and
            authorization state.
          </caption>
          <thead className="bg-white text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th scope="col" className={TH}>
                Charge
              </th>
              <th scope="col" className={TH}>
                Category
              </th>
              <th scope="col" className={`${TH} text-right`}>
                Amount
              </th>
              <th scope="col" className={`${TH} text-right`}>
                Received
              </th>
              <th scope="col" className={`${TH} text-right`}>
                Outstanding
              </th>
              <th scope="col" className={TH}>
                Funding
              </th>
              <th scope="col" className={TH}>
                Authorization
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {lines.map((line) => (
              <tr key={line.id}>
                <th scope="row" className={`${TD} text-left font-medium`}>
                  <span className="block text-slate-900">
                    {line.description}
                  </span>
                  <span className="block text-xs font-normal text-slate-500">
                    {line.name}
                  </span>
                </th>
                <td className={`${TD} whitespace-nowrap text-slate-700`}>
                  {formatChargeCategory(line.source_category)}
                </td>
                <td className={`${NUM} text-slate-700`}>
                  {formatEtb(line.amount)}
                </td>
                <td className={`${NUM} text-slate-700`}>
                  {formatEtb(line.received)}
                </td>
                <td
                  className={`${NUM} font-semibold ${
                    (line.outstanding ?? 0) > 0
                      ? "text-amber-700"
                      : "text-slate-600"
                  }`}
                >
                  {formatEtb(line.outstanding)}
                </td>
                <td className={`${TD} whitespace-nowrap text-slate-700`}>
                  {formatPaymentState(line.payment_state)}
                </td>
                <td className={`${TD} whitespace-nowrap text-slate-700`}>
                  {formatAuthorizationState(line.authorization_state)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-slate-200 bg-slate-50">
            <tr>
              <th scope="row" colSpan={4} className={`${TD} text-right`}>
                Total outstanding
              </th>
              <td className={`${NUM} font-bold text-slate-950`}>
                {formatEtb(totalOutstanding)}
              </td>
              <td colSpan={2} />
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
