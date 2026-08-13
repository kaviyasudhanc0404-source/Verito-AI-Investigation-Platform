import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Check, Loader2, AlertTriangle, Scan, Layers, Clock, Link2, User, FileText } from "lucide-react";
import { AGENTS, type AgentRun, type AgentLine } from "@/lib/investigation";

const iconFor = {
  collector: Layers,
  analyzer: Scan,
  timeline: Clock,
  linker: Link2,
  subject: User,
  report: FileText,
} as const;

function LineRow({ line }: { line: AgentLine }) {
  const color =
    line.kind === "ok"
      ? "text-[color:var(--gold)]"
      : line.kind === "warn"
        ? "text-amber-400"
        : line.kind === "scan"
          ? "text-[color:var(--crimson-glow)]"
          : "text-foreground/70";
  const glyph = line.kind === "ok" ? "✓" : line.kind === "warn" ? "!" : "›";
  return (
    <motion.div
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      className="flex items-start gap-2 font-mono text-[12.5px] leading-relaxed"
    >
      <span className={`mt-[2px] w-3 shrink-0 ${color}`}>{glyph}</span>
      <span className="text-foreground/85">{line.text}</span>
    </motion.div>
  );
}

export function AgentCard({ run, index }: { run: AgentRun; index: number }) {
  const meta = AGENTS[index];
  const Icon = iconFor[meta.id];
  const [open, setOpen] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [run.lines.length]);

  const statusChip =
    run.status === "done" ? (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-400/10 px-2 py-0.5 text-[10px] font-medium tracking-widest text-emerald-300">
        <Check className="h-3 w-3" /> COMPLETE
      </span>
    ) : run.status === "running" ? (
      <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--crimson)]/15 px-2 py-0.5 text-[10px] font-medium tracking-widest text-[color:var(--crimson-glow)]">
        <Loader2 className="h-3 w-3 animate-spin" /> RUNNING
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 rounded-full bg-white/5 px-2 py-0.5 text-[10px] font-medium tracking-widest text-foreground/50">
        QUEUED
      </span>
    );

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className={`glass relative overflow-hidden rounded-2xl ${
        run.status === "running" ? "crimson-glow" : ""
      }`}
    >
      {run.status === "running" && (
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px overflow-hidden">
          <div className="h-px w-1/3 animate-laser bg-gradient-to-r from-transparent via-[color:var(--crimson-glow)] to-transparent" />
        </div>
      )}
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 p-4 text-left"
      >
        <div
          className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl border ${
            run.status === "done"
              ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
              : run.status === "running"
                ? "border-[color:var(--crimson)]/40 bg-[color:var(--crimson)]/10 text-[color:var(--crimson-glow)]"
                : "border-white/10 bg-white/5 text-foreground/50"
          }`}
        >
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] tracking-widest text-foreground/40">
              0{index + 1}
            </span>
            <span className="truncate text-sm font-semibold">{meta.name}</span>
            {statusChip}
          </div>
          <div className="mt-0.5 truncate text-xs text-muted-foreground">{meta.role}</div>
        </div>
        {run.summary.length > 0 && (
          <div className="hidden shrink-0 gap-1 md:flex">
            {run.summary.slice(0, 2).map((s, i) => (
              <span
                key={i}
                className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-[10px] text-foreground/70"
              >
                {s}
              </span>
            ))}
          </div>
        )}
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-foreground/50 transition ${open ? "rotate-180" : ""}`}
        />
      </button>

      <AnimatePresence initial={false}>
        {open && run.lines.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div
              ref={scrollRef}
              className="max-h-64 space-y-1 overflow-y-auto border-t border-white/5 bg-black/20 px-5 py-4"
            >
              {run.lines.map((l, i) => (
                <LineRow key={i} line={l} />
              ))}
              {run.status === "running" && (
                <div className="flex items-center gap-2 pt-1 text-[11px] text-foreground/40">
                  <Loader2 className="h-3 w-3 animate-spin" /> processing…
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export function AgentStream({ runs }: { runs: AgentRun[] }) {
  const complete = runs.filter((r) => r.status === "done").length;
  const total = runs.length || AGENTS.length;
  const pct = Math.round((complete / total) * 100);

  return (
    <div className="space-y-4">
      <div className="glass flex items-center justify-between rounded-xl px-4 py-3">
        <div>
          <div className="font-mono text-[10px] tracking-[0.3em] text-[color:var(--crimson-glow)]">
            {complete === total ? "INVESTIGATION COMPLETE" : "INVESTIGATION IN PROGRESS"}
          </div>
          <div className="mt-1 text-sm font-semibold">
            {complete} / {total} AGENTS COMPLETE
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative h-1.5 w-40 overflow-hidden rounded-full bg-white/5">
            <motion.div
              className="absolute inset-y-0 left-0 bg-gradient-to-r from-[color:var(--crimson)] to-[color:var(--gold)]"
              animate={{ width: `${pct}%` }}
              transition={{ type: "spring", stiffness: 80, damping: 20 }}
            />
          </div>
          <div className="w-10 text-right font-mono text-sm">{pct}%</div>
        </div>
      </div>

      {/* Vertical connecting thread */}
      <div className="relative pl-1">
        <div className="pointer-events-none absolute left-[27px] top-0 bottom-0 w-px bg-gradient-to-b from-transparent via-[color:var(--crimson)]/30 to-transparent" />
        <div className="space-y-4">
          {runs.map((r, i) => (
            <AgentCard key={r.id} run={r} index={i} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function AgentEmpty() {
  return (
    <div className="glass rounded-2xl p-6 text-center text-sm text-muted-foreground">
      <AlertTriangle className="mx-auto mb-2 h-5 w-5 text-[color:var(--gold)]" />
      Submit a case to activate the investigation agents.
    </div>
  );
}
