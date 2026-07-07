"""Headless batch runner — same core as the webapp, for reproducible CI-style runs.

Example:
    export HUAWEI_MAAS_KEY=sk-...
    python cli.py --models glm-5.2 deepseek-v3.2 --reps 3 --out results/run1

Add baselines with --provider name=URL::model::ENV_VAR (key read from that env var).
"""
from __future__ import annotations

import argparse
import os
import sys

from maasbench.pricing import Pricing
from maasbench.providers import HUAWEI_BASE_URL, HUAWEI_MODELS, Provider
from maasbench.runner import RunConfig, planned_total, run_benchmark
from maasbench.scoring import aggregate, composite_scores, decision_matrix, rows_to_df
from maasbench.tasks import load_tasks


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="MaaS coding benchmark (headless).")
    p.add_argument("--tasks", default="tasks", help="tasks dir or file")
    p.add_argument("--models", nargs="*", default=["glm-5.2"],
                   help="Huawei model ids (key from $HUAWEI_MAAS_KEY)")
    p.add_argument("--huawei-key-env", default="HUAWEI_MAAS_KEY")
    p.add_argument("--base-url", default=HUAWEI_BASE_URL)
    p.add_argument("--provider", action="append", default=[],
                   help="extra provider  name=BASEURL::model::ENV_VAR")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--no-stream", action="store_true")
    p.add_argument("--only-objective", action="store_true",
                   help="skip judge tasks")
    p.add_argument("--parallel", type=int, default=0, metavar="N",
                   help="endpoints hit concurrently (0 = all, 1 = sequential)")
    p.add_argument("--focus", nargs="*", default=[], metavar="TASK_ID",
                   help="task ids that get extra repetitions (the weak ones)")
    p.add_argument("--focus-reps", type=int, default=2,
                   help="extra repetitions for --focus tasks (default 2)")
    p.add_argument("--out", default="results/run")
    return p.parse_args(argv)


def build_providers(args) -> list[Provider]:
    provs: list[Provider] = []
    tier = {m: t for m, t in HUAWEI_MODELS}
    key = os.environ.get(args.huawei_key_env, "")
    for m in args.models:
        if not key:
            print(f"WARNING: ${args.huawei_key_env} is empty", file=sys.stderr)
        provs.append(Provider(name=f"huawei_{m}", base_url=args.base_url, model=m,
                              api_key=key, tier=tier.get(m, "frontier")))
    for spec in args.provider:
        name, rest = spec.split("=", 1)
        url, model, env = rest.split("::")
        provs.append(Provider(name=name, base_url=url, model=model,
                              api_key=os.environ.get(env, ""), tier="balanced"))
    return provs


def main(argv=None):
    args = parse_args(argv)
    tasks = load_tasks(args.tasks)
    if args.only_objective:
        tasks = [t for t in tasks if t.evaluation.get("type") != "judge"]
    providers = build_providers(args)
    cfg = RunConfig(temperature=args.temperature, max_tokens=args.max_tokens,
                    repetitions=args.reps, stream=not args.no_stream,
                    max_parallel_providers=args.parallel,
                    focus_task_ids=args.focus, focus_extra_reps=args.focus_reps)

    total = planned_total(tasks, providers, cfg)
    workers = args.parallel or len(providers)
    print(f"Running {len(tasks)} tasks × {len(providers)} providers × {args.reps} reps"
          f"{' (+focus)' if args.focus else ''} = {total} calls "
          f"[{min(workers, len(providers))} endpoint(s) in parallel]")

    def progress(info):
        r = info["row"]
        print(f"[{info['done']:>4}/{info['total']}] {r.model:<22} {r.task_id:<28} "
              f"{r.status:<10} {r.latency_s:>6.1f}s pass={r.passed}")

    rows = run_benchmark(tasks, providers, cfg, pricing=Pricing.load(), progress=progress)

    df = rows_to_df(rows)
    agg = aggregate(df)
    comp = composite_scores(agg)
    dm = decision_matrix(comp)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    df.to_csv(args.out + "_raw.csv", index=False)
    comp.to_csv(args.out + "_composite.csv", index=False)
    dm.to_csv(args.out + "_decision.csv", index=False)
    print("\n=== DECISION MATRIX ===")
    print(dm.to_string(index=False))
    print(f"\nWrote {args.out}_raw.csv / _composite.csv / _decision.csv")


if __name__ == "__main__":
    main()
