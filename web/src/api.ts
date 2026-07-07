import type {
  Config, PricingTable, ProviderPublic, Results, RunConfig, RunMeta, RunState, TestResult,
} from "./types";

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  config: () => fetch("/api/config").then(j<Config>),

  pricing: () => fetch("/api/pricing").then(j<{ models: PricingTable }>),

  providers: () =>
    fetch("/api/providers").then(j<{ providers: ProviderPublic[]; judge: boolean }>),

  addProvider: (spec: Record<string, unknown>) =>
    fetch("/api/providers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    }).then(j<{ providers: ProviderPublic[] }>),

  delProvider: (index: number, remember: boolean) =>
    fetch(`/api/providers/${index}?remember=${remember}`, { method: "DELETE" })
      .then(j<{ providers: ProviderPublic[] }>),

  forget: () =>
    fetch("/api/providers/forget", { method: "POST" }).then(j<{ providers: ProviderPublic[] }>),

  save: () => fetch("/api/providers/save", { method: "POST" }).then(j),

  test: () => fetch("/api/providers/test", { method: "POST" }).then(j<{ results: TestResult[] }>),

  setJudge: (spec: Record<string, unknown>) =>
    fetch("/api/judge", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    }).then(j<{ judge: boolean }>),

  startRun: (cfg: RunConfig) =>
    fetch("/api/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    }).then(j<RunState & { run_id: string }>),

  stopRun: () => fetch("/api/run/stop", { method: "POST" }).then(j),

  results: (runId?: string) =>
    fetch(`/api/results${runId ? `?run_id=${runId}` : ""}`).then(j<Results>),

  runs: () => fetch("/api/runs").then(j<{ runs: RunMeta[] }>),

  exportUrl: (fmt: "csv" | "md" | "html", table = "raw") =>
    `/api/results/export?fmt=${fmt}&table=${table}`,
};
