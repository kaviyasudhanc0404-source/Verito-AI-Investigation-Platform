import { createFileRoute, redirect } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { HistorySidebar } from "@/components/HistorySidebar";
import { Composer } from "@/components/Composer";
import { AgentStream } from "@/components/AgentStream";
import { ReportView } from "@/components/ReportView";
import { VeritoWordmark } from "@/components/VeritoWordmark";
import { AGENTS, type CaseRecord, type EvidenceItem, type ChatMessage } from "@/lib/investigation";
import { investigationsApi, evidenceApi, chatApi } from "@/lib/api";
import { isAuthenticated, clearToken, getUser } from "@/lib/auth-store";
import { subscribeToInvestigation } from "@/lib/sse";
import { Menu, Fingerprint, X, Shield, Clock, Activity, Database } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import type { StoredUser } from "@/lib/auth-store";

export const Route = createFileRoute("/app")({
  beforeLoad: () => {
    if (!isAuthenticated()) {
      throw redirect({ to: "/auth" });
    }
  },
  component: Workspace,
  head: () => ({
    meta: [
      { title: "VERITO — Workspace" },
      { name: "description", content: "Live AI investigation workspace." },
    ],
  }),
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeEmptyRuns(): CaseRecord["runs"] {
  return AGENTS.map((a) => ({ id: a.id, status: "idle" as const, lines: [], summary: [] }));
}

/** Convert backend InvestigationResponse into the frontend CaseRecord shape */
function backendToCase(inv: Awaited<ReturnType<typeof investigationsApi.get>>): CaseRecord {
  return {
    id: inv.id,
    title: inv.title,
    prompt: inv.prompt,
    createdAt: new Date(inv.created_at).getTime(),
    status: inv.status,
    evidence: inv.evidence.map((e) => ({
      id: e.id,
      name: e.name,
      kind: e.kind as EvidenceItem["kind"],
      size: e.size,
    })),
    runs:
      inv.runs.length > 0
        ? inv.runs.map((r) => ({
            id: r.id as CaseRecord["runs"][number]["id"],
            status: r.status as CaseRecord["runs"][number]["status"],
            lines: r.lines as CaseRecord["runs"][number]["lines"],
            summary: r.summary,
          }))
        : makeEmptyRuns(),
    report: inv.report,
    chat: inv.chat.map((m) => ({
      id: m.id,
      role: m.role,
      text: m.text,
      refs: m.refs,
    })),
  };
}

// ── Workspace Component ───────────────────────────────────────────────────────

function Workspace() {
  const navigate = useNavigate();
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileHist, setMobileHist] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set());
  const [currentUser, setCurrentUser] = useState<StoredUser | null>(getUser);
  const [securityOpen, setSecurityOpen] = useState(false);
  const [sessionStart] = useState(() => Date.now());
  const sseCleanupRef = useRef<(() => void) | null>(null);

  // Load investigations from backend on mount
  useEffect(() => {
    if (!isAuthenticated()) return;
    investigationsApi
      .list()
      .then((list) => {
        const mapped = list.map(
          (item) =>
            ({
              id: item.id,
              title: item.title,
              prompt: "",
              createdAt: new Date(item.created_at).getTime(),
              status: item.status,
              evidence: [],
              runs: makeEmptyRuns(),
              chat: [],
            }) satisfies CaseRecord,
        );
        setCases(mapped);
        setPinnedIds(new Set(list.filter((item) => item.pinned).map((item) => item.id)));
        setActiveId(mapped[0]?.id ?? null);
        setHydrated(true);
      })
      .catch(() => setHydrated(true));
  }, []);

  // Load full case when active changes
  useEffect(() => {
    if (!activeId) return;
    investigationsApi
      .get(activeId)
      .then((inv) => {
        const c = backendToCase(inv);
        setCases((prev) => prev.map((x) => (x.id === activeId ? c : x)));
      })
      .catch(() => {});
  }, [activeId]);

  const active = useMemo(() => cases.find((c) => c.id === activeId) ?? null, [cases, activeId]);

  const updateCase = useCallback((id: string, updater: (c: CaseRecord) => CaseRecord) => {
    setCases((prev) => prev.map((c) => (c.id === id ? updater(c) : c)));
  }, []);

  const handleNew = () => {
    setActiveId(null);
    setMobileHist(false);
    sseCleanupRef.current?.();
    sseCleanupRef.current = null;
  };

  const handleSelect = (id: string) => {
    setActiveId(id);
    setMobileHist(false);
    sseCleanupRef.current?.();
    sseCleanupRef.current = null;
  };

  const handleDelete = async (id: string) => {
    try {
      await investigationsApi.delete(id);
    } catch {
      return;
    }
    setCases((prev) => prev.filter((c) => c.id !== id));
    setPinnedIds((prev) => {
      const s = new Set(prev);
      s.delete(id);
      return s;
    });
    if (activeId === id) setActiveId(null);
  };

  const handleRename = async (id: string, title: string) => {
    const previousTitle = cases.find((c) => c.id === id)?.title ?? "Untitled case";
    setCases((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
    try {
      await investigationsApi.rename(id, title);
    } catch {
      setCases((prev) => prev.map((c) => (c.id === id ? { ...c, title: previousTitle } : c)));
    }
  };

  const handlePin = (id: string) => {
    const nextPinned = !pinnedIds.has(id);
    setPinnedIds((prev) => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
    investigationsApi.update(id, { pinned: nextPinned }).catch(() => {
      setPinnedIds((prev) => {
        const s = new Set(prev);
        if (nextPinned) s.delete(id);
        else s.add(id);
        return s;
      });
    });
  };

  const handleLogout = () => {
    clearToken();
    navigate({ to: "/" });
  };

  const handleUserUpdate = (updated: StoredUser) => {
    setCurrentUser(updated);
  };

  const startInvestigation = async (
    text: string,
    evidenceFiles: EvidenceItem[],
    rawFiles?: File[],
  ) => {
    const title = text.slice(0, 60) || "Untitled case";

    // 1. Create the investigation in the backend
    const inv = await investigationsApi.create(title, text);

    // 2. Build optimistic case for immediate UI feedback
    const optimisticCase: CaseRecord = {
      id: inv.id,
      title,
      prompt: text,
      createdAt: new Date(inv.created_at).getTime(),
      status: "processing",
      evidence: [],
      runs: makeEmptyRuns(),
      chat: [],
    };
    setCases((prev) => [optimisticCase, ...prev]);
    setActiveId(inv.id);

    // 3. Upload evidence files (if any)
    if (rawFiles && rawFiles.length > 0) {
      try {
        const uploaded = await evidenceApi.upload(inv.id, rawFiles);
        updateCase(inv.id, (c) => ({
          ...c,
          evidence: uploaded.map((e) => ({
            id: e.id,
            name: e.name,
            kind: e.kind as EvidenceItem["kind"],
            size: e.size,
          })),
        }));
      } catch (err) {
        console.error("Evidence upload failed:", err);
      }
    }

    // 4. Subscribe to SSE stream — live agent updates
    sseCleanupRef.current?.();
    sseCleanupRef.current = subscribeToInvestigation(
      inv.id,
      (updater) => updateCase(inv.id, updater),
      async () => {
        // Pipeline complete — fetch the full updated investigation
        try {
          const updated = await investigationsApi.get(inv.id);
          const c = backendToCase(updated);
          setCases((prev) => prev.map((x) => (x.id === inv.id ? c : x)));
        } catch (err) {
          console.error("Could not load the finished investigation:", err);
        }
      },
    );
  };

  const sendFollowup = async (text: string, _ev: EvidenceItem[]) => {
    if (!active || active.status !== "complete") return;

    // Optimistic user message
    const tempUserId = "m_" + Math.random().toString(36).slice(2, 8);
    updateCase(active.id, (c) => ({
      ...c,
      chat: [...c.chat, { id: tempUserId, role: "user", text }],
    }));

    try {
      const msgs = await chatApi.send(active.id, text);
      // Replace optimistic + add AI reply
      updateCase(active.id, (c) => ({
        ...c,
        chat: [
          ...c.chat.filter((m) => m.id !== tempUserId),
          ...msgs.map((m) => ({ id: m.id, role: m.role, text: m.text, refs: m.refs })),
        ],
      }));
    } catch (err) {
      // Remove optimistic message on error
      updateCase(active.id, (c) => ({
        ...c,
        chat: c.chat.filter((m) => m.id !== tempUserId),
      }));
    }
  };

  if (!hydrated) return null;

  return (
    <div className="flex h-[100svh] w-full overflow-hidden">
      {/* Desktop sidebar */}
      <div className="hidden md:flex">
        <HistorySidebar
          cases={cases}
          activeId={activeId}
          open={sidebarOpen}
          onToggle={() => setSidebarOpen((v) => !v)}
          onSelect={handleSelect}
          onNew={handleNew}
          onDelete={handleDelete}
          onRename={handleRename}
          onPin={handlePin}
          pinnedIds={pinnedIds}
          user={currentUser}
          onLogout={handleLogout}
          onUserUpdate={handleUserUpdate}
        />
      </div>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileHist && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-40 bg-black/60 md:hidden"
              onClick={() => setMobileHist(false)}
            />
            <motion.div
              initial={{ x: "-100%" }}
              animate={{ x: 0 }}
              exit={{ x: "-100%" }}
              transition={{ type: "spring", damping: 26, stiffness: 240 }}
              className="fixed inset-y-0 left-0 z-50 md:hidden"
            >
              <HistorySidebar
                cases={cases}
                activeId={activeId}
                open={true}
                onToggle={() => setMobileHist(false)}
                onSelect={handleSelect}
                onNew={handleNew}
                onDelete={handleDelete}
                onRename={handleRename}
                onPin={handlePin}
                pinnedIds={pinnedIds}
                user={currentUser}
                onLogout={handleLogout}
                onUserUpdate={handleUserUpdate}
              />
            </motion.div>
          </>
        )}
      </AnimatePresence>

      {/* Main */}
      <main className="relative flex min-w-0 flex-1 flex-col">
        <div className="grid-bg pointer-events-none absolute inset-0" aria-hidden />

        <header className="relative flex items-center justify-between border-b border-white/5 px-4 py-3">
          <button
            onClick={() => setMobileHist(true)}
            className="grid h-9 w-9 place-items-center rounded-md text-foreground/70 hover:bg-white/5 md:hidden"
            aria-label="Open history"
          >
            <Menu className="h-4 w-4" />
          </button>
          <div className="min-w-0 flex-1 truncate px-2 text-sm text-foreground/80">
            {active ? active.title : "New investigation"}
          </div>
          {/* Fingerprint / Security button */}
          <button
            type="button"
            aria-label="Security panel"
            onClick={() => setSecurityOpen((v) => !v)}
            className={`glass group mr-2 grid h-8 w-8 place-items-center rounded-full transition ${
              securityOpen
                ? "text-foreground bg-[color:var(--crimson)]/20"
                : "text-[color:var(--crimson-glow)] hover:text-foreground"
            }`}
          >
            <Fingerprint className="h-4 w-4 transition-transform group-hover:scale-110" />
          </button>
        </header>

        {/* Security Panel Drawer */}
        <AnimatePresence>
          {securityOpen && (
            <SecurityPanel
              user={currentUser}
              caseCount={cases.length}
              sessionStart={sessionStart}
              onClose={() => setSecurityOpen(false)}
            />
          )}
        </AnimatePresence>

        <div className="relative flex-1 overflow-y-auto">
          {!active ? (
            <EmptyWorkspace onStart={startInvestigation} />
          ) : (
            <ActiveCase caseRec={active} onSubmit={sendFollowup} />
          )}
        </div>
      </main>
    </div>
  );
}

// ── SecurityPanel ────────────────────────────────────────────────────────────

function SecurityPanel({
  user,
  caseCount,
  sessionStart,
  onClose,
}: {
  user: StoredUser | null;
  caseCount: number;
  sessionStart: number;
  onClose: () => void;
}) {
  const elapsed = Math.floor((Date.now() - sessionStart) / 60000);
  const sessionLabel =
    elapsed < 1
      ? "<1m"
      : elapsed < 60
        ? `${elapsed}m`
        : `${Math.floor(elapsed / 60)}h${elapsed % 60}m`;
  const initials =
    user?.name
      ?.split(" ")
      .map((w) => w[0])
      .join("")
      .slice(0, 2)
      .toUpperCase() ?? "?";

  return (
    <motion.div
      initial={{ opacity: 0, y: -6, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -6, scale: 0.97 }}
      transition={{ type: "spring", damping: 28, stiffness: 300 }}
      className="absolute right-3 top-[52px] z-50 w-64 overflow-hidden rounded-xl border border-white/10 bg-[#0f0f12] shadow-2xl"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/5 px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Shield className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" />
          <div>
            <div className="font-mono text-[9px] tracking-[0.25em] text-foreground/40">
              ACCESS · POINT
            </div>
            <div className="text-xs font-semibold text-foreground leading-tight">
              Security & Session
            </div>
          </div>
        </div>
        <button
          onClick={onClose}
          className="grid h-6 w-6 place-items-center rounded-full text-foreground/40 transition hover:bg-white/5 hover:text-foreground"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* Agent identity — compact inline row */}
      <div className="flex items-center gap-2.5 border-b border-white/5 px-3 py-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[color:var(--crimson)] to-[color:var(--crimson-glow)] text-xs font-bold text-white">
          {initials}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold text-foreground">
            {user?.name ?? "Unknown"}
          </div>
          <div className="truncate font-mono text-[9px] text-foreground/40">
            {user?.email ?? "—"}
          </div>
        </div>
        <span className="flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          <span className="font-mono text-[9px] text-emerald-400">LIVE</span>
        </span>
      </div>

      {/* Stats — compact 3-col */}
      <div className="grid grid-cols-3 divide-x divide-white/5 border-b border-white/5">
        <StatCell icon={<Clock className="h-3 w-3" />} label="Session" value={sessionLabel} />
        <StatCell icon={<Database className="h-3 w-3" />} label="Cases" value={String(caseCount)} />
        <StatCell icon={<Activity className="h-3 w-3" />} label="Status" value="Live" green />
      </div>

      {/* Security rows */}
      <div className="px-3 py-2.5 space-y-1">
        <div className="font-mono text-[9px] tracking-[0.25em] text-foreground/30 mb-1.5">
          SECURITY
        </div>
        {[
          { label: "Encryption", value: "AES-256" },
          { label: "Auth", value: "JWT Bearer" },
          { label: "Transport", value: "TLS 1.3" },
        ].map((row) => (
          <div key={row.label} className="flex items-center justify-between py-1">
            <span className="text-[11px] text-foreground/45">{row.label}</span>
            <div className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              <span className="font-mono text-[10px] text-foreground/65">{row.value}</span>
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

function StatCell({
  icon,
  label,
  value,
  green,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  green?: boolean;
}) {
  return (
    <div className="flex flex-col items-center gap-0.5 px-2 py-2">
      <span className={`${green ? "text-emerald-400" : "text-[color:var(--crimson-glow)]"}`}>
        {icon}
      </span>
      <span className={`text-xs font-bold ${green ? "text-emerald-400" : "text-foreground"}`}>
        {value}
      </span>
      <span className="font-mono text-[8px] tracking-widest text-foreground/30">
        {label.toUpperCase()}
      </span>
    </div>
  );
}

// ── EmptyWorkspace ────────────────────────────────────────────────────────────

function EmptyWorkspace({
  onStart,
}: {
  onStart: (text: string, ev: EvidenceItem[], rawFiles?: File[]) => void;
}) {
  return (
    <div className="mx-auto flex min-h-full max-w-3xl flex-col items-center justify-center px-4 py-16">
      <div className="mb-8 text-center">
        <VeritoWordmark size="md" />
        <div className="mt-6 text-lg text-foreground/80 md:text-xl">What are we investigating?</div>
        <div className="mt-1 text-sm text-muted-foreground">
          Describe the case, attach evidence, or paste a lead.
        </div>
      </div>
      <div className="w-full">
        <Composer onSubmit={onStart} autoFocus />
      </div>

      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {[
          "Missing persons case with 5 witness statements",
          "Analyse this chat log for inconsistencies",
          "Reconstruct the timeline from these photos",
        ].map((s) => (
          <button
            key={s}
            className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-foreground/70 transition hover:border-[color:var(--crimson)]/40 hover:bg-[color:var(--crimson)]/10 hover:text-foreground"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── ActiveCase ────────────────────────────────────────────────────────────────

function ActiveCase({
  caseRec,
  onSubmit,
}: {
  caseRec: CaseRecord;
  onSubmit: (t: string, ev: EvidenceItem[]) => void;
}) {
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-6 pb-32">
      {/* Case header */}
      <div className="glass rounded-2xl p-5">
        <div className="font-mono text-[10px] tracking-[0.3em] text-[color:var(--crimson-glow)]">
          CASE&nbsp;·&nbsp;{caseRec.id.replace("inv_", "").toUpperCase()}
        </div>
        <div className="mt-2 text-lg font-semibold">{caseRec.title || "Untitled case"}</div>
        {caseRec.prompt && <p className="mt-2 text-sm text-foreground/80">{caseRec.prompt}</p>}
        {caseRec.evidence.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {caseRec.evidence.map((e) => (
              <span
                key={e.id}
                className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-xs"
              >
                <span className="font-mono text-[10px] tracking-widest text-[color:var(--gold)]">
                  {e.id}
                </span>
                <span className="truncate max-w-[160px] text-foreground/80">{e.name}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Agent stream */}
      <AgentStream runs={caseRec.runs} />

      {/* Report */}
      {caseRec.report && <ReportView report={caseRec.report} investigationId={caseRec.id} />}

      {/* Chat */}
      {caseRec.chat.length > 0 && (
        <div className="space-y-3">
          {caseRec.chat.map((m) => (
            <div
              key={m.id}
              className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
            >
              <div
                className={
                  m.role === "user"
                    ? "max-w-[80%] rounded-2xl rounded-br-sm bg-[color:var(--crimson)] px-4 py-2.5 text-sm text-white"
                    : "max-w-[85%] rounded-2xl rounded-bl-sm glass px-4 py-3 text-sm text-foreground/90"
                }
              >
                {m.text || <span className="text-foreground/50">Thinking…</span>}
                {m.refs && m.refs.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {m.refs.map((ref) => (
                      <span
                        key={ref}
                        className="inline-flex items-center rounded-md border border-[color:var(--crimson)]/30 bg-[color:var(--crimson)]/10 px-1.5 py-0.5 font-mono text-[10px] tracking-widest text-[color:var(--crimson-glow)]"
                      >
                        {ref}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Follow-up composer */}
      {caseRec.status === "complete" && (
        <div className="sticky bottom-4 z-10">
          <Composer
            onSubmit={onSubmit}
            placeholder="Ask a follow-up question about this case…"
            compact
          />
        </div>
      )}
    </div>
  );
}
