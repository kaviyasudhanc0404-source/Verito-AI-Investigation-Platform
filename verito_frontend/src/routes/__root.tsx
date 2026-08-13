import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { useEffect, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { reportApplicationError } from "../lib/error-reporting";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="glass max-w-md rounded-2xl p-10 text-center">
        <div className="font-mono text-xs tracking-[0.4em] text-[color:var(--crimson-glow)]">
          CASE&nbsp;NOT&nbsp;FOUND
        </div>
        <h1 className="mt-4 text-7xl font-bold tracking-tight">404</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          No evidence exists at this location.
        </p>
        <Link
          to="/"
          className="mt-6 inline-flex items-center justify-center rounded-md bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          Return to VERITO
        </Link>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportApplicationError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="glass max-w-md rounded-2xl p-10 text-center">
        <div className="font-mono text-xs tracking-[0.4em] text-[color:var(--crimson-glow)]">
          INVESTIGATION&nbsp;INTERRUPTED
        </div>
        <h1 className="mt-4 text-xl font-semibold tracking-tight">
          The investigation could not resume
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          A signal was lost mid-analysis. Try re-running the process.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "VERITO — AI Investigation Platform" },
      {
        name: "description",
        content:
          "VERITO connects clues, reconstructs events and reveals the truth. A next-generation AI investigation workspace.",
      },
      { property: "og:title", content: "VERITO — AI Investigation Platform" },
      {
        property: "og:description",
        content: "VERITO connects clues, reconstructs events and reveals the truth. A next-generation AI investigation workspace.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
      { name: "twitter:title", content: "VERITO — AI Investigation Platform" },
      { name: "twitter:description", content: "VERITO connects clues, reconstructs events and reveals the truth. A next-generation AI investigation workspace." },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "icon", href: "/detect.ico", type: "image/x-icon" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Big+Shoulders+Display:wght@700;800;900&family=Bebas+Neue&display=swap",
      },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  return (
    <QueryClientProvider client={queryClient}>
      <Outlet />
    </QueryClientProvider>
  );
}
