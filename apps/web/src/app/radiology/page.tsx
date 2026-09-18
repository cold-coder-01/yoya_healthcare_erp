import RadWorkstation from "@/components/radiology/rad-workstation";

/**
 * The shell lives in layout.tsx. The page is only the workstation -- no page
 * title, because the queue on screen already says where the user is.
 */
export default function RadiologyPage() {
  return <RadWorkstation />;
}
