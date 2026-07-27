import type { ReactNode } from "react";

/* Small primitives only. The page is mostly prose and figures, and a landing page that
   needs a component library is usually hiding the fact that it has nothing to say. */

export function Section({
  id,
  eyebrow,
  title,
  lede,
  children,
  bleed = false,
}: {
  id?: string;
  eyebrow?: string;
  title?: ReactNode;
  lede?: ReactNode;
  children?: ReactNode;
  bleed?: boolean;
}) {
  return (
    <section
      id={id}
      className={`border-t rule ${bleed ? "bg-bg-2" : ""} px-6 py-20 sm:px-10 sm:py-28`}
    >
      <div className="mx-auto max-w-6xl">
        {eyebrow && (
          <p className="mb-4 text-xs font-semibold uppercase tracking-[0.18em] text-gold">
            {eyebrow}
          </p>
        )}
        {title && (
          <h2 className="max-w-4xl text-3xl font-semibold leading-[1.15] tracking-tight sm:text-5xl">
            {title}
          </h2>
        )}
        {lede && (
          <p className="mt-6 max-w-3xl text-lg leading-relaxed text-ink-2 sm:text-xl">
            {lede}
          </p>
        )}
        {children && <div className="mt-12">{children}</div>}
      </div>
    </section>
  );
}

/** A figure with its source attached. The source is not decoration — an unsourced
 *  number on a B2B page is the same thing the research found the industry doing. */
export function Stat({
  figure,
  label,
  source,
  accent = "text-s1",
}: {
  figure: string;
  label: ReactNode;
  source: string;
  accent?: string;
}) {
  return (
    <div className="flex flex-col rounded-xl border rule bg-panel p-6">
      <div className={`stat-figure text-5xl font-semibold sm:text-6xl ${accent}`}>
        {figure}
      </div>
      <p className="mt-4 flex-1 text-[15px] leading-relaxed text-ink-2">{label}</p>
      <p className="mt-5 border-t rule pt-3 text-xs leading-relaxed text-ink-3">
        {source}
      </p>
    </div>
  );
}

export function Card({
  step,
  title,
  children,
}: {
  step?: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border rule bg-panel p-6">
      {step && (
        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-ink-3">
          {step}
        </span>
      )}
      <h3 className="mt-2 text-xl font-semibold tracking-tight">{title}</h3>
      <p className="mt-3 text-[15px] leading-relaxed text-ink-2">{children}</p>
    </div>
  );
}

export function Grid({
  cols = 3,
  children,
}: {
  cols?: 2 | 3 | 4;
  children: ReactNode;
}) {
  const map = {
    2: "sm:grid-cols-2",
    3: "sm:grid-cols-2 lg:grid-cols-3",
    4: "sm:grid-cols-2 lg:grid-cols-4",
  } as const;
  return <div className={`grid grid-cols-1 gap-5 ${map[cols]}`}>{children}</div>;
}

export function CTA({
  href,
  children,
  variant = "primary",
}: {
  href: string;
  children: ReactNode;
  variant?: "primary" | "ghost";
}) {
  const base =
    "inline-flex items-center justify-center rounded-lg px-6 py-3 text-[15px] font-semibold transition-colors";
  const styles =
    variant === "primary"
      ? "bg-gold text-bg-2 hover:bg-white"
      : "border rule text-ink hover:bg-panel";
  return (
    <a href={href} className={`${base} ${styles}`}>
      {children}
    </a>
  );
}
