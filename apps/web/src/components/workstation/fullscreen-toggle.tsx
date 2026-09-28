"use client";

/**
 * The shared workstation fullscreen button. Sits in every desk header,
 * immediately before the signed-in user control.
 *
 * useSyncExternalStore keeps the icon true to the document: entering,
 * leaving by this button, and leaving by the browser's own Esc all fire
 * `fullscreenchange`, and the store re-reads `document.fullscreenElement`.
 * The server snapshot is "unsupported", so the button appears only after
 * hydration and only where the API exists -- no mismatch, no dead control.
 */
import { useSyncExternalStore } from "react";

import {
  fullscreenLabel,
  fullscreenSupported,
  isFullscreen,
  subscribeFullscreen,
  toggleFullscreen,
} from "@/lib/fullscreen";

const noSubscription = () => () => {};

export default function FullscreenToggle() {
  const supported = useSyncExternalStore(
    noSubscription,
    () => fullscreenSupported(document),
    () => false,
  );
  const active = useSyncExternalStore(
    (onChange) => subscribeFullscreen(document, onChange),
    () => isFullscreen(document),
    () => false,
  );

  if (!supported) {
    return null;
  }

  const label = fullscreenLabel(active);
  return (
    <button
      type="button"
      onClick={() => void toggleFullscreen(document)}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded text-slate-500 outline-none hover:bg-slate-100 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-slate-500"
    >
      {active ? (
        <svg aria-hidden viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
          <path d="M8 3v5H3M12 3v5h5M8 17v-5H3M12 17v-5h5" />
        </svg>
      ) : (
        <svg aria-hidden viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
          <path d="M3 8V3h5M17 8V3h-5M3 12v5h5M17 12v5h-5" />
        </svg>
      )}
    </button>
  );
}
