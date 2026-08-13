"use client";

/**
 * Department list from the existing /api/reference/departments route.
 *
 * Companion to useDoctors. Both exist so the Front Desk toolbar can render its
 * own compact controls without importing components/reception/reference-filters,
 * which is shared with the legacy /reception queue and carries that page's
 * taller form styling. The data loading is shared; only the markup differs.
 */
import { useEffect, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import type { ApiEnvelope, Department } from "@/types/reception";

export function useDepartments() {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function run() {
      setLoading(true);
      try {
        const response = await fetch("/api/reference/departments", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<{
          departments: Department[];
        }>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setError(messageFromPayload(payload, "Unable to load departments."));
          setDepartments([]);
          return;
        }
        setError(null);
        setDepartments(payload.data.departments ?? []);
      } catch {
        if (controller.signal.aborted) return;
        setError("Unable to reach the reference service.");
        setDepartments([]);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void run();
    return () => controller.abort();
  }, []);

  return { departments, loading, error };
}
