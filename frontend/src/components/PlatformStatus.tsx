import { useEffect, useState } from "react";
import { Activity, AlertCircle, CheckCircle2, CircleDashed } from "lucide-react";
import { apiGet, type SystemStatus } from "../lib/api";

type LoadState = "loading" | "available" | "unavailable";

function statusText(status: string | undefined): string {
  if (status === "healthy") return "Operational";
  if (status === "starting") return "Worker starting";
  if (status === "stale") return "Worker stale";
  if (status === "unavailable") return "Unavailable";
  return "Checking status";
}

export function PlatformStatus() {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [state, setState] = useState<LoadState>("loading");

  useEffect(() => {
    let active = true;
    const refresh = () => {
      apiGet<SystemStatus>("/system/status")
        .then((value) => {
          if (active) {
            setData(value);
            setState("available");
          }
        })
        .catch(() => {
          if (active) {
            setData(null);
            setState("unavailable");
          }
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const healthy = state === "available" && data?.api === "healthy" && data.database === "healthy";
  const workerReady = data?.worker === "healthy";
  const Icon = state === "loading" ? CircleDashed : healthy ? CheckCircle2 : AlertCircle;
  const label = state === "unavailable" ? "API unavailable" : statusText(data?.worker);

  return (
    <div className={`platform-status ${healthy && workerReady ? "is-healthy" : healthy ? "is-pending" : "is-error"}`}>
      <span className="status-icon"><Icon size={15} /></span>
      <span className="status-copy">
        <strong>{healthy && workerReady ? "Platform operational" : label}</strong>
        <small>{state === "available" ? `API ${data?.api} · DB ${data?.database} · Worker ${data?.worker}` : "Live component status"}</small>
      </span>
      <span className="status-dot" aria-hidden="true" />
    </div>
  );
}

export function StatusCards({ data, loading, error }: { data: SystemStatus | null; loading: boolean; error: boolean }) {
  const components = [
    { label: "API", value: data?.api },
    { label: "Database", value: data?.database },
    { label: "Worker", value: data?.worker },
  ];

  return (
    <div className="status-grid">
      {components.map((component) => {
        const value = error ? "unavailable" : loading ? "checking" : component.value || "unknown";
        const isHealthy = value === "healthy";
        return (
          <article className="status-card" key={component.label}>
            <div className="status-card-heading"><span>{component.label}</span><Activity size={16} /></div>
            <div className={`status-value ${isHealthy ? "healthy" : value === "unavailable" ? "error" : "pending"}`}>
              <span className="status-dot" />{value === "checking" ? "Checking" : value === "starting" ? "Starting" : value}
            </div>
            <p>{isHealthy ? "Responding to health checks" : component.label === "Worker" && value === "starting" ? "Waiting for first heartbeat" : "Live status from the control plane"}</p>
          </article>
        );
      })}
    </div>
  );
}

