export type AgentId =
  "collector" | "analyzer" | "timeline" | "linker" | "validator" | "subject" | "report";

export interface AgentDef {
  id: AgentId;
  name: string;
  role: string;
  icon: string;
}

export const AGENTS: AgentDef[] = [
  {
    id: "collector",
    name: "Evidence Collector",
    role: "Indexes uploaded evidence",
    icon: "layers",
  },
  { id: "analyzer", name: "Evidence Analyzer", role: "Extracts entities & facts", icon: "scan" },
  { id: "timeline", name: "Timeline Builder", role: "Reconstructs events", icon: "clock" },
  { id: "linker", name: "Evidence Linker", role: "Connects related clues", icon: "link" },
  {
    id: "validator",
    name: "Finding Validator",
    role: "Re-checks every finding",
    icon: "shield",
  },
  { id: "subject", name: "Subject Analyzer", role: "Profiles persons of interest", icon: "user" },
  { id: "report", name: "Report Generator", role: "Assembles final findings", icon: "file" },
];

export type LineKind = "info" | "ok" | "scan" | "warn" | "error";

export interface AgentLine {
  kind: LineKind;
  text: string;
}

export interface AgentRun {
  id: AgentId;
  status:
    | "idle"
    | "running"
    | "done"
    | "completed_with_warnings"
    | "completed_with_fallback"
    | "failed"
    | "blocked";
  lines: AgentLine[];
  summary: string[];
}

/** Statuses that count as a completed agent (with or without caveats). */
export const FINISHED_STATUSES: AgentRun["status"][] = [
  "done",
  "completed_with_warnings",
  "completed_with_fallback",
];

export interface CaseRecord {
  id: string;
  title: string;
  prompt: string;
  createdAt: number;
  status: "processing" | "complete" | "failed";
  evidence: EvidenceItem[];
  runs: AgentRun[];
  report?: FinalReport;
  chat: ChatMessage[];
}

export interface EvidenceItem {
  id: string;
  name: string;
  kind: "doc" | "image" | "audio" | "text" | "chat" | "map" | "video";
  size?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "verito";
  text: string;
  refs?: string[];
}

export type Severity = "high" | "medium" | "low";

export interface ReportExcerpt {
  ref: string;
  location: string;
  text: string;
}

export interface ReportFinding {
  id: string;
  title: string;
  detail: string;
  why: string;
  severity: Severity;
  severity_basis: string;
  category: string;
  claim: string;
  confidence: string;
  confidence_basis: string;
  refs: string[];
  records: string[];
  excerpts: ReportExcerpt[];
  calculation: { description: string; text: string } | null;
  qualifiers: string[];
  verify: string[];
  source: string;
}

export interface ReportEvent {
  date: string;
  time: string;
  zone: string;
  approximate: boolean;
  original: string;
  date_type: string;
  event: string;
  category: string;
  refs: string[];
}

export interface ReportPerson {
  name: string;
  role: string;
  category: string;
  involvement: string;
  involvement_basis: string;
  note: string;
  context: string[];
  identity_note: string;
  refs: string[];
}

export interface ReportConnection {
  a: string;
  b: string;
  a_name: string;
  b_name: string;
  type: string;
  method: string;
  reason: string;
  strength: string;
  excerpts: { ref: string; text: string }[];
}

export interface ReportEvidence {
  id: string;
  name: string;
  description: string;
  status: string;
  records: number;
  chunks: number;
  notes: string[];
}

export interface StageDiagnostics {
  name: string;
  status: AgentRun["status"];
  duration_ms: number;
  warnings: number;
  fallbacks: number;
  counts: Record<string, number>;
  llm: { calls: number; failures: number; prompt_tokens: number; completion_tokens: number };
}

/** The final investigation report — mirrors backend schemas/report.py. */
export interface FinalReport {
  title: string;
  status: "final" | "partial";
  summary: string;
  executive_summary: {
    establishes: string[];
    suggests: string[];
    uncertain: string[];
    verify_next: string[];
  };
  at_a_glance: { label: string; value: string }[];
  scope: string[];
  findings: ReportFinding[];
  reconciliation: { check: string; result: string; refs: string[] }[];
  timeline: ReportEvent[];
  all_events: ReportEvent[];
  people: ReportPerson[];
  connections: ReportConnection[];
  open_questions: string[];
  next_steps: string[];
  evidence: ReportEvidence[];
  limitations: string[];
  methodology: string[];
  quality: {
    status: "passed" | "warning" | "partial" | "blocked";
    policy: string;
    reasons: string[];
  };
  withheld: { id: string; title: string; reasons: string[] }[];
  diagnostics: Partial<Record<AgentId, StageDiagnostics>>;
  generated_by: string;
  generated_at: string;
}
