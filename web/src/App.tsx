import { useEffect, useRef, useState } from "react";
import { FlaskConical } from "lucide-react";
import { api } from "./api";
import Providers from "./components/Providers";
import RunPanel from "./components/RunPanel";
import ResultsView from "./components/Results";
import type { Config, PricingTable, ProgressEvent, ProviderPublic, Results, RunConfig, RunMeta, RunState } from "./types";

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [providers, setProviders] = useState<ProviderPublic[]>([]);
  const [remember, setRemember] = useState(true);
  const [state, setState] = useState<RunState>({ run_id: null, status: "idle", done: 0, total: 0, error: null });
  const [log, setLog] = useState<ProgressEvent[]>([]);
  const [results, setResults] = useState<Results | null>(null);
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [pricing, setPricing] = useState<PricingTable>({});
  const [fatal, setFatal] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const reloadProviders = () => api.providers().then((r) => setProviders(r.providers)).catch(() => {});
  const reloadRuns = () => api.runs().then((r) => setRuns(r.runs)).catch(() => {});

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setFatal(String(e)));
    api.pricing().then((r) => setPricing(r.models)).catch(() => {});
    reloadProviders();
    reloadRuns();
    // restore the last run so a reopened browser (or a closed window) shows results
    api.results().then((r) => { if (r.composite?.length) setResults(r); }).catch(() => {});
    return () => esRef.current?.close();
  }, []);

  function subscribe() {
    esRef.current?.close();
    const es = new EventSource("/api/run/events");
    esRef.current = es;
    es.onmessage = (msg) => {
      const ev: ProgressEvent = JSON.parse(msg.data);
      if (ev.type === "progress") {
        setState((s) => ({ ...s, done: ev.done ?? s.done, total: ev.total ?? s.total, status: "running" }));
        setLog((l) => [...l, ev]);
      } else if (ev.type === "state") {
        setState((s) => ({ ...s, ...(ev as any) }));
      } else if (ev.type === "error") {
        setState((s) => ({ ...s, status: "error", error: ev.message ?? "error" }));
      } else if (ev.type === "done" || ev.type === "closed") {
        setState((s) => ({ ...s, status: (ev as any).status ?? "done", done: (ev as any).done ?? s.done }));
        es.close();
        api.results().then(setResults).catch(() => {});
        reloadRuns();
      }
    };
    es.onerror = () => { es.close(); };
  }

  async function startRun(cfg: RunConfig) {
    setLog([]); setResults(null);
    try {
      const r = await api.startRun(cfg);
      setState({ run_id: r.run_id, status: "running", done: 0, total: 0, error: null });
      subscribe();
    } catch (e) {
      setState((s) => ({ ...s, status: "error", error: String(e) }));
    }
  }

  const stopRun = () => api.stopRun();

  if (fatal) return <Center>Backend unreachable: {fatal}. Is the API running on :8000?</Center>;
  if (!config) return <Center>Loading…</Center>;

  return (
    <div className="min-h-full">
      <header className="border-b border-ink-600 bg-ink-800/60 backdrop-blur sticky top-0 z-10">
        <div className="max-w-[1400px] mx-auto px-6 py-3 flex items-center gap-3">
          <FlaskConical className="text-accent" size={22} />
          <div className="flex-1">
            <h1 className="text-lg font-semibold">MaaS Coding Benchmark</h1>
            <p className="text-xs text-slate-500">Huawei ModelArts MaaS vs baselines · Framework v1.0</p>
          </div>
          {runs.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Saved runs</span>
              <select className="input !py-1.5 !w-auto text-xs"
                onChange={(e) => {
                  const id = e.target.value;
                  if (id) api.results(id).then((r) => { if (r.composite?.length) setResults(r); });
                }}>
                <option value="">latest / current</option>
                {runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.saved_at} · {r.calls} calls · {r.status}
                    {r.pass_at_1 != null ? ` · ${(r.pass_at_1 * 100).toFixed(0)}%` : ""}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-6 py-6 grid grid-cols-12 gap-6">
        <div className="col-span-12 lg:col-span-4 space-y-6">
          <Providers config={config} providers={providers} remember={remember}
                     setRemember={setRemember} reload={reloadProviders} />
        </div>
        <div className="col-span-12 lg:col-span-8 space-y-6">
          <RunPanel config={config} providers={providers} state={state} log={log}
                    onStart={startRun} onStop={stopRun} />
          {results && <ResultsView results={results} p95Gate={config.p95_gate} pricing={pricing} />}
        </div>
      </main>
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div className="min-h-full grid place-items-center text-slate-400 text-sm p-8 text-center">{children}</div>;
}
