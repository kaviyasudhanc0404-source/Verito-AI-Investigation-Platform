import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  ClipboardList,
  Clock,
  Download,
  FileText,
  GitCompare,
  HelpCircle,
  Info,
  Link2,
  ListChecks,
  ScrollText,
  Scale,
  Target,
  Users,
} from "lucide-react";
import { reportsApi, type ReportFormat } from "@/lib/api";
import {
  AGENTS,
  type FinalReport,
  type ReportEvent,
  type ReportFinding,
  type Severity,
} from "@/lib/investigation";

const SEVERITY_STYLE: Record<Severity, string> = {
  high: "border-[color:var(--crimson)]/50 bg-[color:var(--crimson)]/15 text-[color:var(--crimson-glow)]",
  medium: "border-amber-400/40 bg-amber-400/10 text-amber-300",
  low: "border-white/15 bg-white/5 text-foreground/70",
};
const NEUTRAL = "border-white/10 bg-white/[0.04] text-foreground/70";
const GOOD = "border-emerald-400/30 bg-emerald-400/10 text-emerald-300";

const INVOLVEMENT_STYLE: Record<string, string> = {
  High: SEVERITY_STYLE.high,
  Medium: SEVERITY_STYLE.medium,
  Low: SEVERITY_STYLE.low,
};

const SUMMARY_PARTS = [
  ["establishes", "What the records establish"],
  ["suggests", "What they suggest (needs review)"],
  ["uncertain", "What remains uncertain"],
  ["verify_next", "What to verify next"],
] as const;

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Info;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <h3 className="flex items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
        <Icon className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" /> {title.toUpperCase()}
      </h3>
      {children}
    </section>
  );
}

