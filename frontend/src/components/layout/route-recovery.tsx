import { Component, type ReactNode, useEffect, useRef } from "react";
import { ArrowLeft, FileQuestion, RefreshCw, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { SpinnerBlock } from "@/components/ui/spinner";

type RecoverySurfaceProps = {
  actions: ReactNode;
  description: string;
  icon: ReactNode;
  role: "alert" | "status";
  testId: "route-load-error" | "route-not-found";
  title: string;
};

function decodePathname(pathname: string): string {
  try {
    return decodeURI(pathname);
  } catch {
    return pathname;
  }
}

function RecoverySurface({
  actions,
  description,
  icon,
  role,
  testId,
  title,
}: RecoverySurfaceProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section
      className="flex flex-1 flex-col items-center justify-center gap-6 py-12 text-center"
      data-testid={testId}
      role={role}
    >
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl border bg-card text-primary shadow-sm">
        {icon}
      </div>
      <div className="max-w-md space-y-2">
        <h1
          ref={headingRef}
          className="rounded-md px-1 text-2xl font-semibold tracking-tight outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          data-testid="route-recovery-heading"
          tabIndex={-1}
        >
          {title}
        </h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">{actions}</div>
    </section>
  );
}

export function NotFoundPage() {
  const { t } = useTranslation();

  return (
    <RecoverySurface
      actions={
        <Button asChild className="press-scale">
          <Link data-testid="route-dashboard-link" to="/dashboard">
            <ArrowLeft aria-hidden="true" />
            {t("routeRecovery.goToDashboard")}
          </Link>
        </Button>
      }
      description={t("routeRecovery.notFoundDescription")}
      icon={<FileQuestion aria-hidden="true" className="h-6 w-6" />}
      role="status"
      testId="route-not-found"
      title={t("routeRecovery.notFoundTitle")}
    />
  );
}

export function RouteLoadError() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const isDashboardPath =
    decodePathname(pathname).replace(/\/+$/, "").toLowerCase() === "/dashboard";
  const dashboardLabel = (
    <>
      <ArrowLeft aria-hidden="true" />
      {t("routeRecovery.goToDashboard")}
    </>
  );

  return (
    <RecoverySurface
      actions={
        <>
          <Button
            className="press-scale"
            data-testid="route-retry"
            onClick={() => window.location.reload()}
            type="button"
          >
            <RefreshCw aria-hidden="true" />
            {t("routeRecovery.reload")}
          </Button>
          <Button asChild className="press-scale" variant="outline">
            {isDashboardPath ? (
              <a data-testid="route-dashboard-link" href="/dashboard">
                {dashboardLabel}
              </a>
            ) : (
              <Link data-testid="route-dashboard-link" to="/dashboard">
                {dashboardLabel}
              </Link>
            )}
          </Button>
        </>
      }
      description={t("routeRecovery.loadErrorDescription")}
      icon={<TriangleAlert aria-hidden="true" className="h-6 w-6" />}
      role="alert"
      testId="route-load-error"
      title={t("routeRecovery.loadErrorTitle")}
    />
  );
}

export function RouteLoading() {
  return (
    <div className="flex flex-1 items-center justify-center py-12" data-testid="route-loading">
      <SpinnerBlock />
    </div>
  );
}

type RouteErrorBoundaryProps = {
  children: ReactNode;
  resetKey: string;
};

type RouteErrorBoundaryState = {
  failed: boolean;
};

export class RouteErrorBoundary extends Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): RouteErrorBoundaryState {
    return { failed: true };
  }

  componentDidUpdate(
    previousProps: RouteErrorBoundaryProps,
    previousState: RouteErrorBoundaryState,
  ): void {
    if (
      previousState.failed &&
      this.state.failed &&
      previousProps.resetKey !== this.props.resetKey
    ) {
      this.setState({ failed: false });
    }
  }

  render(): ReactNode {
    return this.state.failed ? <RouteLoadError /> : this.props.children;
  }
}
