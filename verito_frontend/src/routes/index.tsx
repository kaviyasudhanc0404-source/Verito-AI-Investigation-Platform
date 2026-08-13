import { createFileRoute, Link } from "@tanstack/react-router";
import { Hero } from "@/components/Hero";
import { HowItWorks } from "@/components/HowItWorks";
import { Header } from "@/components/Header";
import { ArrowRight } from "lucide-react";

export const Route = createFileRoute("/")({
  component: Landing,
});

function Landing() {
  return (
    <div className="relative min-h-screen">
      <Header />
      <main>
        <Hero />
        <HowItWorks />

        <section className="px-6 pb-32">
          <div className="glass mx-auto max-w-5xl overflow-hidden rounded-3xl p-10 md:p-16 text-center relative">
            <div
              className="pointer-events-none absolute inset-0 opacity-40"
              aria-hidden
              style={{
                background:
                  "radial-gradient(60% 100% at 50% 0%, oklch(0.66 0.24 22 / 0.35), transparent 70%)",
              }}
            />
            <div className="relative">
              <div className="font-mono text-[11px] tracking-[0.3em] text-[color:var(--crimson-glow)]">
                READY&nbsp;WHEN&nbsp;YOU&nbsp;ARE
              </div>
              <h3 className="mt-4 text-3xl font-semibold tracking-tight md:text-5xl">
                Open a case. <span className="gold-text">Watch it unfold.</span>
              </h3>
              <Link
                to="/app"
                className="mt-8 inline-flex items-center gap-3 rounded-full bg-[color:var(--crimson)] px-7 py-4 text-sm font-semibold tracking-[0.2em] text-white transition hover:bg-[color:var(--crimson-glow)] crimson-glow"
              >
                START INVESTIGATION
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </div>
        </section>

        <footer className="border-t border-white/5 px-6 py-8">
          <div className="mx-auto flex max-w-7xl items-center justify-between text-xs text-muted-foreground">
            <div className="font-mono tracking-[0.3em]">VERITO&nbsp;·&nbsp;2026</div>
            <div>Connect clues. Reconstruct events. Reveal the truth.</div>
          </div>
        </footer>
      </main>
    </div>
  );
}
