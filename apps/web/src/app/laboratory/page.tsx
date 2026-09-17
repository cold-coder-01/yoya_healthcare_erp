import LabWorkstation from "@/components/laboratory/lab-workstation";

/**
 * The shell lives in layout.tsx. The page is only the workstation -- no page
 * title, because the queue on screen already says where the technician is.
 */
export default function LaboratoryPage() {
  return <LabWorkstation />;
}
