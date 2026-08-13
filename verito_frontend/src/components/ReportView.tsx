import { motion } from "framer-motion";
import type { FinalReport } from "@/lib/investigation";
import { Link2, Users, Clock, ScrollText, HelpCircle, Sparkles } from "lucide-react";

export function ReportView({ report, onRef }: { report: FinalReport; onRef?: (id: string) => void }) {
  const RefBadge = ({ id }: { id: string }) => (
    <button
      onClick={() => onRef?.(id)}
      className="mx-0.5 inline-flex items-center rounded-md border border-[color:var(--crimson)]/30 bg-[color:var(--crimson)]/10 px-1.5 py-0.5 font-mono text-[10px] tracking-widest text-[color:var(--crimson-glow)] transition hover:bg-[color:var(--crimson)]/20"
    >
      {id}
    </button>
  );

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6 }}
      className="glass overflow-hidden rounded-2xl"
    >
      <div className="relative flex items-center justify-between border-b border-white/10 px-6 py-5">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px overflow-hidden">
          <div className="h-px w-1/3 animate-laser bg-gradient-to-r from-transparent via-[color:var(--gold)] to-transparent" />
        </div>
        <div>
          <div className="font-mono text-[10px] tracking-[0.3em] text-[color:var(--gold)]">
            VERITO&nbsp;·&nbsp;FINAL&nbsp;REPORT
          </div>
          <div className="mt-1 text-lg font-semibold">Case Reconstruction</div>
        </div>
        <Sparkles className="h-5 w-5 text-[color:var(--gold)]" />
      </div>

      <div className="grid gap-6 p-6 md:grid-cols-2">
        <section className="md:col-span-2">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
            <ScrollText className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" /> CASE SUMMARY
          </div>
          <p className="text-sm leading-relaxed text-foreground/90">{report.summary}</p>
        </section>

        <section>
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
            <Sparkles className="h-3.5 w-3.5 text-[color:var(--gold)]" /> KEY FINDINGS
          </div>
          <ul className="space-y-2 text-sm">
            {report.findings.map((f, i) => (
              <li key={i} className="flex gap-2 rounded-lg border border-white/5 bg-white/[0.02] p-3">
                <span className="mt-0.5 font-mono text-[10px] tracking-widest text-[color:var(--crimson-glow)]">
                  0{i + 1}
                </span>
                <span className="text-foreground/90">{f}</span>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
            <Clock className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" /> TIMELINE
          </div>
          <div className="relative space-y-3 border-l border-[color:var(--crimson)]/30 pl-4">
            {report.timeline.map((t, i) => (
              <div key={i} className="relative">
                <span className="absolute -left-[21px] top-2 h-2 w-2 rounded-full bg-[color:var(--crimson-glow)] shadow-[0_0_10px] shadow-[color:var(--crimson)]" />
                <div className="font-mono text-[11px] tracking-widest text-[color:var(--gold)]">
                  {t.time}
                </div>
                <div className="text-sm text-foreground/90">
                  {t.event}{" "}
                  {t.refs.map((r) => (
                    <RefBadge key={r} id={r} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
            <Link2 className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" /> EVIDENCE CONNECTIONS
          </div>
          <ul className="space-y-2 text-sm">
            {report.connections.map((c, i) => (
              <li key={i} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                <div className="flex items-center gap-2">
                  <RefBadge id={c.a} />
                  <span className="text-foreground/40">↔</span>
                  <RefBadge id={c.b} />
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{c.reason}</div>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
            <Users className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" /> PERSONS OF INTEREST
          </div>
          <ul className="space-y-2 text-sm">
            {report.persons.map((p, i) => (
              <li key={i} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                <div className="font-semibold">{p.name}</div>
                <div className="text-xs text-muted-foreground">{p.note}</div>
              </li>
            ))}
          </ul>
        </section>

        <section className="md:col-span-2">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
            <HelpCircle className="h-3.5 w-3.5 text-amber-400" /> UNRESOLVED QUESTIONS
          </div>
          <ul className="grid gap-2 md:grid-cols-2">
            {report.unresolved.map((u, i) => (
              <li
                key={i}
                className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3 text-sm text-foreground/90"
              >
                {u}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </motion.div>
  );
}
