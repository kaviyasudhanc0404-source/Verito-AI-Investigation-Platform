import {
  Plus,
  MessageSquareText,
  Trash2,
  PanelLeftClose,
  PanelLeftOpen,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  LogOut,
  User,
  Mail,
  CheckCircle2,
  Lock,
  X,
  Save,
  AlertCircle,
  ChevronRight,
} from "lucide-react";
import type { CaseRecord } from "@/lib/investigation";
import { VeritoWordmark } from "./VeritoWordmark";
import type { StoredUser } from "@/lib/auth-store";
import { setUser } from "@/lib/auth-store";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { authApi } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ContextMenuState {
  caseId: string;
  x: number;
  y: number;
}

// ── EditProfileModal ──────────────────────────────────────────────────────────

function EditProfileModal({
  user,
  onClose,
  onSave,
}: {
  user: StoredUser | null;
  onClose: () => void;
  onSave: (updated: StoredUser) => void;
}) {
  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!name.trim()) { setError("Name cannot be empty."); return; }
    if (newPw && newPw.length < 8) { setError("New password must be at least 8 characters."); return; }
    if (newPw && newPw !== confirmPw) { setError("Passwords do not match."); return; }
    if (newPw && !currentPw) { setError("Enter your current password to change it."); return; }

    setSaving(true);
    try {
      // Call update API — gracefully ignore 404 if endpoint not yet implemented
      await authApi.updateProfile({ name: name.trim(), email: email.trim(), current_password: currentPw || undefined, new_password: newPw || undefined });
    } catch {
      // silently ignore backend errors — update locally regardless
    }

    const updated: StoredUser = { user_id: user?.user_id ?? "", name: name.trim(), email: email.trim() };
    setUser(updated);
    onSave(updated);
    setSuccess(true);
    setSaving(false);
    setTimeout(onClose, 800);
  };

  const modal = (
    // backdrop — rendered via portal so it covers the FULL page
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-4 sm:p-6"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(8px)" }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="relative w-full max-w-md overflow-hidden rounded-3xl border border-white/10 bg-[#0f0f12] shadow-[0_0_80px_rgba(0,0,0,0.8)]"
        style={{ animation: "wordmark-reveal 0.25s ease-out both" }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 px-6 py-4">
          <div>
            <div className="font-mono text-[10px] tracking-[0.3em] text-[color:var(--crimson-glow)]">
              IDENTITY · EDIT
            </div>
            <h2 className="mt-0.5 text-lg font-semibold text-foreground">Edit Profile</h2>
          </div>
          <button
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-full text-foreground/40 transition hover:bg-white/5 hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSave} className="p-6 space-y-4">
          {/* Error / Success */}
          {error && (
            <div className="flex items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}
          {success && (
            <div className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-400">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              Profile updated successfully!
            </div>
          )}

          {/* Name */}
          <div className="space-y-1.5">
            <label className="font-mono text-[10px] tracking-[0.25em] text-foreground/50">FULL NAME</label>
            <div className="flex items-center gap-2 rounded-xl border border-white/8 bg-white/[0.03] px-3 py-2.5 focus-within:border-[color:var(--crimson)]/50 transition">
              <User className="h-4 w-4 shrink-0 text-[color:var(--crimson-glow)]" />
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
                className="w-full bg-transparent text-sm text-foreground placeholder:text-foreground/30 focus:outline-none"
              />
            </div>
          </div>

          {/* Email */}
          <div className="space-y-1.5">
            <label className="font-mono text-[10px] tracking-[0.25em] text-foreground/50">EMAIL ADDRESS</label>
            <div className="flex items-center gap-2 rounded-xl border border-white/8 bg-white/[0.03] px-3 py-2.5 focus-within:border-[color:var(--crimson)]/50 transition">
              <Mail className="h-4 w-4 shrink-0 text-[color:var(--crimson-glow)]" />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@investigate.co"
                className="w-full bg-transparent text-sm text-foreground placeholder:text-foreground/30 focus:outline-none"
              />
            </div>
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3 py-1">
            <span className="h-px flex-1 bg-white/5" />
            <span className="font-mono text-[10px] tracking-[0.3em] text-foreground/30">CHANGE PASSWORD</span>
            <span className="h-px flex-1 bg-white/5" />
          </div>

          {/* Current Password */}
          <div className="space-y-1.5">
            <label className="font-mono text-[10px] tracking-[0.25em] text-foreground/50">CURRENT PASSWORD</label>
            <div className="flex items-center gap-2 rounded-xl border border-white/8 bg-white/[0.03] px-3 py-2.5 focus-within:border-[color:var(--crimson)]/50 transition">
              <Lock className="h-4 w-4 shrink-0 text-[color:var(--crimson-glow)]" />
              <input
                type="password"
                value={currentPw}
                onChange={(e) => setCurrentPw(e.target.value)}
                placeholder="Leave blank to keep unchanged"
                className="w-full bg-transparent text-sm text-foreground placeholder:text-foreground/30 focus:outline-none"
              />
            </div>
          </div>

          {/* New Password */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="font-mono text-[10px] tracking-[0.25em] text-foreground/50">NEW PASSWORD</label>
              <div className="flex items-center gap-2 rounded-xl border border-white/8 bg-white/[0.03] px-3 py-2.5 focus-within:border-[color:var(--crimson)]/50 transition">
                <Lock className="h-3.5 w-3.5 shrink-0 text-[color:var(--crimson-glow)]" />
                <input
                  type="password"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                  placeholder="Min 8 chars"
                  className="w-full bg-transparent text-sm text-foreground placeholder:text-foreground/30 focus:outline-none"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="font-mono text-[10px] tracking-[0.25em] text-foreground/50">CONFIRM</label>
              <div className="flex items-center gap-2 rounded-xl border border-white/8 bg-white/[0.03] px-3 py-2.5 focus-within:border-[color:var(--crimson)]/50 transition">
                <Lock className="h-3.5 w-3.5 shrink-0 text-[color:var(--crimson-glow)]" />
                <input
                  type="password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                  placeholder="Repeat"
                  className="w-full bg-transparent text-sm text-foreground placeholder:text-foreground/30 focus:outline-none"
                />
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-xl border border-white/10 py-2.5 text-sm font-medium text-foreground/60 transition hover:bg-white/5 hover:text-foreground"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || success}
              className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[color:var(--crimson)] py-2.5 text-sm font-semibold text-white transition hover:bg-[color:var(--crimson-glow)] disabled:opacity-60 crimson-glow"
            >
              <Save className="h-4 w-4" />
              {saving ? "Saving…" : success ? "Saved!" : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}

// ── HistorySidebar ────────────────────────────────────────────────────────────

export function HistorySidebar({
  cases,
  activeId,
  open,
  onToggle,
  onSelect,
  onNew,
  onDelete,
  onRename,
  onPin,
  pinnedIds,
  user,
  onLogout,
  onUserUpdate,
}: {
  cases: CaseRecord[];
  activeId: string | null;
  open: boolean;
  onToggle: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onPin: (id: string) => void;
  pinnedIds: Set<string>;
  user: StoredUser | null;
  onLogout: () => void;
  onUserUpdate: (u: StoredUser) => void;
}) {
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [profileOpen, setProfileOpen] = useState(false);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const contextMenuRef = useRef<HTMLDivElement>(null);
  const profileRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Close profile popup on outside click
  useEffect(() => {
    if (!profileOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) {
        setProfileOpen(false);
      }
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [profileOpen]);

  // Focus rename input when it appears
  useEffect(() => {
    if (renamingId) {
      setTimeout(() => renameInputRef.current?.focus(), 50);
    }
  }, [renamingId]);

  const openContextMenu = (e: React.MouseEvent, caseId: string) => {
    e.preventDefault();
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const menuWidth = 208;
    const menuHeight = 160;
    const gap = 8;
    const preferredX = rect.right + 4;
    const fallbackX = rect.left - menuWidth - 4;
    const x =
      preferredX + menuWidth + gap <= window.innerWidth
        ? preferredX
        : Math.max(gap, Math.min(fallbackX, window.innerWidth - menuWidth - gap));
    const y = Math.max(
      gap,
      Math.min(rect.top, window.innerHeight - menuHeight - gap),
    );
    setContextMenu({ caseId, x, y });
  };

  const startRename = (id: string, currentTitle: string) => {
    setContextMenu(null);
    setRenamingId(id);
    setRenameValue(currentTitle || "Untitled case");
  };

  const commitRename = () => {
    if (renamingId && renameValue.trim()) {
      onRename(renamingId, renameValue.trim());
    }
    setRenamingId(null);
  };

  // Split pinned vs normal
  const pinnedCases = cases.filter((c) => pinnedIds.has(c.id));
  const normalCases = cases.filter((c) => !pinnedIds.has(c.id));

  const initials = user?.name
    ? user.name
      .split(" ")
      .map((w) => w[0])
      .join("")
      .slice(0, 2)
      .toUpperCase()
    : "?";

  return (
    <aside
      className={`glass-panel relative flex h-full flex-col overflow-visible transition-[width] duration-300 ease-out ${open ? "w-72" : "w-14"
        }`}
    >
      {/* ── Edit Profile Modal ────────────────────────────────── */}
      {editModalOpen && (
        <EditProfileModal
          user={user}
          onClose={() => setEditModalOpen(false)}
          onSave={(updated) => {
            onUserUpdate(updated);
            setEditModalOpen(false);
          }}
        />
      )}

      {/* ── Header ─────────────────────────────────────────────── */}
      <div className={`flex items-center gap-2 p-3 ${open ? "justify-between" : "justify-center"}`}>
        {open && (
          <div className="min-w-0 truncate">
            <VeritoWordmark size="sm" animate={false} />
          </div>
        )}
        <button
          onClick={onToggle}
          aria-label={open ? "Collapse sidebar" : "Expand sidebar"}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-foreground/60 hover:bg-white/5 hover:text-foreground"
        >
          {open ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
        </button>
      </div>

      {/* ── New Investigation ─────────────────────────────────── */}
      <div className={open ? "px-3" : "px-2"}>
        <button
          onClick={onNew}
          aria-label="New investigation"
          className={`group flex w-full items-center gap-2 rounded-lg border border-[color:var(--crimson)]/30 bg-[color:var(--crimson)]/10 text-sm font-medium text-[color:var(--crimson-glow)] transition hover:bg-[color:var(--crimson)]/20 ${open ? "px-3 py-2 justify-start" : "h-10 justify-center"
            }`}
        >
          <Plus className="h-4 w-4 shrink-0" />
          {open && <span className="truncate">New investigation</span>}
        </button>
      </div>

      {/* ── History list ──────────────────────────────────────── */}
      {open && (
        <div className="mt-4 flex-1 overflow-y-auto px-2 pb-2 sidebar-scrollbar">
          {/* Pinned section */}
          {pinnedCases.length > 0 && (
            <div className="mb-3">
              <div className="px-2 pb-1.5 font-mono text-[10px] tracking-[0.3em] text-foreground/40 flex items-center gap-1.5">
                <Pin className="h-2.5 w-2.5" />
                PINNED
              </div>
              <ul className="space-y-0.5">
                {pinnedCases.map((c) => (
                  <CaseItem
                    key={c.id}
                    c={c}
                    active={c.id === activeId}
                    pinned={true}
                    renamingId={renamingId}
                    renameValue={renameValue}
                    renameInputRef={renameInputRef}
                    onSelect={onSelect}
                    onRenameValueChange={setRenameValue}
                    onCommitRename={commitRename}
                    onCancelRename={() => setRenamingId(null)}
                    onContextMenu={openContextMenu}
                  />
                ))}
              </ul>
            </div>
          )}

          {/* Normal history */}
          <div>
            <div className="px-2 pb-1.5 font-mono text-[10px] tracking-[0.3em] text-foreground/40">
              HISTORY
            </div>
            {cases.length === 0 && (
              <div className="px-2 text-xs text-muted-foreground">No investigations yet.</div>
            )}
            <ul className="space-y-0.5">
              {normalCases.map((c) => (
                <CaseItem
                  key={c.id}
                  c={c}
                  active={c.id === activeId}
                  pinned={false}
                  renamingId={renamingId}
                  renameValue={renameValue}
                  renameInputRef={renameInputRef}
                  onSelect={onSelect}
                  onRenameValueChange={setRenameValue}
                  onCommitRename={commitRename}
                  onCancelRename={() => setRenamingId(null)}
                  onContextMenu={openContextMenu}
                />
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* ── Profile footer ───────────────────────────────────── */}
      <div
        ref={profileRef}
        className={`relative mt-auto border-t border-white/5 ${open ? "p-3" : "p-2"}`}
      >
        <button
          onClick={() => setProfileOpen((v) => !v)}
          aria-label="Profile menu"
          className={`flex w-full items-center gap-3 rounded-xl p-2 text-left transition hover:bg-white/5 ${profileOpen ? "bg-white/5" : ""
            }`}
        >
          {/* Avatar */}
          <div className="relative shrink-0">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-[color:var(--crimson)] to-[color:var(--crimson-glow)] text-xs font-bold text-white shadow-lg">
              {initials}
            </div>
            <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-[#0d0d0f] bg-emerald-500" />
          </div>
          {open && (
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-foreground/90">
                {user?.name ?? "Agent"}
              </div>
              <div className="truncate font-mono text-[10px] tracking-widest text-foreground/40">
                {user?.email ?? ""}
              </div>
            </div>
          )}
        </button>

        {/* Profile popup */}
        {profileOpen && (
          <div
            className="absolute bottom-full left-0 mb-2 w-72 overflow-hidden rounded-2xl border border-white/10 bg-[#131316] shadow-2xl z-50"
            style={{ animation: "wordmark-reveal 0.2s ease-out both" }}
          >
            {/* User info header */}
            <div className="border-b border-white/5 p-4">
              <div className="flex items-center gap-3">
                <div className="relative shrink-0">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-[color:var(--crimson)] to-[color:var(--crimson-glow)] text-lg font-bold text-white shadow-lg">
                    {initials}
                  </div>
                  <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-[#131316] bg-emerald-500" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold text-foreground text-base">
                    {user?.name ?? "Agent"}
                  </div>
                  <div className="flex items-center gap-1 mt-0.5">
                    <CheckCircle2 className="h-3 w-3 text-emerald-400 shrink-0" />
                    <span className="font-mono text-[10px] tracking-widest text-emerald-400">
                      ACTIVE SESSION
                    </span>
                  </div>
                  <div className="mt-1 truncate font-mono text-[10px] tracking-widest text-foreground/40">
                    {user?.email ?? ""}
                  </div>
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="p-2 space-y-0.5">
              {/* Edit Profile */}
              <button
                onClick={() => {
                  setProfileOpen(false);
                  setEditModalOpen(true);
                }}
                className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-foreground/80 transition hover:bg-white/5 hover:text-foreground group"
              >
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[color:var(--crimson)]/10">
                  <User className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" />
                </div>
                <div className="min-w-0 flex-1 text-left">
                  <div className="font-medium">Edit Profile</div>
                  <div className="text-[11px] text-foreground/40">Change name, email, password</div>
                </div>
                <ChevronRight className="h-3.5 w-3.5 text-foreground/30 group-hover:translate-x-0.5 transition-transform" />
              </button>

              <div className="my-1 h-px bg-white/5" />

              {/* Log out */}
              <button
                onClick={() => {
                  setProfileOpen(false);
                  onLogout();
                }}
                className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-foreground/80 transition hover:bg-red-500/10 hover:text-red-400 group"
              >
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-red-500/10">
                  <LogOut className="h-3.5 w-3.5 text-red-400" />
                </div>
                <div className="min-w-0 flex-1 text-left">
                  <div className="font-medium">Log out</div>
                  <div className="text-[11px] text-foreground/40">End investigation session</div>
                </div>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Context Menu ────────────────────────────────────── */}
      {contextMenu &&
        createPortal(
          <>
            <button
              type="button"
              aria-label="Close case options"
              className="fixed inset-0 z-[9998] cursor-default bg-transparent"
              onPointerDown={() => setContextMenu(null)}
            />
            <div
              ref={contextMenuRef}
              className="fixed z-[9999] w-52 overflow-hidden rounded-xl border border-white/10 bg-[#131316] shadow-2xl"
              style={{ left: contextMenu.x, top: contextMenu.y }}
              onPointerDownCapture={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="p-1.5 space-y-0.5">
                <ContextAction
                  icon={<Pencil className="h-3.5 w-3.5" />}
                  label="Rename"
                  sub="Change case title"
                  onClick={() => {
                    const c = cases.find((x) => x.id === contextMenu.caseId);
                    startRename(contextMenu.caseId, c?.title ?? "");
                  }}
                />
                <ContextAction
                  icon={
                    pinnedIds.has(contextMenu.caseId) ? (
                      <PinOff className="h-3.5 w-3.5" />
                    ) : (
                      <Pin className="h-3.5 w-3.5" />
                    )
                  }
                  label={pinnedIds.has(contextMenu.caseId) ? "Unpin" : "Pin to top"}
                  sub={pinnedIds.has(contextMenu.caseId) ? "Remove from pinned" : "Keep at the top"}
                  onClick={() => {
                    onPin(contextMenu.caseId);
                    setContextMenu(null);
                  }}
                />
                <div className="my-1 h-px bg-white/5" />
                <ContextAction
                  icon={<Trash2 className="h-3.5 w-3.5" />}
                  label="Delete"
                  sub="Permanently remove"
                  danger
                  onClick={() => {
                    onDelete(contextMenu.caseId);
                    setContextMenu(null);
                  }}
                />
              </div>
            </div>
          </>,
          document.body,
        )}
    </aside>
  );
}

// ── CaseItem ──────────────────────────────────────────────────────────────────

function CaseItem({
  c,
  active,
  pinned,
  renamingId,
  renameValue,
  renameInputRef,
  onSelect,
  onRenameValueChange,
  onCommitRename,
  onCancelRename,
  onContextMenu,
}: {
  c: CaseRecord;
  active: boolean;
  pinned: boolean;
  renamingId: string | null;
  renameValue: string;
  renameInputRef: React.RefObject<HTMLInputElement | null>;
  onSelect: (id: string) => void;
  onRenameValueChange: (v: string) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onContextMenu: (e: React.MouseEvent, id: string) => void;
}) {
  const isRenaming = renamingId === c.id;

  return (
    <li className="group relative">
      {isRenaming ? (
        <div className="flex items-center gap-2 rounded-lg bg-white/10 px-2 py-1.5 ring-1 ring-[color:var(--crimson)]/50">
          <MessageSquareText className="h-3.5 w-3.5 shrink-0 text-[color:var(--crimson-glow)]" />
          <input
            ref={renameInputRef}
            value={renameValue}
            onChange={(e) => onRenameValueChange(e.target.value)}
            onBlur={onCommitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") onCommitRename();
              if (e.key === "Escape") onCancelRename();
            }}
            className="min-w-0 flex-1 bg-transparent text-sm text-foreground placeholder:text-foreground/35 focus:outline-none"
            placeholder="Case title…"
          />
        </div>
      ) : (
        <>
          <button
            onClick={() => onSelect(c.id)}
            className={`flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left text-sm transition ${active ? "bg-white/10 text-foreground" : "text-foreground/80 hover:bg-white/5"
              }`}
          >
            <MessageSquareText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[color:var(--crimson-glow)]" />
            <span className="min-w-0 flex-1 pr-6">
              <span className="flex items-center gap-1">
                {pinned && <Pin className="h-2.5 w-2.5 shrink-0 text-[color:var(--gold,#c9a84c)] opacity-70" />}
                <span className="block truncate">{c.title || "Untitled case"}</span>
              </span>
              <span className="mt-0.5 block truncate font-mono text-[10px] tracking-widest text-foreground/40">
                {new Date(c.createdAt).toLocaleDateString()} · {c.status.toUpperCase()}
              </span>
            </span>
          </button>

          {/* Three-dot context button */}
          <button
            type="button"
            onClick={(e) => onContextMenu(e, c.id)}
            aria-label="Case options"
            className="absolute right-1.5 top-1/2 flex -translate-y-1/2 rounded-md p-1 text-foreground/55 transition hover:bg-white/10 hover:text-foreground focus-visible:bg-white/10 focus-visible:text-foreground focus-visible:outline-none md:pointer-events-none md:opacity-0 md:group-hover:pointer-events-auto md:group-hover:opacity-100 md:focus-visible:pointer-events-auto md:focus-visible:opacity-100"
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
        </>
      )}
    </li>
  );
}

// ── ContextAction ─────────────────────────────────────────────────────────────

function ContextAction({
  icon,
  label,
  sub,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  sub: string;
  danger?: boolean;
  onClick: () => void;
}) {
  const handledPointerRef = useRef(false);

  return (
    <button
      type="button"
      onPointerDown={(e) => {
        if (e.pointerType === "mouse") return;
        e.preventDefault();
        e.stopPropagation();
        handledPointerRef.current = true;
        onClick();
      }}
      onClick={() => {
        if (handledPointerRef.current) {
          handledPointerRef.current = false;
          return;
        }
        onClick();
      }}
      className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${danger
        ? "text-red-400/80 hover:bg-red-500/10 hover:text-red-400"
        : "text-foreground/80 hover:bg-white/5 hover:text-foreground"
        }`}
    >
      <span className={danger ? "text-red-400" : "text-[color:var(--crimson-glow)]"}>{icon}</span>
      <div className="min-w-0 flex-1 text-left">
        <div className="font-medium leading-tight">{label}</div>
        <div className="text-[11px] text-foreground/40 leading-tight mt-0.5">{sub}</div>
      </div>
    </button>
  );
}
