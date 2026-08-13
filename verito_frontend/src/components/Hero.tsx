import { Link } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { ArrowRight, ScanLine } from "lucide-react";
import { VeritoWordmark } from "./VeritoWordmark";
import { FloatingEvidence } from "./FloatingEvidence";

export function Hero() {
  return (
    <section className="relative flex min-h-[100svh] items-center justify-center overflow-hidden px-6 pt-24">
      {/* Ambient layers */}
      <div className="absolute inset-0 grid-bg" aria-hidden />
      <div
        className="absolute inset-0 opacity-70"
        aria-hidden
        style={{
          background:
            "radial-gradient(60% 40% at 50% 55%, oklch(0.28 0.14 22 / 0.35), transparent 70%)",
        }}
      />
      {/* Volumetric light beam */}
      <div
        className="pointer-events-none absolute -top-40 left-1/2 h-[80vh] w-[70vw] -translate-x-1/2 opacity-40 blur-2xl"
        aria-hidden
        style={{
          background:
            "conic-gradient(from 210deg at 50% 0%, transparent 0deg, oklch(0.66 0.24 22 / 0.4) 20deg, transparent 40deg)",
        }}
      />
      <FloatingEvidence />

      <div className="relative z-10 mx-auto flex max-w-6xl flex-col items-center text-center">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="glass mb-8 inline-flex items-center gap-2 rounded-full px-3 py-1.5 font-mono text-[11px] tracking-[0.25em] text-foreground/70"
        >
          <ScanLine className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" />
          AI&nbsp;INVESTIGATION&nbsp;·&nbsp;LIVE
        </motion.div>

        <VeritoWordmark size="xl" />

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 1.1, duration: 0.7 }}
          className="mt-10 space-y-1 font-display text-2xl font-medium tracking-tight md:text-3xl"
        >
          <div>Connect clues.</div>
          <div>Reconstruct events.</div>
          <div className="gold-text">Reveal the truth.</div>
        </motion.div>

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.4 }}
          className="mt-6 max-w-xl text-sm text-muted-foreground md:text-base"
        >
          Transform scattered evidence into a connected investigation.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 1.6 }}
          className="mt-10"
        >
          <Link
            to="/app"
            className="group relative inline-flex items-center gap-3 rounded-full bg-[color:var(--crimson)] px-7 py-4 text-sm font-semibold tracking-[0.2em] text-white transition hover:bg-[color:var(--crimson-glow)] crimson-glow"
          >
            <span className="absolute inset-0 overflow-hidden rounded-full">
              <span className="absolute inset-y-0 w-1/3 animate-laser bg-gradient-to-r from-transparent via-white/40 to-transparent" />
            </span>
            <span className="relative">START INVESTIGATION</span>
            <ArrowRight className="relative h-4 w-4 transition group-hover:translate-x-0.5" />
          </Link>
          <div className="mt-4 font-mono text-[10px] tracking-[0.3em] text-foreground/40">
            NO&nbsp;SIGN-UP&nbsp;·&nbsp;INSTANT&nbsp;WORKSPACE
          </div>
        </motion.div>
      </div>

      {/* Bottom fade */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-32 bg-gradient-to-b from-transparent to-background" />
    </section>
  );
}
