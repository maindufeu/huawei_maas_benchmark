"""Offline self-test: exercises tasks, evaluators, sandbox, and scoring WITHOUT a live API.

Run: python selftest.py   (expects to pass with a green summary)
"""
from maasbench import evaluators
from maasbench.evaluators import get_evaluator
from maasbench.tasks import extract_code, load_tasks
from maasbench.runner import RunRow
from maasbench.scoring import aggregate, composite_scores, decision_matrix, rows_to_df


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, name


# 1. task loading
tasks = load_tasks("tasks")
by_id = {t.id: t for t in tasks}
check(f"loaded {len(tasks)} tasks", len(tasks) >= 10)
check("classes present", {t.task_class for t in tasks} >= {"T1", "T2", "T3", "T4", "T5"})

# 2. code extraction
code = extract_code("blah\n```python\ndef f():\n    return 1\n```\ntail", "python")
check("extract_code pulls fenced block", code == "def f():\n    return 1")

# 3. evaluators registered
check("evaluators registered", set(evaluators.available()) >= {"unit_test", "contains", "judge", "json_keys"})

# 4. unit_test evaluator — correct solution passes
t = by_id["t1_two_sum"]
good = "```python\ndef two_sum(nums, target):\n    seen={}\n    for i,n in enumerate(nums):\n        if target-n in seen: return [seen[target-n], i]\n        seen[n]=i\n```"
ev = get_evaluator("unit_test").evaluate(t, good)
check(f"correct two_sum passes ({ev.n_passed}/{ev.n_checks})", ev.passed and ev.n_checks == 4)

# 5. unit_test evaluator — wrong solution fails
bad = "```python\ndef two_sum(nums, target):\n    return [0, 0]\n```"
ev2 = get_evaluator("unit_test").evaluate(t, bad)
check("wrong two_sum fails", not ev2.passed and ev2.score < 1.0)

# 6. debugging task with context file (buggy.py present) — a correct fix passes
t3 = by_id["t3_binary_search_offbyone"]
fix = ("```python\ndef binary_search(arr, target):\n    lo, hi = 0, len(arr)-1\n"
       "    while lo <= hi:\n        mid=(lo+hi)//2\n        if arr[mid]==target: return mid\n"
       "        elif arr[mid]<target: lo=mid+1\n        else: hi=mid-1\n    return -1\n```")
ev3 = get_evaluator("unit_test").evaluate(t3, fix)
check(f"binary search fix passes ({ev3.n_passed}/{ev3.n_checks})", ev3.passed)

# 7. contains evaluator
t5 = by_id["fp_jwt_validation"]
ev5 = get_evaluator("contains").evaluate(t5, "The code never checks the signature, that's bad.")
check("contains evaluator matches 'signature'", ev5.passed)

# 8. scoring pipeline on fabricated rows
def row(model, tclass, tid, rep, passed, lat, cost, status="ok", err=None):
    return RunRow(task_id=tid, task_class=tclass, task_title=tid, language="python",
                  difficulty="easy", provider=model, model=model, vendor="custom",
                  tier="frontier",
                  repetition=rep, status=status, latency_s=lat, ttft_s=lat*0.2,
                  tokens_per_second=50.0, prompt_tokens=100, completion_tokens=200,
                  reasoning_tokens=0, passed=passed, eval_score=1.0 if passed else 0.0,
                  n_checks=4, n_passed=4 if passed else 1, eval_type="unit_test",
                  cost_usd=cost, error_type=err)

rows = []
for rep in (1, 2, 3):
    rows.append(row("glm-5.2", "T1", "t1_two_sum", rep, True, 30.0, 0.001))
    rows.append(row("deepseek-v3.2", "T1", "t1_two_sum", rep, rep != 2, 6.0, 0.0002))
rows.append(row("glm-5.2", "T1", "t1_lru", 1, False, 0.0, None, status="filtered", err="content_filter"))

df = rows_to_df(rows)
agg = aggregate(df)
comp = composite_scores(agg)
dm = decision_matrix(comp)
check("aggregate produced rows", not agg.empty)
check("composite has verdict col", "verdict" in comp.columns)
check("high-latency glm-5.2 never gets 🟢", not comp[comp.model == "glm-5.2"].verdict.str.startswith("🟢").any())

# focused gate test: a lone strong model with p95>15s must be 🟡 with a p95 tag, not 🟢
gate_rows = [row("solo", "T2", "t2_x", r, True, 20.0, 0.0005) for r in (1, 2, 3)]
gate_comp = composite_scores(aggregate(rows_to_df(gate_rows)))
v = gate_comp.verdict.iloc[0]
check(f"p95>15s gate downgrades to conditional ({v})", v.startswith("🟡") and "p95" in v)
check("decision matrix non-empty", not dm.empty)

print("\nAggregate:\n", agg.to_string(index=False))
print("\nComposite:\n", comp[["task_class","model","pass@1","latency_p95","cost_per_ok_usd","composite","verdict"]].to_string(index=False))
print("\nDecision:\n", dm.to_string(index=False))
print("\nALL SELF-TESTS PASSED ✅")
