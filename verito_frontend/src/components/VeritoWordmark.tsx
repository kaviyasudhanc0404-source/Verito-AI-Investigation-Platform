import { motion } from "framer-motion";

export function VeritoWordmark({
  size = "md",
  animate = true,
  continuousScan = false,
}: {
  size?: "sm" | "md" | "lg" | "xl";
  animate?: boolean;
  continuousScan?: boolean;
}) {
  const sizes = {
    sm: "text-xl",
    md: "text-3xl md:text-4xl",
    lg: "text-4xl md:text-5xl",
    xl: "text-[11vw] md:text-[7.5vw] leading-none",
  } as const;

  const lensSize = "h-[0.9em] w-[0.9em]";

  const containerAnim = animate
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: { duration: 0.4 } }
    : {};

  const scanClass = animate ? "animate-scan-y-once" : "";

  return (
    <motion.div
      {...containerAnim}
      className={`relative inline-flex items-baseline font-brand font-bold tracking-[0.08em] ${sizes[size]}`}
    >
      <span className={animate ? "animate-wordmark" : ""}>V</span>
      <span className={animate ? "animate-wordmark" : ""} style={{ animationDelay: "60ms" }}>
        E
      </span>
      <span className={animate ? "animate-wordmark" : ""} style={{ animationDelay: "120ms" }}>
        R
      </span>
      <span className={animate ? "animate-wordmark" : ""} style={{ animationDelay: "180ms" }}>
        I
      </span>
      <span className={animate ? "animate-wordmark" : ""} style={{ animationDelay: "240ms" }}>
        T
      </span>

      {/* The "O" as a holographic magnifying lens */}
      <span
        className={`relative inline-grid ${lensSize} place-items-center align-baseline`}
        aria-label="O"
        style={{ transform: "translateY(0.05em)" }}
      >
        <motion.svg
          viewBox="0 0 100 100"
          className="absolute inset-0 h-full w-full"
          initial={animate ? { scale: 0.4, opacity: 0, rotate: -30 } : false}
          animate={{ scale: 1, opacity: 1, rotate: 0 }}
          transition={{ delay: 0.35, duration: 0.7, type: "spring", stiffness: 120 }}
        >
          <defs>
            <radialGradient id="lens-grad" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="oklch(0.98 0.03 22)" stopOpacity="0.15" />
              <stop offset="70%" stopColor="oklch(0.66 0.24 22)" stopOpacity="0.35" />
              <stop offset="100%" stopColor="oklch(0.4 0.2 22)" stopOpacity="0" />
            </radialGradient>
            <linearGradient id="lens-ring" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="oklch(0.9 0.14 82)" />
              <stop offset="60%" stopColor="oklch(0.66 0.24 22)" />
              <stop offset="100%" stopColor="oklch(0.9 0.14 82)" />
            </linearGradient>
          </defs>
          <circle cx="50" cy="50" r="42" fill="url(#lens-grad)" />
          <circle cx="50" cy="50" r="42" fill="none" stroke="url(#lens-ring)" strokeWidth="4" />
          <circle
            cx="50"
            cy="50"
            r="34"
            fill="none"
            stroke="oklch(0.66 0.24 22 / 0.35)"
            strokeWidth="1"
          />
          {/* Fingerprint ridges */}
          <g
            stroke="oklch(0.9 0.14 82 / 0.7)"
            strokeWidth="1"
            fill="none"
            style={{ animation: "fingerprint-scan 2.2s ease-in-out 0.7s both" }}
          >
            <path d="M30 55 Q50 30 70 55" />
            <path d="M34 60 Q50 40 66 60" />
            <path d="M38 64 Q50 48 62 64" />
            <path d="M42 68 Q50 56 58 68" />
            <path d="M46 71 Q50 64 54 71" />
          </g>
          <ellipse cx="38" cy="36" rx="10" ry="6" fill="white" opacity="0.15" />
        </motion.svg>
        {animate && (
          <span
            className="pointer-events-none absolute inset-[10%] overflow-hidden rounded-full"
            aria-hidden
          >
            <span
              className={`absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r from-transparent via-[color:var(--crimson-glow)] to-transparent ${scanClass}`}
            />
          </span>
        )}
      </span>

      {/* Crimson laser sweep — one-shot on mount, or continuous when requested */}
      {(animate || continuousScan) && (
        <span className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <span
            className={`absolute inset-y-0 w-[30%] ${continuousScan ? "animate-laser" : "animate-laser-once"}`}
            style={{
              background:
                "linear-gradient(90deg, transparent, oklch(0.66 0.24 22 / 0.7), transparent)",
              filter: "blur(4px)",
            }}
          />
        </span>
      )}
    </motion.div>
  );
}
