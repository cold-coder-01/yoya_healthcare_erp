"use client";

/**
 * Doctor list for a department, from the EXISTING /api/reference/doctors route.
 *
 * Extracted from components/clinical/doctor-select.tsx so the Front Desk panel
 * can render its own dense control without a second copy of the fetch, the
 * abort handling and the current-doctor merge. That component still owns its
 * own markup; only the data loading moved here.
 */
import { useEffect, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import type { ApiEnvelope, Doctor } from "@/types/reception";

export function useDoctors(
  departmentId?: number | null,
  /**
   * The doctor already recorded on the record. Kept in the list even when the
   * department filter would exclude them, so opening a visit whose doctor sits
   * in another department never silently blanks the field and drops the
   * assignment on the next write.
   */
  currentDoctor?: { id: number; name: string } | null,
) {
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function run() {
      setLoading(true);
      const query = departmentId
        ? `?department_id=${encodeURIComponent(String(departmentId))}`
        : "";
      try {
        const response = await fetch(`/api/reference/doctors${query}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<{
          doctors: Doctor[];
        }>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setError(messageFromPayload(payload, "Unable to load doctors."));
          setDoctors([]);
          return;
        }
        setError(null);
        setDoctors(payload.data.doctors ?? []);
      } catch {
        if (controller.signal.aborted) return;
        setError("Unable to reach the reference service.");
        setDoctors([]);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void run();
    return () => controller.abort();
  }, [departmentId]);

  const options = [...doctors];
  if (currentDoctor && !options.some((doctor) => doctor.id === currentDoctor.id)) {
    options.unshift({
      id: currentDoctor.id,
      name: currentDoctor.name,
      department: null,
      user_linked: false,
      active: true,
    });
  }

  return { options, loading, error };
}
