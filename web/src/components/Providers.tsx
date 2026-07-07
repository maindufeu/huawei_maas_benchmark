import { useState } from "react";
import { Plus, Trash2, Plug, Save, Eraser, Server, Cloud, Cpu, Boxes, Wind } from "lucide-react";
import { api } from "../api";
import type { Config, ProviderPublic, TestResult } from "../types";

const HUAWEI_URL = "https://api-ap-southeast-1.modelarts-maas.com/openai/v1";
const GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/";
const LOCAL_URL = "http://127.0.0.1:8080/v1";
const AZURE_URL = "https://<resource>.services.ai.azure.com/openai/v1";

const PRESETS: Record<string, { url: string; model: string; icon: JSX.Element }> = {
  "Huawei MaaS": { url: HUAWEI_URL, model: "", icon: <Cloud size={15} /> },
  "Azure OpenAI": { url: AZURE_URL, model: "DeepSeek-V4-Pro", icon: <Wind size={15} /> },
  "Google Gemini": { url: GEMINI_URL, model: "gemini-3.5-flash", icon: <Boxes size={15} /> },
  "Local (llama.cpp)": { url: LOCAL_URL, model: "local-model", icon: <Cpu size={15} /> },
  "Custom (OpenAI-compatible)": { url: "", model: "", icon: <Server size={15} /> },
};
const TIERS = ["frontier", "balanced", "cost", "reasoning", "small"];

