import FrontDeskWorkstation from "@/components/front-desk/front-desk-workstation";

/**
 * The shell lives in layout.tsx (sidebarless, one brand line). The page is only
 * the workstation -- no page title, because the queue on screen already says
 * where the nurse is, and "Front Desk Workstation" under "Front Desk Workspace"
 * under "YOYA EMR" was three labels explaining the same thing.
 */
export default function FrontDeskPage() {
  return <FrontDeskWorkstation />;
}
