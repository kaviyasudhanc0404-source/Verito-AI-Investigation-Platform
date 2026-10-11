import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowRight, Mail, Lock, User as UserIcon, ScanLine, ShieldCheck } from "lucide-react";
import { VeritoWordmark } from "@/components/VeritoWordmark";
import { authApi, type TokenResponse } from "@/lib/api";
import { isAuthenticated, setToken, setUser } from "@/lib/auth-store";
import { signInWithGoogle } from "@/lib/firebase";

export const Route = createFileRoute("/auth")({
  beforeLoad: () => {
    if (isAuthenticated()) {
      throw redirect({ to: "/app" });
    }
  },
  component: AuthPage,
  head: () => ({
    meta: [
      { title: "VERITO — Connect" },
      {
        name: "description",
        content: "Log in or create an account to enter the VERITO investigation workspace.",
      },
    ],
  }),
});

function AuthPage() {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const saveAndNavigate = (tokenRes: TokenResponse) => {
    setToken(tokenRes.access_token);
    setUser({ user_id: tokenRes.user_id, name: tokenRes.name, email: tokenRes.email });
    navigate({ to: "/app" });
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (mode === "signup") {
      if (!name.trim()) {
        setError("Please enter your name.");
        return;
      }
      if (password.length < 8) {
        setError("Password must be at least 8 characters.");
        return;
      }
      if (password !== confirmPassword) {
        setError("Passwords do not match.");
        return;
      }
    }

    setLoading(true);
    try {
      if (mode === "signup") {
        const res = await authApi.signup(name.trim(), email.trim(), password);
        saveAndNavigate(res);
      } else {
        const res = await authApi.login(email.trim(), password);
        saveAndNavigate(res);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Authentication failed.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const onGoogleLogin = async () => {
    setError(null);
    setLoading(true);
    try {
      const idToken = await signInWithGoogle();
      const res = await authApi.googleLogin(idToken);
      saveAndNavigate(res);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Google login failed.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-[100svh] w-full items-center justify-center overflow-hidden px-4 py-16">
      {/* Ambient background */}
      <div className="grid-bg pointer-events-none absolute inset-0" aria-hidden />
      <div
        className="pointer-events-none absolute inset-0 opacity-70"
        aria-hidden
        style={{
          background:
            "radial-gradient(60% 40% at 50% 50%, oklch(0.28 0.14 22 / 0.35), transparent 70%)",
        }}
      />
      <div
        className="pointer-events-none absolute -top-40 left-1/2 h-[70vh] w-[60vw] -translate-x-1/2 opacity-40 blur-2xl"
        aria-hidden
        style={{
          background:
            "conic-gradient(from 210deg at 50% 0%, transparent 0deg, oklch(0.66 0.24 22 / 0.4) 20deg, transparent 40deg)",
        }}
      />

      <Link to="/" className="absolute left-6 top-6 z-10">
        <VeritoWordmark size="sm" animate={false} />
      </Link>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="glass relative z-10 w-full max-w-md rounded-3xl p-8"
      >
        <div className="mb-6 text-center">
          <div className="glass mx-auto inline-flex items-center gap-2 rounded-full px-3 py-1.5 font-mono text-[10px] tracking-[0.3em] text-foreground/70">
            <ScanLine className="h-3.5 w-3.5 text-[color:var(--crimson-glow)]" />
            SECURE&nbsp;·&nbsp;CHANNEL
          </div>
          <h1 className="mt-5 text-2xl font-semibold tracking-tight md:text-3xl">
            {mode === "login" ? "Resume investigation" : "Open a new case file"}
          </h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            {mode === "login"
              ? "Enter your credentials to access the workspace."
              : "Create an account to start connecting clues."}
          </p>
        </div>

        {/* Mode switch */}
        <div className="mb-6 grid grid-cols-2 rounded-full border border-white/10 bg-white/[0.03] p-1 text-sm">
          {(["login", "signup"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m);
                setError(null);
              }}
              className={`relative rounded-full px-4 py-2 font-medium tracking-wide transition ${
                mode === m ? "text-white" : "text-foreground/60 hover:text-foreground"
              }`}
            >
              {mode === m && (
                <motion.span
                  layoutId="auth-pill"
                  className="absolute inset-0 rounded-full bg-[color:var(--crimson)] crimson-glow"
                  transition={{ type: "spring", stiffness: 400, damping: 32 }}
                />
              )}
              <span className="relative">{m === "login" ? "Log in" : "Sign up"}</span>
            </button>
          ))}
        </div>

        {/* Error message */}
        {error && (
          <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
            {error}
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-3">
          <AnimatePresence initial={false}>
            {mode === "signup" && (
              <motion.div
                key="name"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
              >
                <Field
                  icon={<UserIcon className="h-4 w-4" />}
                  label="Full name"
                  type="text"
                  placeholder="Detective name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </motion.div>
            )}
          </AnimatePresence>

          <Field
            icon={<Mail className="h-4 w-4" />}
            label="Email"
            type="email"
            placeholder="you@investigate.co"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <Field
            icon={<Lock className="h-4 w-4" />}
            label="Password"
            type="password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          <AnimatePresence initial={false}>
            {mode === "signup" && (
              <motion.div
                key="confirm"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
              >
                <Field
                  icon={<ShieldCheck className="h-4 w-4" />}
                  label="Confirm password"
                  type="password"
                  placeholder="••••••••"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                />
              </motion.div>
            )}
          </AnimatePresence>

          {mode === "login" && (
            <div className="flex items-center justify-between text-xs">
              <label className="inline-flex items-center gap-2 text-foreground/60">
                <input type="checkbox" className="h-3.5 w-3.5 accent-[color:var(--crimson)]" />
                Remember this device
              </label>
              <button type="button" className="text-[color:var(--crimson-glow)] hover:underline">
                Forgot?
              </button>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="group relative mt-2 inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-full bg-[color:var(--crimson)] px-6 py-3.5 text-sm font-semibold tracking-[0.2em] text-white transition hover:bg-[color:var(--crimson-glow)] crimson-glow disabled:opacity-60"
          >
            <span className="absolute inset-0 overflow-hidden rounded-full">
              <span className="absolute inset-y-0 w-1/3 animate-laser bg-gradient-to-r from-transparent via-white/40 to-transparent" />
            </span>
            <span className="relative">
              {loading
                ? "AUTHENTICATING…"
                : mode === "login"
                  ? "ENTER WORKSPACE"
                  : "OPEN CASE FILE"}
            </span>
            <ArrowRight className="relative h-4 w-4 transition group-hover:translate-x-0.5" />
          </button>

          <div className="my-4 flex items-center gap-3 text-[10px] font-mono tracking-[0.3em] text-foreground/40">
            <span className="h-px flex-1 bg-white/10" />
            OR
            <span className="h-px flex-1 bg-white/10" />
          </div>

          <button
            type="button"
            onClick={onGoogleLogin}
            disabled={loading}
            className="flex w-full items-center justify-center gap-3 rounded-full border border-white/10 bg-white/[0.03] px-6 py-3 text-sm font-medium text-foreground/90 transition hover:border-[color:var(--gold)]/40 hover:bg-white/[0.06] disabled:opacity-60"
          >
            <svg className="h-4 w-4" viewBox="0 0 48 48" aria-hidden>
              <path
                fill="#EA4335"
                d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
              />
              <path
                fill="#4285F4"
                d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
              />
              <path
                fill="#FBBC05"
                d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
              />
              <path
                fill="#34A853"
                d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
              />
            </svg>
            Continue with Google
          </button>
        </form>

        <div className="mt-6 text-center font-mono text-[10px] tracking-[0.3em] text-foreground/40">
          {mode === "login" ? "NEW HERE?" : "ALREADY REGISTERED?"}
          <button
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "signup" : "login");
              setError(null);
            }}
            className="ml-2 text-[color:var(--crimson-glow)] hover:underline"
          >
            {mode === "login" ? "SIGN UP" : "LOG IN"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

function Field({
  icon,
  label,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & { icon: React.ReactNode; label: string }) {
  return (
    <label className="block">
      <div className="mb-1.5 font-mono text-[10px] tracking-[0.25em] text-foreground/50">
        {label.toUpperCase()}
      </div>
      <div className="glass flex items-center gap-2 rounded-xl px-3 py-2.5 focus-within:border-[color:var(--crimson)]/60">
        <span className="text-[color:var(--crimson-glow)]">{icon}</span>
        <input
          {...rest}
          className="w-full bg-transparent text-sm text-foreground placeholder:text-foreground/35 focus:outline-none"
        />
      </div>
    </label>
  );
}
