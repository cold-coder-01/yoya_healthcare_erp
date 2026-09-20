import PharmacyWorkstation from "@/components/pharmacy/pharmacy-workstation";

/**
 * The shell lives in layout.tsx. The page is only the workstation -- no page
 * title, because the queue on screen already says where the user is.
 */
export default function PharmacyPage() {
  return <PharmacyWorkstation />;
}
