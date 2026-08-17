import CashierWorkstation from "@/components/cashier/cashier-workstation";

/**
 * The shell lives in layout.tsx. The page is only the workstation -- the queue
 * on screen already says where the cashier is, so a page title would be a
 * third label explaining the same thing.
 */
export default function CashierPage() {
  return <CashierWorkstation />;
}
