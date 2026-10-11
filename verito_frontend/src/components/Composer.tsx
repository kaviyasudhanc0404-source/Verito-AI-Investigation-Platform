import { useRef, useState } from "react";
import {
  Paperclip,
  Mic,
  ArrowUp,
  X,
  FileText,
  Image as ImageIcon,
  Music,
  MapPin,
  MessageSquare,
  Table,
  Video,
} from "lucide-react";
import type { EvidenceItem } from "@/lib/investigation";

/** Same categories as the backend (utils/file_detector.py); the server's value replaces this after upload. */
function kindOf(name: string): EvidenceItem["kind"] {
  const n = name.toLowerCase();
  if (/\.(png|jpe?g|webp|gif|heic|bmp|tiff?)$/.test(n)) return "image";
  if (/\.(mp3|wav|m4a|ogg|flac)$/.test(n)) return "audio";
  if (/\.(mp4|mov|avi|mkv)$/.test(n)) return "video";
  if (/\.(eml|msg)$/.test(n)) return "chat";
  if (/\.(txt|csv|xlsx?|json|xml)$/.test(n)) return "text";
  return "doc";
}

const ICONS: Record<EvidenceItem["kind"], typeof FileText> = {
  image: ImageIcon,
  audio: Music,
  video: Video,
  doc: FileText,
  text: Table,
  chat: MessageSquare,
  map: MapPin,
};
const iconFor = (k: EvidenceItem["kind"]) => ICONS[k];

export function Composer({
  onSubmit,
  placeholder = "Describe the case, ask a question, or attach evidence…",
  autoFocus,
  compact,
}: {
  onSubmit: (text: string, evidence: EvidenceItem[], rawFiles?: File[]) => void;
  placeholder?: string;
  autoFocus?: boolean;
  compact?: boolean;
}) {
  const [text, setText] = useState("");
  const [items, setItems] = useState<EvidenceItem[]>([]);
  const [drag, setDrag] = useState(false);
  const [listening, setListening] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  // Store raw File objects so app.tsx can upload them to the backend
  const rawFilesRef = useRef<File[]>([]);

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const fileArray = Array.from(files);
    const next: EvidenceItem[] = fileArray.map((f, i) => ({
      id: `E-${String(items.length + i + 1).padStart(3, "0")}`,
      name: f.name,
      kind: kindOf(f.name),
      size: f.size,
    }));
    rawFilesRef.current = [...rawFilesRef.current, ...fileArray];
    setItems((s) => [...s, ...next]);
  };

  const submit = () => {
    if (!text.trim() && items.length === 0) return;
    onSubmit(text.trim(), items, rawFilesRef.current);
    setText("");
    setItems([]);
    rawFilesRef.current = [];
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        addFiles(e.dataTransfer.files);
      }}
      className={`glass relative rounded-2xl transition ${drag ? "crimson-glow" : ""} ${compact ? "p-3" : "p-4"}`}
    >
      {items.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {items.map((it) => {
            const Icon = iconFor(it.kind);
            return (
              <div
                key={it.id}
                className="group inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs"
              >
                <Icon className="h-3.5 w-3.5 text-[color:var(--gold)]" />
                <span className="font-mono text-[10px] tracking-widest text-[color:var(--crimson-glow)]">
                  {it.id}
                </span>
                <span className="max-w-[160px] truncate text-foreground/90">{it.name}</span>
                <button
                  onClick={() => setItems((s) => s.filter((x) => x.id !== it.id))}
                  className="opacity-60 transition hover:opacity-100"
                  aria-label="Remove"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <textarea
        autoFocus={autoFocus}
        rows={compact ? 1 : 3}
        placeholder={placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
        }}
        className="min-h-[44px] w-full resize-none bg-transparent px-1 text-[15px] placeholder:text-foreground/40 focus:outline-none"
      />

      <div className="mt-2 flex items-center justify-between">
        <div className="flex items-center gap-1">
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => addFiles(e.target.files)}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="grid h-9 w-9 place-items-center rounded-full text-foreground/70 transition hover:bg-white/5 hover:text-foreground"
            aria-label="Attach evidence"
          >
            <Paperclip className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setListening((v) => !v)}
            className={`grid h-9 w-9 place-items-center rounded-full transition ${
              listening
                ? "bg-[color:var(--crimson)]/20 text-[color:var(--crimson-glow)] animate-pulse-node"
                : "text-foreground/70 hover:bg-white/5 hover:text-foreground"
            }`}
            aria-label="Voice input"
          >
            <Mic className="h-4 w-4" />
          </button>
          {listening && (
            <div className="ml-2 flex items-end gap-[3px]">
              {[6, 12, 8, 16, 10, 14, 7].map((h, i) => (
                <span
                  key={i}
                  className="w-[3px] rounded-full bg-[color:var(--crimson-glow)]"
                  style={{
                    height: h,
                    animation: `float-y 0.9s ease-in-out ${i * 80}ms infinite`,
                  }}
                />
              ))}
            </div>
          )}
        </div>

        <button
          onClick={submit}
          disabled={!text.trim() && items.length === 0}
          className="group relative inline-flex items-center gap-2 rounded-full bg-[color:var(--crimson)] px-4 py-2 text-xs font-semibold tracking-[0.2em] text-white transition hover:bg-[color:var(--crimson-glow)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          <span>INVESTIGATE</span>
          <ArrowUp className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Focus laser */}
      <div className="pointer-events-none absolute inset-x-3 -bottom-px h-px bg-gradient-to-r from-transparent via-[color:var(--crimson)]/60 to-transparent" />
    </div>
  );
}
