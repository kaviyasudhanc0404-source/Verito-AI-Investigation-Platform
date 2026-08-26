/**
 * lib/sse.ts
 * ----------
 * Server-Sent Events client for the live agent investigation stream.
 * Connects to GET /api/investigations/{id}/stream and calls onEvent for each message.
 */

import { getToken } from "./auth-store";
import { API_BASE_URL } from "./api";
import type { AgentRun, CaseRecord, EvidenceItem, FinalReport } from "./investigation";

export type SseEvent =
  | { event: "agent_started"; agent_id: string }
  | { event: "agent_activity"; agent_id: string; line: { kind: string; text: string } }
  | { event: "agent_output"; agent_id: string; summary: string[] }
  | { event: "agent_completed"; agent_id: string }
  | { event: "agent_completed_with_warnings"; agent_id: string }
  | { event: "agent_blocked"; agent_id: string; reason: string }
  | { event: "agent_failed"; agent_id: string; error: string }
  | { event: "investigation_completed"; investigation_id: string }
  | { event: "stream_end" };

/**
 * Subscribe to the investigation SSE stream.
 * Uses native EventSource (browser) for automatic reconnect support.
 *
 * Returns a cleanup function to close the connection.
 */
export function subscribeToInvestigation(
  investigationId: string,
  onUpdate: (updater: (prev: CaseRecord) => CaseRecord) => void,
  onComplete: () => void,
): () => void {
  const token = getToken();
  const url = `${API_BASE_URL}/investigations/${investigationId}/stream`;

  // EventSource doesn't support custom headers, so we use fetch + ReadableStream
  let abortController = new AbortController();
  let closed = false;

  async function connectStream() {
    try {
      const response = await fetch(url, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
        },
        signal: abortController.signal,
      });

      if (!response.ok || !response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;

          try {
            const payload = JSON.parse(raw) as SseEvent;
            handleEvent(payload, onUpdate, onComplete);
          } catch {
            // Skip malformed event
          }
        }
      }
    } catch (err) {
      if (!closed) {
        console.error("SSE connection error:", err);
      }
    }
  }

  connectStream();

  return () => {
    closed = true;
    abortController.abort();
  };
}

function handleEvent(
  payload: SseEvent,
  onUpdate: (updater: (prev: CaseRecord) => CaseRecord) => void,
  onComplete: () => void,
): void {
  switch (payload.event) {
    case "agent_started":
      onUpdate((prev) => ({
        ...prev,
        runs: prev.runs.map((r) =>
          r.id === payload.agent_id ? { ...r, status: "running" as const } : r,
        ),
      }));
      break;

    case "agent_activity":
      onUpdate((prev) => ({
        ...prev,
        runs: prev.runs.map((r) =>
          r.id === payload.agent_id
            ? {
                ...r,
                lines: [...r.lines, payload.line as AgentRun["lines"][number]],
              }
            : r,
        ),
      }));
      break;

    case "agent_completed":
      onUpdate((prev) => ({
        ...prev,
        runs: prev.runs.map((r) =>
          r.id === payload.agent_id ? { ...r, status: "done" as const } : r,
        ),
      }));
      break;

    case "agent_completed_with_warnings":
      onUpdate((prev) => ({
        ...prev,
        runs: prev.runs.map((r) =>
          r.id === payload.agent_id ? { ...r, status: "completed_with_warnings" as const } : r,
        ),
      }));
      break;

    case "agent_blocked":
      onUpdate((prev) => ({
        ...prev,
        runs: prev.runs.map((r) =>
          r.id === payload.agent_id
            ? {
                ...r,
                status: "blocked" as const,
                lines: [...r.lines, { kind: "warn" as const, text: payload.reason }],
              }
            : r,
        ),
      }));
      break;

    case "agent_failed":
      onUpdate((prev) => ({
        ...prev,
        runs: prev.runs.map((r) =>
          r.id === payload.agent_id
            ? {
                ...r,
                status: "failed" as const,
                lines: [...r.lines, { kind: "error" as const, text: payload.error }],
              }
            : r,
        ),
      }));
      break;

    case "investigation_completed":
      onComplete();
      break;

    case "stream_end":
      onComplete();
      break;
  }
}
