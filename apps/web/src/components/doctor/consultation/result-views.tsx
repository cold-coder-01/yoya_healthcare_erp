"use client";

import { useState } from "react";

import {
  IMAGE_UNAVAILABLE_TEXT,
  abnormalFlagText,
  abnormalTone,
  abnormalToneClass,
  clinicalValue,
  fileSizeText,
  imageContentPath,
  isDocumented,
  reportPreview,
  resultImages,
  viewableImages,
} from "@/lib/results-format";
import type { ImageSrcBuilder } from "@/lib/results-format";
import type {
  LaboratoryReview,
  RadiologyImage,
  RadiologyReview,
} from "@/types/doctor-results";
/*
  THE HISTORY ROWS ARE THE SAME DOCUMENTS, MINUS ONE KEY. Slice 9A strips
  `billing_blocked` from every historical laboratory and radiology row, so a
  history row is structurally a Results row less that field. Widening these two
  components to accept either is what lets the current Results tab and the
  History viewer render a released result through the SAME code -- which is the
  only way the two surfaces cannot end up disagreeing about what it says.

  Neither body reads `billing_blocked`; it is a pending-side signal and these
  render released documents.
*/
import type {
  HistoryLaboratoryReview,
  HistoryRadiologyReview,
} from "@/types/doctor-history";

import ImageLightbox from "./image-lightbox";

/**
 * The two documents a released result can be, rendered for the viewer canvas.
 *
 * THESE ARE BODIES, NOT MODALS. The centered shell, the focus trap, the close
 * routes and the read-only treatment all belong to
 * ClinicalResultViewerModal; these components only know how to lay out a
 * laboratory panel and a radiology report. That split is what lets the two
 * documents look like themselves without either growing its own dialog.
 *
 * THEY RECEIVE DATA AND NOTHING ELSE. No fetch, no state, no callbacks that
 * write. Everything here was already loaded by the Results workspace when the
 * worklist rendered, so opening a viewer costs a state change and no request.
 *
 * NOTHING IS DERIVED. Values, units and reference ranges are printed exactly as
 * the laboratory recorded them, and abnormality is the laboratory's own flag.
 * A component that recomputed either would be a second clinical opinion with no
 * clinician behind it.
 */

/** A section heading inside the canvas. Quiet, and consistent between bodies. */
function ViewerSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-slate-200 pt-3 first:border-0 first:pt-0">
      <h3 className="cl-micro font-bold uppercase tracking-[0.07em] text-slate-400">
        {title}
      </h3>
      <div className="mt-1">{children}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Laboratory
 * ------------------------------------------------------------------ */

