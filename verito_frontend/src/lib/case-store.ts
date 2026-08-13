import type { CaseRecord } from "./investigation";

const KEY = "verito.cases.v1";

export function loadCases(): CaseRecord[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as CaseRecord[]) : [];
  } catch {
    return [];
  }
}

export function saveCases(cases: CaseRecord[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY, JSON.stringify(cases));
}

export function upsertCase(c: CaseRecord) {
  const list = loadCases();
  const i = list.findIndex((x) => x.id === c.id);
  if (i >= 0) list[i] = c;
  else list.unshift(c);
  saveCases(list);
}

export function deleteCase(id: string) {
  saveCases(loadCases().filter((c) => c.id !== id));
}

export function newCaseId() {
  return "case_" + Math.random().toString(36).slice(2, 10);
}
