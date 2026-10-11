/**
 * lib/sse.ts
 * ----------
 * Live agent stream: GET /api/investigations/{id}/stream (Server-Sent Events over fetch,
 * because EventSource cannot send the Authorization header).
 *
 * If the connection drops before the run finishes, it reconnects with backoff. The server
 * replays the run's events on reconnect, so `agent_started` resets that agent's lines.
 */

import { getToken } from "./auth-store";
import { API_BASE_URL } from "./api";
import type { AgentRun, CaseRecord } from "./investigation";

type SseEvent =
  | {
      event:
        | "agent_started"
        | "agent_completed"
        | "agent_completed_with_warnings"
        | "agent_completed_with_fallback";
      agent_id: string;
    }
  | { event: "agent_activity"; agent_id: string; line: AgentRun["lines"][number] }
  | { event: "agent_blocked"; agent_id: string; reason: string }
  | { event: "agent_failed"; agent_id: string; error: string }
  | { event: "investigation_completed"; investigation_id: string }
  | { event: "stream_end" };

type CaseUpdater = (updater: (prev: CaseRecord) => CaseRecord) => void;

const MAX_RECONNECTS = 5;

export function subscribeToInvestigation(
  investigationId: string,
  onUpdate: CaseUpdater,
  onComplete: () => void,
): () => void {
  const controller = new AbortController();
  let finished = false;

  const complete = () => {
    if (!finished) {
      finished = true;
      onComplete();
    }
  };

  async function connect(attempt: number): Promise<void> {
    try {
      const response = await fetch(`${API_BASE_URL}/investigations/${investigationId}/stream`, {
        headers: { Authorization: `Bearer ${getToken() ?? ""}`, Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`Stream failed (${response.status})`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!finished) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue; // skips ": keep-alive" comments
          try {
            handleEvent(JSON.parse(line.slice(6)) as SseEvent, onUpdate, complete);
          } catch {
            // ignore a malformed event
          }
        }
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      console.error("SSE connection error:", err);
    }
    if (!finished && !controller.signal.aborted && attempt < MAX_RECONNECTS) {
      await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** attempt));
      return connect(attempt + 1);
    }
    if (!controller.signal.aborted) complete();
  }

  void connect(0);
  return () => controller.abort();
}

const STATUS_BY_EVENT = {
  agent_started: "running",
  agent_completed: "done",
  agent_completed_with_warnings: "completed_with_warnings",
  agent_completed_with_fallback: "completed_with_fallback",
  agent_blocked: "blocked",
  agent_failed: "failed",
} as const satisfies Record<string, AgentRun["status"]>;

function handleEvent(payload: SseEvent, onUpdate: CaseUpdater, onComplete: () => void): void {
  if (payload.event === "investigation_completed" || payload.event === "stream_end") {
    onComplete();
    return;
  }
  const agentId = payload.agent_id;
  onUpdate((prev) => ({
    ...prev,
    runs: prev.runs.map((run) => {
      if (run.id !== agentId) return run;
      switch (payload.event) {
        case "agent_activity":
          return { ...run, lines: [...run.lines, payload.line] };
        case "agent_started":
          return { ...run, status: "running", lines: [] };
        case "agent_blocked":
          return {
            ...run,
            status: "blocked",
            lines: [...run.lines, { kind: "warn", text: payload.reason }],
          };
        case "agent_failed":
          return {
            ...run,
            status: "failed",
            lines: [...run.lines, { kind: "error", text: payload.error }],
          };
        default:
          return { ...run, status: STATUS_BY_EVENT[payload.event] };
      }
    }),
  }));
}
