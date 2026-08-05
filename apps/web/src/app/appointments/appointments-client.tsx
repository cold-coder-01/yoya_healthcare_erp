"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

type Appointment = {
  id: number;
  name: string;
  patient_id: number;
  patient_name: string;
  patient_number: string;
  appointment_date: string | false | null;
  doctor_name: string;
  reason: string | false | null;
  state: string;
  started_at: string | false | null;
  completed_at: string | false | null;
};

type AppointmentsResponse = {
  success: boolean;
  data?: {
    appointments?: Appointment[];
    appointment?: Appointment;
  };
  error?: {
    code: string;
    message: string;
  };
};

function formatDate(value: Appointment["appointment_date"]) {
  if (!value) {
    return "Not scheduled";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function getStateLabel(state: string) {
  return state
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

async function readResponse(response: Response): Promise<AppointmentsResponse> {
  try {
    return (await response.json()) as AppointmentsResponse;
  } catch {
    return {
      success: false,
      error: {
        code: "invalid_response",
        message: "The server returned an invalid response.",
      },
    };
  }
}

async function requestAppointments() {
  const response = await fetch("/api/appointments", {
    method: "GET",
    cache: "no-store",
  });
  const payload = await readResponse(response);

  if (!response.ok || !payload.success) {
    throw new Error(payload.error?.message ?? "Unable to load appointments.");
  }

  return payload.data?.appointments ?? [];
}

export default function AppointmentsClient() {
  const router = useRouter();
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [startingId, setStartingId] = useState<number | null>(null);

  const waitingCount = useMemo(
    () => appointments.filter((appointment) => appointment.state === "waiting").length,
    [appointments],
  );

  const loadAppointments = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      setAppointments(await requestAppointments());
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load appointments.",
      );
      setAppointments([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;

    async function loadInitialAppointments() {
      try {
        const nextAppointments = await requestAppointments();
        if (active) {
          setAppointments(nextAppointments);
          setError(null);
        }
      } catch (loadError) {
        if (active) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Unable to load appointments.",
          );
          setAppointments([]);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadInitialAppointments();

    return () => {
      active = false;
    };
  }, []);

  async function startAppointment(appointmentId: number) {
    setStartingId(appointmentId);
    setError(null);

    try {
      const response = await fetch(`/api/appointments/${appointmentId}/start`, {
        method: "POST",
      });
      const payload = await readResponse(response);

      if (!response.ok || !payload.success || !payload.data?.appointment) {
        setError(payload.error?.message ?? "Unable to start consultation.");
        return;
      }

      setAppointments((current) =>
        current.map((appointment) =>
          appointment.id === appointmentId ? payload.data!.appointment! : appointment,
        ),
      );
    } catch {
      setError("Unable to reach the appointment service.");
    } finally {
      setStartingId(null);
    }
  }

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <main className="min-h-screen bg-slate-50 px-5 py-8 text-slate-950 sm:px-8">
      <section className="mx-auto w-full max-w-6xl">
        <header className="mb-8 flex flex-col gap-4 border-b border-slate-200 pb-6 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-emerald-700">
              YOYA General Hospital
            </p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight">
              Demo appointments
            </h1>
            <p className="mt-2 text-sm text-slate-600">
              {waitingCount} waiting consultation{waitingCount === 1 ? "" : "s"}
            </p>
          </div>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={loadAppointments}
              disabled={loading}
              className="h-10 rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Refresh
            </button>
            <button
              type="button"
              onClick={logout}
              className="h-10 rounded-md bg-slate-900 px-4 text-sm font-medium text-white transition hover:bg-slate-700"
            >
              Logout
            </button>
          </div>
        </header>

        {error ? (
          <div className="mb-5 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        {loading ? (
          <div className="rounded-lg border border-slate-200 bg-white p-8 text-sm text-slate-600 shadow-sm">
            Loading appointments...
          </div>
        ) : appointments.length === 0 ? (
          <div className="rounded-lg border border-slate-200 bg-white p-8 text-sm text-slate-600 shadow-sm">
            No appointments are available for this Odoo user.
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-100 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                  <tr>
                    <th className="px-4 py-3">Appointment</th>
                    <th className="px-4 py-3">Patient</th>
                    <th className="px-4 py-3">Date</th>
                    <th className="px-4 py-3">Doctor</th>
                    <th className="px-4 py-3">Reason</th>
                    <th className="px-4 py-3">State</th>
                    <th className="px-4 py-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200">
                  {appointments.map((appointment) => (
                    <tr key={appointment.id} className="align-top">
                      <td className="px-4 py-4 font-medium text-slate-950">
                        {appointment.name}
                      </td>
                      <td className="px-4 py-4">
                        <div className="font-medium text-slate-900">
                          {appointment.patient_name}
                        </div>
                        <div className="text-xs text-slate-500">
                          {appointment.patient_number}
                        </div>
                      </td>
                      <td className="px-4 py-4 text-slate-700">
                        {formatDate(appointment.appointment_date)}
                      </td>
                      <td className="px-4 py-4 text-slate-700">
                        {appointment.doctor_name}
                      </td>
                      <td className="max-w-xs px-4 py-4 text-slate-600">
                        {appointment.reason || "-"}
                      </td>
                      <td className="px-4 py-4">
                        <span className="inline-flex rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-800 ring-1 ring-emerald-100">
                          {getStateLabel(appointment.state)}
                        </span>
                      </td>
                      <td className="px-4 py-4 text-right">
                        {appointment.state === "waiting" ? (
                          <button
                            type="button"
                            onClick={() => startAppointment(appointment.id)}
                            disabled={startingId === appointment.id}
                            className="h-9 rounded-md bg-emerald-700 px-3 text-xs font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-400"
                          >
                            {startingId === appointment.id
                              ? "Starting..."
                              : "Start Consultation"}
                          </button>
                        ) : (
                          <span className="text-xs text-slate-400">No action</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
