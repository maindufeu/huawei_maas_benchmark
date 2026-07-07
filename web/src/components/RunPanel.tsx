import { useState } from "react";
import { Play, Square, Gavel, Crosshair, Zap } from "lucide-react";
import { api } from "../api";
import type { Config, ProgressEvent, ProviderPublic, RunConfig, RunState } from "../types";

export default function RunPanel({
  config, providers, state, log, onStart, onStop,
}: {
  config: Config;
  providers: ProviderPublic[];
  state: RunState;
  log: ProgressEvent[];
  onStart: (cfg: RunConfig) => void;
  onStop: () => void;
}) {
  const [classes, setClasses] = useState<string[]>(config.classes.map((c) => c.id));
  const [onlyObjective, setOnlyObjective] = useState(true);
  const [reps, setReps] = useState(3);
  const [temperature, setTemp] = useState(0.2);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [stream, setStream] = useState(true);
  const [warmup, setWarmup] = useState(true);
  const [timeout, setTimeout] = useState(180);
  const [parallel, setParallel] = useState(true);
  const [focusTasks, setFocusTasks] = useState<string[]>([]);
  const [focusReps, setFocusReps] = useState(2);

  // judge
  const [judgeOn, setJudgeOn] = useState(false);
  const [jUrl, setJUrl] = useState("https://api-ap-southeast-1.modelarts-maas.com/openai/v1");
  const [jModel, setJModel] = useState("glm-5.2");
  const [jKey, setJKey] = useState("");

  const running = state.status === "running";
  const selectedTasks = config.tasks.filter(
    (t) => classes.includes(t.task_class) && (!onlyObjective || t.eval_type !== "judge")
  );
  const nTasks = selectedTasks.length;
  const nFocus = selectedTasks.filter((t) => focusTasks.includes(t.id)).length;
  const nCalls = (nTasks * reps + nFocus * focusReps) * providers.length;

  async function start() {
    if (judgeOn && jKey) {
      await api.setJudge({ enabled: true, base_url: jUrl, model: jModel, api_key: jKey });
    } else {
      await api.setJudge({ enabled: false });
    }
    onStart({
      task_classes: classes, only_objective: onlyObjective, repetitions: reps,
      temperature, max_tokens: maxTokens, stream, warmup, timeout_s: timeout,
      max_parallel: parallel ? 0 : 1,
      focus_tasks: focusTasks, focus_extra_reps: focusReps,
    });
  }

  return (
    <div className="card space-y-4">
      <h2 className="text-sm font-semibold text-slate-300">Run configuration</h2>

      <div>
        <label className="label">Task classes</label>
        <div className="flex flex-wrap gap-2">
          {config.classes.map((c) => {
            const on = classes.includes(c.id);
            return (
              <button key={c.id}
                onClick={() => setClasses(on ? classes.filter((x) => x !== c.id) : [...classes, c.id])}
                className={`chip ${on ? "bg-accent/20 border-accent text-accent"
                  : "border-ink-600 text-slate-400 hover:border-slate-400"}`}
                title={c.label}>
                {c.id} · {c.label}
              </button>
            );
          })}
        </div>
      </div>

      <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
        <input type="checkbox" checked={onlyObjective} onChange={(e) => setOnlyObjective(e.target.checked)} />
        Only auto-graded (skip judge / design tasks)
      </label>

      <div className="grid grid-cols-3 gap-3">
        <Num label="Repetitions" value={reps} set={setReps} min={1} max={5} />
        <Num label="Temperature" value={temperature} set={setTemp} min={0} max={1} step={0.1} />
        <Num label="Max tokens" value={maxTokens} set={setMaxTokens} min={256} max={16384} step={256} />
        <Num label="Timeout (s)" value={timeout} set={setTimeout} min={15} max={600} step={15} />
        <Toggle label="Streaming (TTFT)" value={stream} set={setStream} />
        <Toggle label="Warm-up" value={warmup} set={setWarmup} />
      </div>

      <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer font-medium">
        <input type="checkbox" checked={parallel} onChange={(e) => setParallel(e.target.checked)} />
        <Zap size={14} /> Parallel endpoints
        <span className="text-slate-500 font-normal">
          — each provider runs in its own worker; calls to one endpoint stay sequential
        </span>
      </label>

      {/* focus: extra reps for the historically weakest tasks */}
      <div className="bg-ink-900/40 rounded-lg p-3 border border-ink-700 space-y-2">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-xs text-slate-300 font-medium">
            <Crosshair size={14} /> Focus tasks (extra repetitions)
          </span>
          {focusTasks.length > 0 && (
            <span className="text-xs text-slate-500">
              +{focusReps} reps × {nFocus} task(s)
              <button className="ml-2 text-slate-400 hover:text-slate-200 underline"
                      onClick={() => setFocusTasks([])}>clear</button>
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {selectedTasks.map((t) => {
            const on = focusTasks.includes(t.id);
            return (
              <button key={t.id}
                onClick={() => setFocusTasks(on ? focusTasks.filter((x) => x !== t.id) : [...focusTasks, t.id])}
                className={`chip ${on ? "bg-amber-400/20 border-amber-400 text-amber-300"
                  : "border-ink-600 text-slate-400 hover:border-slate-400"}`}
                title={t.title}>
                {t.id}
              </button>
            );
          })}
        </div>
        {focusTasks.length > 0 && (
          <div className="w-32">
            <Num label="Extra reps" value={focusReps} set={setFocusReps} min={1} max={10} />
          </div>
        )}
      </div>

      {/* judge */}
      <div className="bg-ink-900/40 rounded-lg p-3 border border-ink-700 space-y-2">
        <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer font-medium">
          <input type="checkbox" checked={judgeOn} onChange={(e) => setJudgeOn(e.target.checked)} />
          <Gavel size={14} /> LLM-as-judge (for T4 design tasks)
        </label>
        {judgeOn && (
          <div className="grid grid-cols-3 gap-2">
            <input className="input col-span-2" value={jUrl} onChange={(e) => setJUrl(e.target.value)} placeholder="judge base URL" />
            <input className="input" value={jModel} onChange={(e) => setJModel(e.target.value)} placeholder="model" />
            <input className="input col-span-3" type="password" value={jKey} onChange={(e) => setJKey(e.target.value)} placeholder="judge API key" />
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <button className="btn-primary" onClick={start} disabled={running || nCalls === 0}>
          <Play size={15} /> Run benchmark
        </button>
        {running && <button className="btn-danger" onClick={onStop}><Square size={14} /> Stop</button>}
        <span className="text-xs text-slate-500">
          {nTasks} tasks × {providers.length} providers × {reps}
          {nFocus > 0 && <> (+{nFocus}×{focusReps} focus)</>} = <b>{nCalls}</b> calls
          {parallel && providers.length > 1 && <> · ~{providers.length}× faster</>}
        </span>
      </div>

      {/* progress */}
      {(running || state.total > 0) && (
        <div className="space-y-2">
          <div className="h-2 bg-ink-700 rounded-full overflow-hidden">
            <div className="h-full bg-accent transition-all"
                 style={{ width: `${state.total ? (state.done / state.total) * 100 : 0}%` }} />
          </div>
          <div className="text-xs text-slate-400">
            {state.done}/{state.total} · <b>{state.status}</b>
            {state.error && <span className="text-rose-400"> · {state.error}</span>}
          </div>
          <div className="max-h-44 overflow-auto text-xs font-mono space-y-0.5 bg-ink-900/60 rounded-lg p-2">
            {log.slice(-80).map((e, i) => (
              <div key={i} className="flex gap-2">
                <span className={e.status === "ok" ? (e.passed ? "text-emerald-400" : "text-amber-400")
                  : e.error_type === "content_filter" ? "text-fuchsia-400" : "text-rose-400"}>
                  {e.status === "ok" ? (e.passed ? "✓" : "✗") : "⚠"}
                </span>
                <span className="text-slate-500">{e.model}</span>
                <span className="text-slate-400">{e.task_id}</span>
                <span className="text-slate-600">{e.latency_s}s</span>
                {e.error_type && <span className="text-rose-400">{e.error_type}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Num({ label, value, set, min, max, step = 1 }: {
  label: string; value: number; set: (v: number) => void; min: number; max: number; step?: number;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <input className="input" type="number" value={value} min={min} max={max} step={step}
             onChange={(e) => set(Number(e.target.value))} />
    </div>
  );
}
function Toggle({ label, value, set }: { label: string; value: boolean; set: (v: boolean) => void; }) {
  return (
    <label className="flex items-end gap-2 text-xs text-slate-400 cursor-pointer pb-2">
      <input type="checkbox" checked={value} onChange={(e) => set(e.target.checked)} /> {label}
    </label>
  );
}
