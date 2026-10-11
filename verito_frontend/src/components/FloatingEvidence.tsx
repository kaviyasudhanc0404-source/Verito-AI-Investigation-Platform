import { motion } from "framer-motion";

const items = [
  { x: "6%", y: "22%", label: "E-001", kind: "DOC", title: "Statement.pdf", delay: 0.1 },
  { x: "18%", y: "68%", label: "E-002", kind: "MSG", title: "Chat log · Mar 14", delay: 0.2 },
  { x: "78%", y: "18%", label: "E-004", kind: "IMG", title: "Pier 9 · 21:42", delay: 0.15 },
  { x: "86%", y: "62%", label: "E-006", kind: "AUD", title: "Voice memo 00:47", delay: 0.25 },
  { x: "70%", y: "80%", label: "E-007", kind: "GPS", title: "40.7128°N, -74.0060°W", delay: 0.3 },
  { x: "10%", y: "45%", label: "E-003", kind: "PRT", title: "Fingerprint fragment", delay: 0.35 },
];

export function FloatingEvidence() {
  return (
    <div className="pointer-events-none absolute inset-0 hidden md:block" aria-hidden>
      {items.map((it, i) => (
        <motion.div
          key={i}
          initial={{ opacity: 0, y: 20, scale: 0.9 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ delay: 1 + it.delay, duration: 0.7, ease: [0.2, 0.7, 0.2, 1] }}
          className="absolute animate-float-y"
          style={{ left: it.x, top: it.y, animationDelay: `${i * 0.4}s` }}
        >
          <div className="glass w-[190px] rounded-xl p-3">
            <div className="flex items-center justify-between font-mono text-[10px] tracking-widest text-[color:var(--crimson-glow)]">
              <span>{it.label}</span>
              <span className="text-[color:var(--gold)]">{it.kind}</span>
            </div>
            <div className="mt-2 h-14 rounded-md bg-gradient-to-br from-white/5 to-white/0 relative overflow-hidden">
              <div className="absolute inset-x-0 top-0 h-px animate-scan-y bg-gradient-to-r from-transparent via-[color:var(--crimson-glow)] to-transparent" />
              <div className="absolute inset-2 grid grid-cols-6 gap-[3px] opacity-40">
                {Array.from({ length: 18 }).map((_, k) => (
                  <div key={k} className="h-1 rounded-sm bg-white/30" />
                ))}
              </div>
            </div>
            <div className="mt-2 truncate text-xs text-foreground/80">{it.title}</div>
          </div>
        </motion.div>
      ))}

      {/* Connecting thread SVG */}
      <svg
        className="absolute inset-0 h-full w-full"
        preserveAspectRatio="none"
        viewBox="0 0 100 100"
      >
        <defs>
          <linearGradient id="thread" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="oklch(0.66 0.24 22)" stopOpacity="0" />
            <stop offset="50%" stopColor="oklch(0.66 0.24 22)" stopOpacity="0.7" />
            <stop offset="100%" stopColor="oklch(0.9 0.14 82)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <motion.path
          d="M 12,28 C 30,40 40,70 24,72 S 60,60 78,24 C 90,50 78,80 74,82 S 20,55 14,48"
          fill="none"
          stroke="url(#thread)"
          strokeWidth="0.25"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ delay: 1.4, duration: 2.2, ease: "easeInOut" }}
          style={{ filter: "drop-shadow(0 0 3px oklch(0.66 0.24 22 / 0.5))" }}
        />
      </svg>
    </div>
  );
}
