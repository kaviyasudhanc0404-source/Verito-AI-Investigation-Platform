import { motion } from "framer-motion";
import { FileInput, Cpu, GitBranch, FileCheck2 } from "lucide-react";

const stages = [
  {
    n: "01",
    title: "SUBMIT THE CASE",
    text: "Text, files and voice enter the investigation workspace as evidence.",
    Icon: FileInput,
    tilt: -2.5,
  },
  {
    n: "02",
    title: "AGENTS INVESTIGATE",
    text: "Six specialised AI agents activate and analyse each piece in parallel.",
    Icon: Cpu,
    tilt: 1.8,
  },
  {
    n: "03",
    title: "CONNECTIONS EMERGE",
    text: "Related clues bind together through animated evidence threads.",
    Icon: GitBranch,
    tilt: -1.6,
  },
  {
    n: "04",
    title: "THE CASE TAKES SHAPE",
    text: "Findings assemble into a clear, structured investigation report.",
    Icon: FileCheck2,
    tilt: 2.2,
  },
] as const;

export function HowItWorks() {
  return (
    <section id="works" className="relative px-6 py-32">
      {/* Corkboard dot backdrop */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.08]"
        aria-hidden
        style={{
          backgroundImage:
            "radial-gradient(oklch(0.9 0.14 82 / 0.5) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }}
      />

      <div className="relative mx-auto max-w-6xl">
        <div className="mb-20 text-center">
          <div className="font-mono text-[11px] tracking-[0.3em] text-[color:var(--crimson-glow)]">
            HOW&nbsp;VERITO&nbsp;WORKS
          </div>
          <h2 className="mt-4 text-3xl font-semibold tracking-tight md:text-5xl">
            The <span className="gold-text">evidence board</span>
          </h2>
          <p className="mt-3 text-sm text-muted-foreground">
            Pinned intel. Red threads. Every step of the investigation, visible.
          </p>
        </div>

        <div className="relative">
          {/* Red investigation thread connecting the pins (desktop) */}
          <svg
            className="pointer-events-none absolute inset-x-0 top-0 hidden h-24 w-full md:block"
            viewBox="0 0 1000 100"
            preserveAspectRatio="none"
            aria-hidden
          >
            <defs>
              <linearGradient id="thread-grad" x1="0" x2="1">
                <stop offset="0%" stopColor="oklch(0.66 0.24 22)" stopOpacity="0" />
                <stop offset="10%" stopColor="oklch(0.66 0.24 22)" stopOpacity="0.95" />
                <stop offset="90%" stopColor="oklch(0.66 0.24 22)" stopOpacity="0.95" />
                <stop offset="100%" stopColor="oklch(0.66 0.24 22)" stopOpacity="0" />
              </linearGradient>
            </defs>
            <motion.path
              d="M 125,42 Q 260,72 375,38 T 625,42 T 875,44"
              fill="none"
              stroke="url(#thread-grad)"
              strokeWidth="2"
              strokeLinecap="round"
              initial={{ pathLength: 0 }}
              whileInView={{ pathLength: 1 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 2.4, ease: "easeInOut" }}
              style={{ filter: "drop-shadow(0 0 6px oklch(0.66 0.24 22 / 0.7))" }}
            />
          </svg>

          <div className="grid gap-10 md:grid-cols-4 md:gap-6">
            {stages.map((s, i) => (
              <motion.div
                key={s.n}
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ delay: i * 0.15, duration: 0.6 }}
                className="relative pt-10"
              >
                {/* Thread anchor / pushpin */}
                <div className="pin-thumbtack absolute left-1/2 top-4 z-20 -translate-x-1/2" aria-hidden />
                {/* Short string from pin down to card */}
                <div
                  className="absolute left-1/2 top-6 z-10 h-6 w-px -translate-x-1/2"
                  style={{
                    background:
                      "linear-gradient(180deg, oklch(0.66 0.24 22 / 0.9), oklch(0.66 0.24 22 / 0.3))",
                  }}
                  aria-hidden
                />

                {/* Dark evidence card */}
                <div
                  className="glass evidence-card relative overflow-hidden rounded-2xl p-6"
                  style={{ transform: `rotate(${s.tilt}deg)` }}
                >
                  {/* Corner clip marker */}
                  <div
                    className="absolute -right-6 -top-6 h-16 w-16 rotate-45"
                    style={{
                      background:
                        "linear-gradient(180deg, oklch(0.66 0.24 22 / 0.35), transparent)",
                    }}
                    aria-hidden
                  />

                  {/* Header: evidence code + icon */}
                  <div className="flex items-center justify-between">
                    <div className="font-mono text-[10px] tracking-[0.3em] text-[color:var(--crimson-glow)]">
                      EVIDENCE&nbsp;·&nbsp;{s.n}
                    </div>
                    <div className="grid h-9 w-9 place-items-center rounded-lg border border-white/10 bg-[color:var(--crimson)]/10 text-[color:var(--crimson-glow)]">
                      <s.Icon className="h-4 w-4" />
                    </div>
                  </div>

                  {/* Stage number watermark */}
                  <div
                    className="pointer-events-none absolute -bottom-4 right-3 font-brand text-[80px] font-black leading-none opacity-[0.06]"
                    aria-hidden
                  >
                    {s.n}
                  </div>

                  <div className="mt-6 font-brand text-[15px] font-bold uppercase tracking-wider text-foreground">
                    {s.title}
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {s.text}
                  </p>

                  {/* Bottom accent line */}
                  <div className="mt-5 flex items-center gap-2">
                    <span className="h-px flex-1 bg-gradient-to-r from-[color:var(--crimson)]/70 to-transparent" />
                    <span className="font-mono text-[9px] tracking-[0.3em] text-foreground/40">
                      STEP&nbsp;{s.n}
                    </span>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
