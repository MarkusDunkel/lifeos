// A PDF rendered with pdf.js, page by page, with comment areas on top: in feedback mode,
// drag a rectangle over a page to comment on that area; the text inside it comes along.
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";
import type { FeedbackItem, Region } from "../workspaces/api";
import { dragRegion, type Point, type TextBox, textIn } from "./regions";

type PdfJs = typeof import("pdfjs-dist");
type PdfDocument = Awaited<ReturnType<PdfJs["getDocument"]>["promise"]>;
type PdfPage = Awaited<ReturnType<PdfDocument["getPage"]>>;

let loader: Promise<PdfJs> | null = null;
function pdfjs(): Promise<PdfJs> {
  // loaded on first use: the library is large and only the review page needs it
  loader ??= import("pdfjs-dist").then((lib) => {
    lib.GlobalWorkerOptions.workerSrc = workerUrl;
    return lib;
  });
  return loader;
}

type Props = {
  url: string;
  title: string;
  items: FeedbackItem[]; // comments of the shown round; those with a region are drawn
  reviewing: boolean; // dragging marks an area only in feedback mode
  focus: string | null; // the comment to scroll to
  onRegion: (region: Region, text: string) => void;
  onPick: (id: string) => void;
};

export function PdfView({ url, title, items, reviewing, focus, onRegion, onPick }: Props) {
  const [doc, setDoc] = useState<PdfDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let task: ReturnType<PdfJs["getDocument"]> | null = null;
    setDoc(null);
    setError(null);
    pdfjs()
      .then((lib) => {
        if (cancelled) return null;
        task = lib.getDocument({ url });
        return task.promise;
      })
      .then((d) => {
        if (d && !cancelled) setDoc(d);
      })
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
      void task?.destroy(); // also frees the document and its worker data
    };
  }, [url]);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!focus) return;
    boxRef.current?.querySelector(`[data-region="${focus}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focus]);

  return (
    <div ref={boxRef} className="h-full overflow-y-auto bg-slate-100 p-3 dark:bg-slate-900" aria-label={title}>
      {error && <PdfError message={error} url={url} />}
      {!doc && !error && <p className="p-4 text-sm text-slate-400">Rendering…</p>}
      {doc && width > 0 && (
        <div className="space-y-3">
          {Array.from({ length: doc.numPages }, (_, i) => (
            <PageView
              key={`${url}#${i + 1}`}
              doc={doc}
              number={i + 1}
              width={width - 24}
              items={items.filter((it) => it.region?.page === i + 1)}
              reviewing={reviewing}
              focus={focus}
              onRegion={onRegion}
              onPick={onPick}
            />
          ))}
        </div>
      )}
      {reviewing && doc && (
        <p className="mt-2 text-center text-xs text-slate-500">Drag over the page to comment on an area.</p>
      )}
    </div>
  );
}

function PdfError({ message, url }: { message: string; url: string }) {
  return (
    <div className="m-2 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
      The PDF could not be shown: {message}{" "}
      <a href={url} target="_blank" rel="noreferrer" className="underline">
        Open it directly
      </a>
    </div>
  );
}

type PageProps = Omit<Props, "url" | "title"> & { doc: PdfDocument; number: number; width: number };

function PageView({ doc, number, width, items, reviewing, focus, onRegion, onPick }: PageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [text, setText] = useState<TextBox[]>([]);
  const [drag, setDrag] = useState<{ from: Point; to: Point } | null>(null);

  useEffect(() => {
    let cancelled = false;
    let task: ReturnType<PdfPage["render"]> | null = null;
    void doc.getPage(number).then(async (page) => {
      if (cancelled || !canvasRef.current) return;
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: width / base.width });
      const ratio = window.devicePixelRatio || 1;
      const canvas = canvasRef.current;
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      setSize({ w: viewport.width, h: viewport.height });
      task = page.render({ canvas, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      await task.promise.catch(() => undefined); // cancelled on resize
      const content = await page.getTextContent();
      if (cancelled) return;
      const lib = await pdfjs();
      setText(
        content.items.flatMap((item) => {
          if (!("str" in item)) return [];
          const t = lib.Util.transform(base.transform, item.transform);
          const h = Math.hypot(t[2], t[3]);
          return [
            {
              str: item.str,
              eol: item.hasEOL,
              x: (t[4] / base.width) * 100,
              y: ((t[5] - h) / base.height) * 100,
              w: (item.width / base.width) * 100,
              h: (h / base.height) * 100,
            },
          ];
        }),
      );
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, number, width]);

  const point = (e: React.PointerEvent<HTMLDivElement>): Point => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const finish = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag || !size) return;
    const region = dragRegion(number, drag.from, point(e), size.w, size.h);
    setDrag(null);
    if (region) onRegion(region, textIn(text, region));
  };

  const live = drag && size ? dragRegion(number, drag.from, drag.to, size.w, size.h) : null;

  return (
    <div className="relative mx-auto bg-white shadow" style={size ? { width: size.w, height: size.h } : { width }}>
      <canvas ref={canvasRef} className="block" style={size ? { width: size.w, height: size.h } : undefined} />
      <div
        className={`absolute inset-0 ${reviewing ? "cursor-crosshair touch-none" : ""}`}
        onPointerDown={(e) => {
          if (!reviewing || e.button !== 0 || (e.target as Element).closest("[data-region]")) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          const p = point(e);
          setDrag({ from: p, to: p });
        }}
        onPointerMove={(e) => drag && setDrag({ ...drag, to: point(e) })}
        onPointerUp={finish}
        onPointerCancel={() => setDrag(null)}
      >
        {items.map((item) => (
          <button
            key={item.id}
            data-region={item.id}
            onClick={() => onPick(item.id)}
            title={item.feedback}
            className={`absolute border-2 text-left ${
              focus === item.id ? "border-accent-600 bg-accent-500/15" : "border-amber-500 bg-amber-400/10 hover:bg-amber-400/20"
            }`}
            style={box(item.region!)}
          >
            <span className="absolute -top-2.5 -left-2.5 rounded bg-amber-500 px-1 text-[10px] font-semibold text-white">
              {item.id}
            </span>
          </button>
        ))}
        {live && <div className="pointer-events-none absolute border-2 border-dashed border-accent-600 bg-accent-500/10" style={box(live)} />}
      </div>
    </div>
  );
}

const box = (r: Region) => ({ left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` });