export function LaboratoryResultView({
  row,
}: {
  row: LaboratoryReview | HistoryLaboratoryReview;
}) {
  const result = row.result;
  if (!result) return null;
  const noted = result.lines.filter((line) => isDocumented(line.notes));

  return (
    <div className="flex flex-col gap-3">
      {isDocumented(row.clinical_indication) ? (
        <ViewerSection title="Clinical indication">
          <p className="whitespace-pre-wrap break-words cl-secondary leading-relaxed text-slate-700">
            {row.clinical_indication}
          </p>
        </ViewerSection>
      ) : null}

      <ViewerSection title="Results">
        {/* Wide content scrolls inside its own container, so the dialog never
            scrolls horizontally. */}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse cl-secondary">
            <thead>
              <tr className="border-b border-slate-300 text-left">
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Test
                </th>
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Result
                </th>
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Unit
                </th>
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Reference
                </th>
                <th scope="col" className="py-1.5 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Flag
                </th>
              </tr>
            </thead>
            <tbody>
              {result.lines.map((line) => {
                const tone = abnormalTone(line.abnormal_flag);
                const word = abnormalFlagText(
                  line.abnormal_flag,
                  line.abnormal_flag_label,
                );
                return (
                  <tr
                    key={line.id}
                    className="border-b border-slate-100 align-top last:border-0"
                  >
                    <th
                      scope="row"
                      className="py-2 pr-3 text-left font-semibold text-slate-800"
                    >
                      {line.name}
                      {line.code ? (
                        <span className="ml-1 font-mono cl-micro font-normal text-slate-400">
                          {line.code}
                        </span>
                      ) : null}
                      {/* The linkage the server refused to guess at. Stated,
                          because a value whose ordered test is uncertain is
                          still a value the doctor should see. */}
                      {!line.ordered ? (
                        <span className="ml-1 cl-micro font-normal italic text-slate-400">
                          unmatched order line
                        </span>
                      ) : null}
                    </th>
                    <td
                      className={`py-2 pr-3 font-semibold tabular-nums ${
                        tone === "critical"
                          ? "text-red-900"
                          : tone === "warn"
                            ? "text-amber-900"
                            : "text-slate-900"
                      }`}
                    >
                      {clinicalValue(line.value)}
                    </td>
                    <td className="py-2 pr-3 text-slate-600">
                      {clinicalValue(line.unit)}
                    </td>
                    <td className="py-2 pr-3 text-slate-600">
                      {clinicalValue(line.reference_range)}
                    </td>
                    <td className="py-2">
                      {/* THE WORD, ALWAYS. Never colour alone. */}
                      {word && tone !== "neutral" ? (
                        <span
                          className={`inline-flex rounded border px-1.5 py-px cl-micro font-bold uppercase tracking-wide ${abnormalToneClass(tone)}`}
                        >
                          {word}
                        </span>
                      ) : word ? (
                        /* A normal row states its judgement without a chip: a
                           table where every row is boxed makes the rows that
                           matter harder, not easier, to find. */
                        <span className="cl-micro text-slate-500">{word}</span>
                      ) : (
                        <span className="cl-micro text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </ViewerSection>

      {isDocumented(result.interpretation) ? (
        <ViewerSection title="Interpretation">
          <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-800">
            {result.interpretation}
          </p>
        </ViewerSection>
      ) : null}

      {isDocumented(result.remarks) ? (
        <ViewerSection title="Remarks">
          <p className="whitespace-pre-wrap break-words cl-secondary leading-relaxed text-slate-700">
            {result.remarks}
          </p>
        </ViewerSection>
      ) : null}

      {noted.length ? (
        <ViewerSection title="Line notes">
          <ul className="space-y-1">
            {noted.map((line) => (
              <li
                key={line.id}
                className="cl-secondary leading-relaxed text-slate-700"
              >
                <span className="font-semibold text-slate-800">
                  {line.name}:{" "}
                </span>
                {line.notes}
              </li>
            ))}
          </ul>
        </ViewerSection>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Radiology
 * ------------------------------------------------------------------ */

export function RadiologyResultView({
  row,
  appointmentId,
  imageSrc,
}: {
  row: RadiologyReview | HistoryRadiologyReview;
  /* Needed to compose the BFF image path. The visit travels in that URL so
     Odoo can verify the image belongs to a released result of THIS
     consultation -- the id alone would be a weaker check. */
  appointmentId: number;
  /* OPTIONAL, AND OMITTING IT IS THE CURRENT-VISIT BEHAVIOUR. The Results tab
     passes nothing and keeps the exact path it has always built. The History
     viewer passes its own builder, because a prior episode's bytes come from
     the longitudinal route that names both appointments; the results route
     deliberately refuses a historical visit. */
  imageSrc?: ImageSrcBuilder;
}) {
  const src: ImageSrcBuilder =
    imageSrc ??
    ((imageId, disposition) =>
      imageContentPath(appointmentId, imageId, disposition));
  const result = row.result;
  if (!result) return null;
  const detailed = result.exams.filter(
    (exam) => isDocumented(exam.summary) || isDocumented(exam.notes),
  );
  const images = resultImages(result);

  return (
    <div className="flex flex-col gap-3">
      {isDocumented(row.clinical_indication) ? (
        <ViewerSection title="Clinical indication">
          <p className="whitespace-pre-wrap break-words cl-secondary leading-relaxed text-slate-700">
            {row.clinical_indication}
          </p>
        </ViewerSection>
      ) : null}

      {result.has_report ? (
        <>
          {/* IMPRESSION LEADS. It is the conclusion a clinician acts on;
              findings are the evidence behind it. */}
          {isDocumented(result.impression) ? (
            <ViewerSection title="Impression">
              <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-900">
                {result.impression}
              </p>
            </ViewerSection>
          ) : null}
          {isDocumented(result.findings) ? (
            <ViewerSection title="Findings">
              <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-800">
                {result.findings}
              </p>
            </ViewerSection>
          ) : null}
          {isDocumented(result.recommendations) ? (
            <ViewerSection title="Recommendations">
              <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-800">
                {result.recommendations}
              </p>
            </ViewerSection>
          ) : null}
        </>
      ) : (
        <ViewerSection title="Report">
          {/* A REACHABLE STATE: radiology has no completeness constraint, so a
              report can be released with no narrative at all. Blank space would
              read as a broken screen; this reads as a fact about the record. */}
          <p className="cl-secondary italic leading-relaxed text-slate-500">
            {reportPreview(result)}
          </p>
        </ViewerSection>
      )}

      <ViewerSection title="Examinations">
        <ul className="space-y-1">
          {result.exams.map((exam) => (
            <li key={exam.id} className="cl-secondary leading-relaxed text-slate-700">
              <span className="font-semibold text-slate-800">{exam.name}</span>
              {exam.modality_label ? ` · ${exam.modality_label}` : ""}
              {exam.body_part ? ` · ${exam.body_part}` : ""}
              {exam.contrast_used ? (
                <span className="ml-1.5 rounded border border-violet-300 bg-violet-50 px-1 py-px cl-micro font-bold uppercase tracking-wide text-violet-900">
                  contrast
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </ViewerSection>

      {detailed.length ? (
        <ViewerSection title="Per-study notes">
          <ul className="space-y-1">
            {detailed.map((exam) => (
              <li key={exam.id} className="cl-secondary leading-relaxed text-slate-700">
                <span className="font-semibold text-slate-800">{exam.name}: </span>
                {[exam.summary, exam.notes].filter(Boolean).join(" — ")}
              </li>
            ))}
          </ul>
        </ViewerSection>
      ) : null}

      {/* LAST, and deliberately so. A radiologist's impression is the finding
          a doctor acts on; the pictures are the evidence behind it, read after
          the words rather than instead of them. */}
      {images.length ? (
        <ViewerSection title="Imaging">
          <ImagingGallery imageSrc={src} images={images} />
        </ViewerSection>
      ) : null}
    </div>
  );
}

/**
 * The attached files: thumbnails for pictures, tiles for documents.
 *
 * ONE PLACE HOLDS THE OPEN STATE, so the lightbox knows which image it started
 * from and can page from there. Everything it needs is already in `images` --
 * opening it costs no request beyond the bytes the browser fetches for the
 * picture itself.
 */
function ImagingGallery({
  imageSrc,
  images,
}: {
  imageSrc: ImageSrcBuilder;
  images: RadiologyImage[];
}) {
  /* Only pictures are paged through. A PDF is opened, not flicked past, so it
     is excluded from the lightbox list and the indices stay meaningful. */
  const viewable = viewableImages({ images });
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  /* The thumbnail that opened the lightbox. Focus returns to it on close, so a
     keyboard user resumes where they were rather than at the top of the
     dialog. */
  const [origin, setOrigin] = useState<HTMLButtonElement | null>(null);

  function open(image: RadiologyImage, button: HTMLButtonElement) {
    const index = viewable.findIndex((candidate) => candidate.id === image.id);
    if (index < 0) return;
    setOrigin(button);
    setOpenIndex(index);
  }

  function close() {
    setOpenIndex(null);
    const button = origin;
    setOrigin(null);
    // Next frame: the thumbnail is still behind the lightbox at this point in
    // the commit, and focusing an element about to re-render loses the ring.
    if (button) requestAnimationFrame(() => button.focus());
  }

  return (
    <>
      <ul className="flex flex-wrap gap-2">
        {images.map((image) =>
          image.kind === "pdf" ? (
            <li key={image.id}>
              <PdfTile imageSrc={imageSrc} image={image} />
            </li>
          ) : (
            <li key={image.id}>
              <Thumbnail
                imageSrc={imageSrc}
                image={image}
                onOpen={(button) => open(image, button)}
              />
            </li>
          ),
        )}
      </ul>

      {openIndex !== null ? (
        <ImageLightbox
          imageSrc={imageSrc}
          images={viewable}
          startIndex={openIndex}
          onClose={close}
        />
      ) : null}
    </>
  );
}

/** One picture, as a button that opens it larger. */
function Thumbnail({
  imageSrc,
  image,
  onOpen,
}: {
  imageSrc: ImageSrcBuilder;
  image: RadiologyImage;
  onOpen: (origin: HTMLButtonElement) => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={(event) => onOpen(event.currentTarget)}
      /* Names the image, so a row of these does not announce as several
         identical "View image" controls. */
      aria-label={`View image: ${image.name}`}
      className="group flex w-[160px] flex-col gap-1 rounded-lg border border-slate-200 bg-white p-1.5 text-left outline-none transition-colors hover:border-emerald-400 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/40"
    >
      <span className="flex h-[104px] items-center justify-center overflow-hidden rounded bg-slate-100">
        {failed ? (
          /* CONTAINED. One unreachable file must not take the report down with
             it, and no backend error text is echoed to the doctor. */
          <span className="px-2 text-center cl-micro text-slate-500">
            {IMAGE_UNAVAILABLE_TEXT}
          </span>
        ) : (
          /* eslint-disable-next-line @next/next/no-img-element --
             next/image would route these bytes through its own loader and
             cache, which is a second path to clinical data. The BFF route
             stays the only way an image reaches the browser. */
          <img
            src={imageSrc(image.id)}
            alt=""
            onError={() => setFailed(true)}
            className="h-full w-full object-cover"
          />
        )}
      </span>
      <span className="min-w-0 truncate cl-meta font-semibold text-slate-800">
        {image.name}
      </span>
      {image.caption ? (
        <span className="min-w-0 truncate cl-micro text-slate-500">
          {image.caption}
        </span>
      ) : null}
    </button>
  );
}

/**
 * One document, as a tile with an explicit Open.
 *
 * NOT AN INLINE FRAME. Rendering an uploaded PDF in an <iframe> or <object>
 * gives the file's own scripting a context inside this application's origin;
 * proxying the bytes through the BFF makes them same-origin, which is exactly
 * what makes that dangerous. A new tab hands the file to the browser's own
 * viewer instead.
 */
function PdfTile({
  imageSrc,
  image,
}: {
  imageSrc: ImageSrcBuilder;
  image: RadiologyImage;
}) {
  const size = fileSizeText(image.file_size);
  return (
    <div className="flex w-[220px] flex-col gap-1 rounded-lg border border-slate-200 bg-white p-2">
      <div className="flex min-w-0 items-center gap-1.5">
        <span
          aria-hidden
          className="shrink-0 rounded border border-red-300 bg-red-50 px-1 py-px cl-micro font-bold uppercase tracking-wide text-red-800"
        >
          PDF
        </span>
        <span className="min-w-0 truncate cl-meta font-semibold text-slate-800">
          {image.name}
        </span>
      </div>
      <span className="min-w-0 truncate cl-micro text-slate-500">
        {[image.filename, size].filter(Boolean).join(" · ")}
      </span>
      {image.caption ? (
        <span className="min-w-0 truncate cl-micro text-slate-500">
          {image.caption}
        </span>
      ) : null}
      <a
        href={imageSrc(image.id, "attachment")}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-0.5 self-start rounded border border-slate-300 bg-white px-2 py-0.5 cl-meta font-bold uppercase tracking-wide text-slate-700 outline-none transition-colors hover:border-emerald-400 hover:bg-emerald-50 hover:text-emerald-900 focus-visible:ring-2 focus-visible:ring-emerald-600"
      >
        Open PDF
      </a>
    </div>
  );
}
