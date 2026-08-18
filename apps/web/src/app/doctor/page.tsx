import DoctorWorkstation from "@/components/doctor/doctor-workstation";

/**
 * The shell lives in layout.tsx. The page is only the workstation -- no page
 * title, because the queue on screen already says where the doctor is.
 */
export default function DoctorPage() {
  return <DoctorWorkstation />;
}
