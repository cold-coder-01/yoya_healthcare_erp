"use client";

import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { ImageSrcBuilder } from "@/lib/results-format";
import {
  IMAGE_UNAVAILABLE_TEXT,
  lightboxPosition,
  stepIndex,
} from "@/lib/results-format";
import type { RadiologyImage } from "@/types/doctor-results";

/**
 * One clinical image, large, above the result viewer.
 *
 * IT OPENS OVER DATA THE DESK ALREADY HAS. The images, their names and their
 * captions all arrived with the Results payload, so opening this costs no JSON
 * request. The browser does fetch the BYTES through the `<img src>` below --
 * that is the point of the component and is expected -- but nothing here asks
 * the server what the image IS a second time.
 *
 * ITS OWN SHELL, NOT ClinicalResultViewerModal. That modal is a document
 * canvas: a white sheet with a header, a metadata strip and a scrolling body,
 * sized for reading prose. An image wants the opposite -- a dark ground, no
 * chrome competing with the picture, and bounds set by the viewport rather
 * than by a measure. Reusing it would have meant a `variant` flag threading
 * through every part of that layout.
 *
 * IT LAYERS ABOVE THE VIEWER rather than replacing it, so closing returns the
 * doctor to the report they were reading, at the section they were in.
 *
 * READ-ONLY, like every other Doctor Results surface: no edit, no annotation,
 * no delete, and no download button for an image -- a picture is looked at
 * here, and the PDF tile in the viewer is where "open a file" lives.
 */
export default function ImageLightbox({
  imageSrc,
  images,
  startIndex,
  onClose,
}: {
  /* HOW A PATH IS BUILT, injected by the surface that opened this. The current
     visit's Results tab and the History viewer resolve to different BFF
     routes, and neither is this component's business to know. */
  imageSrc: ImageSrcBuilder;
  /** Already loaded. Only `kind: "image"` items should be passed in. */
  images: RadiologyImage[];
  startIndex: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(startIndex);
  const [failed, setFailed] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const total = images.length;
  const image = images[index];

  const step = useCallback(
    (by: number) => {
      setFailed(false);
      setIndex((current) => stepIndex(current, total, by));
    },
    [total],
  );

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        // Stopped here so Escape closes the lightbox and does not also reach
        // the result viewer underneath it.
        event.stopPropagation();
        onClose();
        return;
      }
      if (total <= 1) return;
      if (event.key === "ArrowRight") {
        event.stopPropagation();
        step(1);
      } else if (event.key === "ArrowLeft") {
        event.stopPropagation();
        step(-1);
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onClose, step, total]);

  /* The same minimal trap the other two modals use: Tab cycling is the
     browser's, but focus may not leave the panel. */
  const onKeyDownPanel = useCallback((event: ReactKeyboardEvent) => {
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = panel.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  if (!image) return null;
  const position = lightboxPosition(index, total);
  const titleId = "image-lightbox-title";

  return (
    /* z above the result viewer's 100, so it layers rather than replaces. */
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
      <div
        aria-hidden
        onClick={onClose}
        /* Darker than the document modal's backdrop: a clinical image is read
           by its greys, and a light ground behind it shifts the perception of
           the ones near black. */
        className="absolute inset-0 bg-slate-950/80"
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDownPanel}
        className="relative flex max-h-[92vh] w-[1100px] max-w-[95vw] flex-col gap-2"
      >
        <header className="flex shrink-0 items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2
              id={titleId}
              className="min-w-0 truncate cl-head font-semibold text-white"
            >
              {image.name}
            </h2>
            {image.caption ? (
              <p className="mt-0.5 truncate cl-meta text-slate-300">
                {image.caption}
              </p>
            ) : null}
          </div>
          {position ? (
            <span className="shrink-0 rounded border border-slate-600 bg-slate-900/70 px-1.5 py-px cl-micro font-bold tabular-nums text-slate-200">
              {position}
            </span>
          ) : null}
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close image"
            className="shrink-0 rounded-md border border-slate-600 bg-slate-900/70 px-2 py-1 cl-body font-semibold leading-none text-slate-200 outline-none transition-colors hover:border-slate-400 hover:text-white focus-visible:ring-2 focus-visible:ring-emerald-500"
          >
            ✕
          </button>
        </header>

        <div className="relative flex min-h-0 flex-1 items-center justify-center">
          {total > 1 ? (
            <button
              type="button"
              onClick={() => step(-1)}
              aria-label="Previous image"
              className="absolute left-0 z-10 rounded-md border border-slate-600 bg-slate-900/80 px-2 py-3 cl-body font-bold text-slate-200 outline-none transition-colors hover:border-slate-400 hover:text-white focus-visible:ring-2 focus-visible:ring-emerald-500"
            >
              ‹
            </button>
          ) : null}

          {failed ? (
            /* A contained failure: the lightbox says so and stays usable, and
               no backend error text is echoed to the doctor. */
            <p
              role="alert"
              className="rounded-md border border-slate-600 bg-slate-900/80 px-4 py-6 cl-secondary text-slate-200"
            >
              {IMAGE_UNAVAILABLE_TEXT}
            </p>
          ) : (
            /* eslint-disable-next-line @next/next/no-img-element --
               next/image would proxy or optimise these bytes through its own
               loader, which is a second path to clinical data and a cache we
               do not control. A plain <img> keeps the one BFF route the only
               way an image reaches the browser. */
            <img
              key={image.id}
              src={imageSrc(image.id)}
              alt={image.caption ? `${image.name}. ${image.caption}` : image.name}
              onError={() => setFailed(true)}
              className="max-h-[80vh] max-w-full object-contain"
            />
          )}

          {total > 1 ? (
            <button
              type="button"
              onClick={() => step(1)}
              aria-label="Next image"
              className="absolute right-0 z-10 rounded-md border border-slate-600 bg-slate-900/80 px-2 py-3 cl-body font-bold text-slate-200 outline-none transition-colors hover:border-slate-400 hover:text-white focus-visible:ring-2 focus-visible:ring-emerald-500"
            >
              ›
            </button>
          ) : null}
        </div>

        <p className="shrink-0 cl-meta text-slate-400">
          {total > 1 ? "Arrow keys move between images. " : ""}Escape closes.
        </p>
      </div>
    </div>
  );
}