function Pill({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wider ${className}`}
    >
      {children}
    </span>
  );
}

function Bullets({ items, ordered = false }: { items: string[]; ordered?: boolean }) {
  const List = ordered ? "ol" : "ul";
  return (
    <List
      className={`${ordered ? "list-decimal" : "list-disc"} space-y-1.5 pl-5 text-sm text-foreground/85`}
    >
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </List>
  );
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <p className="text-sm leading-relaxed text-foreground/70">
      <span className="font-medium text-foreground/80">{label}: </span>
      {children}
    </p>
  );
}

function groupByDate(events: ReportEvent[]) {
  const groups: { date: string; events: ReportEvent[] }[] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if (last && last.date === event.date) last.events.push(event);
    else groups.push({ date: event.date, events: [event] });
  }
  return groups;
}

export function ReportView({
  report,
  investigationId,
}: {
  report: FinalReport;
  investigationId: string;
}) {
  const [showAllEvents, setShowAllEvents] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const fileNames = useMemo(
    () => new Map(report.evidence.map((e) => [e.id, e.name])),
    [report.evidence],
  );
  const events = showAllEvents && report.all_events.length ? report.all_events : report.timeline;
  const summary = report.executive_summary;
  const stages = AGENTS.flatMap((agent) => {
    const stage = report.diagnostics?.[agent.id];
    return stage ? [{ id: agent.id, ...stage }] : [];
  });

  const download = async (format: ReportFormat) => {
    setDownloadError(null);
    try {
      await reportsApi.download(investigationId, format);
    } catch {
      setDownloadError("The report could not be downloaded. Please try again.");
    }
  };

  const Refs = ({ refs }: { refs: string[] }) => (
    <span className="inline-flex flex-wrap gap-1">
      {refs.map((id) => (
        <span
          key={id}
          title={fileNames.get(id) ?? id}
          className="rounded-md border border-[color:var(--crimson)]/30 bg-[color:var(--crimson)]/10 px-1.5 py-0.5 font-mono text-[10px] tracking-widest text-[color:var(--crimson-glow)]"
        >
          {id}
        </span>
      ))}
    </span>
  );

  const FindingCard = ({ finding }: { finding: ReportFinding }) => (
    <li className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] tracking-widest text-[color:var(--crimson-glow)]">
            {finding.id}
          </span>
          <Pill className={SEVERITY_STYLE[finding.severity]}>{finding.severity.toUpperCase()}</Pill>
          {finding.claim && <Pill className={NEUTRAL}>{finding.claim.toUpperCase()}</Pill>}
          {finding.confidence && (
            <Pill className={NEUTRAL}>{finding.confidence.toUpperCase()} CONFIDENCE</Pill>
          )}
        </div>
        <p className="font-semibold leading-snug">{finding.title}</p>
        {finding.detail && (
          <p className="text-sm leading-relaxed text-foreground/85">{finding.detail}</p>
        )}
        {finding.why && <Labelled label="Why this matters">{finding.why}</Labelled>}
        {finding.calculation?.text && (
          <Labelled label="Calculation">{finding.calculation.text}</Labelled>
        )}
        {finding.qualifiers.length > 0 && (
          <Labelled label="Context">{finding.qualifiers.join(" ")}</Labelled>
        )}
        {finding.verify.length > 0 && (
          <Labelled label="How to verify">{finding.verify.join(" ")}</Labelled>
        )}
        {finding.confidence_basis && (
          <p className="text-xs text-foreground/50">Confidence basis: {finding.confidence_basis}</p>
        )}
        <div className="flex flex-wrap items-center gap-2 text-xs text-foreground/50">
          {finding.refs.length > 0 && (
            <>
              Sources: <Refs refs={finding.refs} />
            </>
          )}
        </div>
        {finding.excerpts.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer font-medium text-[color:var(--gold)]">
              Show source records ({finding.excerpts.length})
            </summary>
            <ul className="mt-2 space-y-1.5">
              {finding.excerpts.map((excerpt, i) => (
                <li key={i} className="rounded-lg bg-black/20 p-2 font-mono text-[11px]">
                  <span className="text-[color:var(--gold)]">
                    {excerpt.ref} · {excerpt.location}
                  </span>
                  <div className="mt-0.5 break-words text-foreground/75">{excerpt.text}</div>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </li>
  );

  return (
    <motion.article
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="glass overflow-hidden rounded-2xl"
    >
      {/* Header */}
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-white/10 px-6 py-5">
        <div className="min-w-0">
          <div className="font-mono text-[10px] tracking-[0.3em] text-[color:var(--gold)]">
            VERITO · {report.status === "partial" ? "PARTIAL REPORT" : "FINAL REPORT"}
          </div>
          <h2 className="mt-1 text-xl font-semibold leading-snug">
            {report.title || "Investigation report"}
          </h2>
          <div className="mt-1 text-xs text-muted-foreground">
            {report.generated_at && `Prepared ${new Date(report.generated_at).toLocaleString()} · `}
            {report.generated_by === "ai"
              ? "AI-assisted wording, every finding re-checked against its evidence"
              : "Written from rule-based checks"}
          </div>
        </div>
        <div className="flex gap-2">
          {(["pdf", "docx"] as const).map((format) => (
            <button
              key={format}
              onClick={() => download(format)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-medium transition hover:bg-white/10"
            >
              <Download className="h-3.5 w-3.5" /> {format === "pdf" ? "PDF" : "Word"}
            </button>
          ))}
        </div>
        {downloadError && <p className="w-full text-xs text-red-400">{downloadError}</p>}
      </header>

      <div className="space-y-8 p-6">
        {report.status === "partial" && (
          <div className="flex gap-2 rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <span className="font-semibold">Partial report. </span>
              {report.quality.reasons.join(" ")}
            </div>
          </div>
        )}

        <Section icon={ScrollText} title="Summary">
          <p className="text-[15px] leading-relaxed text-foreground/90">{report.summary}</p>
        </Section>

        {SUMMARY_PARTS.some(([key]) => summary[key].length > 0) && (
          <Section icon={Scale} title="What the evidence shows">
            <div className="grid gap-3 md:grid-cols-2">
              {SUMMARY_PARTS.filter(([key]) => summary[key].length > 0).map(([key, label]) => (
                <div key={key} className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <div className="mb-2 text-[10px] font-semibold tracking-widest text-foreground/50">
                    {label.toUpperCase()}
                  </div>
                  <Bullets items={summary[key]} />
                </div>
              ))}
            </div>
          </Section>
        )}

        {report.at_a_glance.length > 0 && (
          <Section icon={ClipboardList} title="At a glance">
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {report.at_a_glance.map((item) => (
                <div
                  key={item.label}
                  className="rounded-xl border border-white/5 bg-white/[0.03] p-3"
                >
                  <dt className="text-[10px] font-semibold tracking-widest text-foreground/50">
                    {item.label.toUpperCase()}
                  </dt>
                  <dd className="mt-1 text-sm text-foreground/90">{item.value}</dd>
                </div>
              ))}
            </dl>
          </Section>
        )}

        {report.scope.length > 0 && (
          <Section icon={Target} title="Scope">
            <Bullets items={report.scope} />
          </Section>
        )}

        {report.findings.length > 0 && (
          <Section icon={AlertTriangle} title="Key findings">
            <ol className="space-y-3">
              {report.findings.map((finding) => (
                <FindingCard key={finding.id || finding.title} finding={finding} />
              ))}
            </ol>
          </Section>
        )}

        {report.reconciliation.length > 0 && (
          <Section icon={GitCompare} title="Reconciliation results">
            <ul className="divide-y divide-white/5 rounded-xl border border-white/5">
              {report.reconciliation.map((row, i) => (
                <li key={i} className="grid gap-1 px-3 py-2 text-sm sm:grid-cols-[200px_1fr]">
                  <span className="text-xs font-medium text-foreground/60">{row.check}</span>
                  <span className="text-foreground/85">
                    {row.result} {row.refs.length > 0 && <Refs refs={row.refs} />}
                  </span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        {events.length > 0 && (
          <Section icon={Clock} title={showAllEvents ? "Full timeline" : "Key events"}>
            <div className="space-y-4">
              {groupByDate(events).map((group) => (
                <div key={group.date} className="grid gap-2 sm:grid-cols-[110px_1fr]">
                  <div className="font-mono text-[11px] tracking-widest text-[color:var(--gold)] sm:pt-1">
                    {group.date}
                  </div>
                  <ul className="space-y-2 border-l border-[color:var(--crimson)]/30 pl-4">
                    {group.events.map((event, i) => (
                      <li key={i} className="text-sm leading-relaxed">
                        <div className="flex flex-wrap items-baseline gap-x-2">
                          {event.time && (
                            <span
                              title={event.original ? `Recorded as ${event.original}` : undefined}
                              className="font-mono text-[11px] text-foreground/60"
                            >
                              {event.time} {event.zone}
                            </span>
                          )}
                          {event.category && (
                            <span className="text-[10px] uppercase tracking-wider text-foreground/40">
                              {event.category}
                            </span>
                          )}
                          {event.approximate && (
                            <span className="text-[10px] uppercase tracking-wider text-amber-300/80">
                              approximate date
                            </span>
                          )}
                        </div>
                        <span className="text-foreground/90">{event.event}</span>{" "}
                        <Refs refs={event.refs} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            {report.all_events.length > report.timeline.length && (
              <button
                onClick={() => setShowAllEvents((v) => !v)}
                className="text-xs font-medium text-[color:var(--gold)] hover:underline"
              >
                {showAllEvents
                  ? "Show key events only"
                  : `Show all ${report.all_events.length} events`}
              </button>
            )}
          </Section>
        )}

        {report.people.length > 0 && (
          <Section icon={Users} title="People involved">
            <div className="grid gap-3 md:grid-cols-2">
              {report.people.map((person) => (
                <div
                  key={person.name}
                  className="rounded-xl border border-white/5 bg-white/[0.02] p-4"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{person.name}</span>
                    {person.involvement && (
                      <Pill className={INVOLVEMENT_STYLE[person.involvement] ?? SEVERITY_STYLE.low}>
                        {person.involvement.toUpperCase()} INVOLVEMENT
                      </Pill>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {[person.role, person.category !== person.role ? person.category : ""]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                  {person.involvement_basis && (
                    <p className="mt-2 text-xs text-foreground/60">{person.involvement_basis}</p>
                  )}
                  <p className="mt-2 text-sm leading-relaxed text-foreground/80">{person.note}</p>
                  {person.context.length > 0 && (
                    <p className="mt-1 text-xs text-foreground/60">
                      Context: {person.context.join(" ")}
                    </p>
                  )}
                  {person.identity_note && (
                    <p className="mt-1 text-xs text-amber-300/80">{person.identity_note}</p>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}

        {report.connections.length > 0 && (
          <Section icon={Link2} title="How the evidence connects">
            <ul className="space-y-2">
              {report.connections.map((link, i) => (
                <li
                  key={i}
                  className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-sm"
                >
                  <div className="flex flex-wrap items-center gap-2 text-foreground/80">
                    <Refs refs={[link.a]} /> {link.a_name}
                    <span className="text-foreground/40">↔</span>
                    <Refs refs={[link.b]} /> {link.b_name}
                    {link.type && <Pill className={NEUTRAL}>{link.type.toUpperCase()}</Pill>}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{link.reason}</p>
                </li>
              ))}
            </ul>
          </Section>
        )}

        {(report.open_questions.length > 0 || report.next_steps.length > 0) && (
          <div className="grid gap-6 md:grid-cols-2">
            {report.open_questions.length > 0 && (
              <Section icon={HelpCircle} title="Open questions">
                <Bullets items={report.open_questions} />
              </Section>
            )}
            {report.next_steps.length > 0 && (
              <Section icon={ListChecks} title="Recommended next steps">
                <Bullets items={report.next_steps} ordered />
              </Section>
            )}
          </div>
        )}

        {report.evidence.length > 0 && (
          <Section icon={FileText} title="Evidence inventory">
            <ul className="divide-y divide-white/5 rounded-xl border border-white/5">
              {report.evidence.map((item) => (
                <li key={item.id} className="space-y-1 px-3 py-2 text-sm">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="font-mono text-[11px] tracking-widest text-[color:var(--gold)]">
                      {item.id}
                    </span>
                    <span className="text-foreground/90">{item.name}</span>
                    <span className="text-xs text-muted-foreground">{item.description}</span>
                    {item.status && (
                      <Pill className={item.status === "indexed" ? GOOD : SEVERITY_STYLE.medium}>
                        {item.status.replace(/_/g, " ").toUpperCase()}
                      </Pill>
                    )}
                  </div>
                  {item.notes.length > 0 && (
                    <p className="text-xs text-amber-300/80">{item.notes.join(" ")}</p>
                  )}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {report.limitations.length > 0 && (
          <div className="flex gap-2 rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-xs text-foreground/70">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
            <div>
              <div className="mb-1 font-semibold tracking-widest text-foreground/60">
                LIMITATIONS
              </div>
              <ul className="space-y-1">
                {report.limitations.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {report.withheld.length > 0 && (
          <Section icon={AlertTriangle} title="Findings withheld by validation">
            <Bullets
              items={report.withheld.map((w) => `${w.id} ${w.title}: ${w.reasons.join("; ")}`)}
            />
          </Section>
        )}

        {report.methodology.length > 0 && (
          <details className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
            <summary className="flex cursor-pointer items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
              <BookOpen className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" /> METHODOLOGY
            </summary>
            <div className="mt-3">
              <Bullets items={report.methodology} />
            </div>
          </details>
        )}

        {stages.length > 0 && (
          <details className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
            <summary className="flex cursor-pointer items-center gap-2 text-xs font-semibold tracking-widest text-foreground/60">
              <Activity className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" /> PROCESSING
              DIAGNOSTICS
            </summary>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[480px] text-left text-xs">
                <thead className="text-foreground/50">
                  <tr>
                    {["Step", "Status", "Time", "AI calls", "Fallbacks", "Warnings"].map((h) => (
                      <th key={h} className="pb-2 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-foreground/80">
                  {stages.map((stage) => (
                    <tr key={stage.id}>
                      <td className="py-1.5">{stage.name}</td>
                      <td>{stage.status.replace(/_/g, " ")}</td>
                      <td>{(stage.duration_ms / 1000).toFixed(1)}s</td>
                      <td>
                        {stage.llm.calls}
                        {stage.llm.failures ? ` (${stage.llm.failures} failed)` : ""}
                      </td>
                      <td>{stage.fallbacks}</td>
                      <td>{stage.warnings}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </div>
    </motion.article>
  );
}
