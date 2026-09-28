/**
 * Workstation fullscreen: the Fullscreen API, made safe to call anywhere.
 *
 * Pure functions over a minimal Document shape, so node:test drives them with
 * a fake and the component stays a thin wrapper. Every function tolerates a
 * browser WITHOUT the API (older WebViews, iPhone Safari on non-video
 * elements): support is false, the toggle is not offered, nothing throws.
 *
 * Fullscreen is presentation only. Entering it neither reloads nor navigates,
 * so the route and every desk's in-memory state are exactly as they were.
 * Esc is the BROWSER's exit; nothing here listens for keys -- the
 * `fullscreenchange` event is what keeps the button honest afterwards.
 */

export type FullscreenDocument = {
  fullscreenEnabled?: boolean;
  fullscreenElement?: unknown;
  documentElement?: { requestFullscreen?: () => Promise<void> } | null;
  exitFullscreen?: () => Promise<void>;
  addEventListener?: (type: "fullscreenchange", listener: () => void) => void;
  removeEventListener?: (type: "fullscreenchange", listener: () => void) => void;
};

export const FULLSCREEN_CHANGE = "fullscreenchange";

export function fullscreenSupported(doc: FullscreenDocument | null | undefined): boolean {
  return Boolean(
    doc &&
      doc.fullscreenEnabled === true &&
      typeof doc.documentElement?.requestFullscreen === "function" &&
      typeof doc.exitFullscreen === "function",
  );
}

export function isFullscreen(doc: FullscreenDocument | null | undefined): boolean {
  return Boolean(doc?.fullscreenElement);
}

/** Enter when out, exit when in. A refusal (no user gesture, policy) is swallowed. */
export async function toggleFullscreen(doc: FullscreenDocument | null | undefined): Promise<void> {
  if (!doc || !fullscreenSupported(doc)) return;
  try {
    if (isFullscreen(doc)) {
      await doc.exitFullscreen!();
    } else {
      await doc.documentElement!.requestFullscreen!();
    }
  } catch {
    // The browser said no; the button stays as it was and fullscreenchange
    // never fires. Nothing to recover.
  }
}

/** Subscribe to fullscreenchange; returns the unsubscribe. No-op if unsupported. */
export function subscribeFullscreen(
  doc: FullscreenDocument | null | undefined,
  onChange: () => void,
): () => void {
  if (!doc?.addEventListener || !doc.removeEventListener) return () => {};
  doc.addEventListener(FULLSCREEN_CHANGE, onChange);
  return () => doc.removeEventListener?.(FULLSCREEN_CHANGE, onChange);
}

export function fullscreenLabel(active: boolean): "Enter fullscreen" | "Exit fullscreen" {
  return active ? "Exit fullscreen" : "Enter fullscreen";
}