export default function Providers({
  config, providers, remember, setRemember, reload,
}: {
  config: Config;
  providers: ProviderPublic[];
  remember: boolean;
  setRemember: (v: boolean) => void;
  reload: () => void;
}) {
  const [ptype, setPtype] = useState("Huawei MaaS");
  const preset = PRESETS[ptype];
  const [url, setUrl] = useState(preset.url);
  const [model, setModel] = useState(preset.model);
  const [hwModels, setHwModels] = useState<string[]>(["glm-5.2"]);
  const [effort, setEffort] = useState("(none)");
  const [tier, setTier] = useState("frontier");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [tests, setTests] = useState<TestResult[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const isLocal = ptype === "Local (llama.cpp)";
  const isHuawei = ptype === "Huawei MaaS";

  function changeType(t: string) {
    setPtype(t);
    setUrl(PRESETS[t].url);
    setModel(PRESETS[t].model);
    setTier(t === "Local (llama.cpp)" ? "small" : "frontier");
    setErr(null);
  }

  async function add() {
    setErr(null);
    if (!url) return setErr("Enter the base URL.");
    if (!apiKey && !isLocal) return setErr("Enter the API key.");
    setBusy(true);
    try {
      await api.addProvider({
        type: ptype, base_url: url, api_key: apiKey, tier,
        models: isHuawei ? hwModels : [model],
        reasoning_effort: isHuawei ? effort : "(none)",
        remember,
      });
      setApiKey("");
      reload();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  async function runTest() {
    setBusy(true); setTests(null);
    try { setTests((await api.test()).results); }
    catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
          <Server size={16} /> Providers
        </h2>
        <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
          <input type="checkbox" checked={remember}
                 onChange={(e) => setRemember(e.target.checked)} />
          💾 Remember on this machine
        </label>
      </div>

      {/* add form */}
      <div className="space-y-3 bg-ink-900/50 rounded-lg p-3 border border-ink-700">
        <div>
          <label className="label">Type</label>
          <div className="grid grid-cols-2 gap-2">
            {Object.keys(PRESETS).map((t) => (
              <button key={t} onClick={() => changeType(t)}
                className={`btn justify-start text-xs ${ptype === t ? "btn-primary" : "btn-ghost"}`}>
                {PRESETS[t].icon}{t}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="label">Base URL</label>
          <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>

        {isHuawei ? (
          <div>
            <label className="label">Models</label>
            <div className="flex flex-wrap gap-2">
              {config.huawei_models.map((m) => {
                const on = hwModels.includes(m.model);
                return (
                  <button key={m.model}
                    onClick={() => setHwModels(on
                      ? hwModels.filter((x) => x !== m.model)
                      : [...hwModels, m.model])}
                    className={`chip ${on ? "bg-accent/20 border-accent text-accent"
                      : "border-ink-600 text-slate-400 hover:border-slate-400"}`}>
                    {m.model}
                  </button>
                );
              })}
            </div>
            <div className="mt-2">
              <label className="label">reasoning_effort (GLM-5.2 / DeepSeek)</label>
              <select className="input" value={effort} onChange={(e) => setEffort(e.target.value)}>
                {["(none)", "high", "max"].map((o) => <option key={o}>{o}</option>)}
              </select>
            </div>
          </div>
        ) : (
          <div>
            <label className="label">Model id</label>
            <input className="input" value={model} onChange={(e) => setModel(e.target.value)} />
            {ptype === "Google Gemini" && (
              <p className="text-[11px] text-amber-400/80 mt-1">
                Use a real id (<code>gemini-3.5-flash</code>, <code>gemini-3.1-pro-preview</code>).
                The generic <code>gemini-3-pro</code> returns 404.
              </p>
            )}
            {ptype === "Azure OpenAI" && (
              <p className="text-[11px] text-sky-400/80 mt-1">
                Base URL = your resource's <code>/openai/v1</code> path. Model = the
                <b> deployment name</b> (e.g. <code>DeepSeek-V4-Pro</code>). Compares the same model
                across vendors — it's tagged <code>azure:…</code> so it won't merge with Huawei's.
              </p>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">API key{isLocal && " (optional)"}</label>
            <input className="input" type="password" value={apiKey}
                   onChange={(e) => setApiKey(e.target.value)} placeholder={isLocal ? "not required" : "sk-…"} />
          </div>
          <div>
            <label className="label">Tier</label>
            <select className="input" value={tier} onChange={(e) => setTier(e.target.value)}>
              {TIERS.map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
        </div>

        {err && <p className="text-xs text-rose-400">{err}</p>}
        <button className="btn-primary w-full justify-center" onClick={add} disabled={busy}>
          <Plus size={15} /> Add provider
        </button>
      </div>

      {/* configured list */}
      {providers.length > 0 && (
        <div className="space-y-2">
          {providers.map((p) => (
            <div key={p.index} className="flex items-center justify-between bg-ink-900/40 rounded-lg px-3 py-2">
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">
                  {p.name}{" "}
                  <span className="text-slate-500">· {p.tier}</span>
                  {p.is_local && <span className="chip ml-1 border-emerald-600 text-emerald-400">local · $0</span>}
                </div>
                <div className="text-xs text-slate-500 truncate">{p.model} · {p.base_url}</div>
              </div>
              <button className="btn-ghost !p-1.5" onClick={async () => { await api.delProvider(p.index, remember); reload(); }}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          <div className="flex gap-2">
            <button className="btn-ghost flex-1 justify-center" onClick={runTest} disabled={busy}>
              <Plug size={14} /> Test connection
            </button>
            <button className="btn-ghost justify-center" onClick={async () => { await api.save(); }} disabled={busy}>
              <Save size={14} />
            </button>
            <button className="btn-danger justify-center" onClick={async () => { await api.forget(); reload(); }}>
              <Eraser size={14} />
            </button>
          </div>
        </div>
      )}

      {tests && (
        <div className="space-y-1">
          {tests.map((t) => (
            <div key={t.name} className={`text-xs rounded px-2 py-1 ${t.ok
              ? "bg-emerald-900/30 text-emerald-300" : "bg-rose-900/30 text-rose-300"}`}>
              {t.ok ? "✅" : "⛔"} <b>{t.name}</b> · {t.status}
              {t.ok ? ` · ${t.latency_s}s` : ` · ${t.error_type}: ${t.error_message}`}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
