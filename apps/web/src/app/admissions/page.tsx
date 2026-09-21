import AdmissionsWorkstation from "@/components/admissions/admissions-workstation";

/**
 * The shell lives in layout.tsx. The page is only the workstation -- no page
 * title, because the ward strip and census on screen already say where the
 * user is.
 */
export default function AdmissionsPage() {
  return <AdmissionsWorkstation />;
}
