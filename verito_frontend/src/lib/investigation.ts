export type AgentId =
  | "collector"
  | "analyzer"
  | "timeline"
  | "linker"
  | "subject"
  | "report";

export interface AgentDef {
  id: AgentId;
  name: string;
  role: string;
  icon: string;
}

export const AGENTS: AgentDef[] = [
  { id: "collector", name: "Evidence Collector", role: "Indexes uploaded evidence", icon: "layers" },
  { id: "analyzer", name: "Evidence Analyzer", role: "Extracts entities & facts", icon: "scan" },
  { id: "timeline", name: "Timeline Builder", role: "Reconstructs events", icon: "clock" },
  { id: "linker", name: "Evidence Linker", role: "Connects related clues", icon: "link" },
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
  status: "idle" | "running" | "done" | "completed_with_warnings" | "failed" | "blocked";
  lines: AgentLine[];
  summary: string[];
}

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

export interface FinalReport {
  summary: string;
  findings: string[];
  timeline: { time: string; event: string; refs: string[] }[];
  connections: { a: string; b: string; reason: string }[];
  persons: { name: string; note: string }[];
  unresolved: string[];
}
