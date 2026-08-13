import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Fingerprint, LogIn, Workflow } from "lucide-react";
import { VeritoWordmark } from "./VeritoWordmark";

export function Header() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <header className="fixed inset-x-0 top-0 z-50">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
        <Link to="/" className="group inline-flex items-center gap-2">
          <VeritoWordmark size="sm" animate={false} continuousScan />
        </Link>

        <div ref={ref} className="relative">
          <button
            type="button"
            aria-label="Open menu"
            onClick={() => setOpen((v) => !v)}
            className="glass group grid h-10 w-10 place-items-center rounded-full text-[color:var(--crimson-glow)] transition hover:text-foreground"
          >
            <Fingerprint className="h-4 w-4 transition-transform group-hover:scale-110" />
          </button>

          {open && (
            <div
              className="glass absolute right-0 mt-3 w-56 origin-top-right overflow-hidden rounded-xl p-1.5 shadow-2xl"
              style={{ animation: "wordmark-reveal 0.2s ease-out both" }}
            >
              <div className="px-3 pb-2 pt-1.5 font-mono text-[10px] tracking-[0.3em] text-foreground/40">
                ACCESS&nbsp;POINT
              </div>
              <Link
                to="/auth"
                onClick={() => setOpen(false)}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-foreground/90 transition hover:bg-[color:var(--crimson)]/15 hover:text-foreground"
              >
                <LogIn className="h-4 w-4 text-[color:var(--crimson-glow)]" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">Connect</div>
                  <div className="text-[11px] text-foreground/50">Login or sign up</div>
                </div>
              </Link>
              <a
                href="/#works"
                onClick={() => setOpen(false)}
                className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-foreground/90 transition hover:bg-[color:var(--crimson)]/15 hover:text-foreground"
              >
                <Workflow className="h-4 w-4 text-[color:var(--gold)]" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">Works</div>
                  <div className="text-[11px] text-foreground/50">How VERITO investigates</div>
                </div>
              </a>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
