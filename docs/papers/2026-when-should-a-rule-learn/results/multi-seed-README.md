# Multi-seed measurements — pending

The initial attempt at 5-seed × 4-benchmark training-order variation
used `PYTHONHASHSEED` which only affects dict iteration order, not
the benchmark runner's training stream. All 5 seeds per benchmark
produced identical results (a sanity check, not a real multi-seed
validation). Those 17 MB of duplicate JSONL files were removed from
this directory.

**Correct multi-seed runs** use the `shuffle_seed=N` keyword in
`postrule.research.run_benchmark_experiment`, which actually shuffles
the training stream before streaming it through the switch. The
kwarg is shipped as of v0.2.0.

**To regenerate:**

```bash
for SEED in 1 2 3 4 5; do
  for BENCH in atis hwu64 banking77 clinc150; do
    # TODO: wire shuffle_seed through the CLI; currently requires
    # Python-level invocation of run_benchmark_experiment.
    ../.venv/bin/python -c "
from postrule.benchmarks import load_${BENCH}
from postrule.benchmarks.rules import build_reference_rule
from postrule.ml import SklearnTextHead
from postrule.research import run_benchmark_experiment
import json, dataclasses

ds = load_${BENCH}()
rule = build_reference_rule(ds.train).as_callable()
head = SklearnTextHead(min_outcomes=100)
cps = run_benchmark_experiment(
    train=ds.train, test=ds.test, rule=rule, ml_head=head,
    checkpoint_every=500, shuffle_seed=${SEED},
)
for cp in cps:
    print(json.dumps({'kind': 'checkpoint', **dataclasses.asdict(cp)}))
" > ${BENCH}_seed${SEED}.jsonl
  done
done
```

Once rerun, the paper's §5 multi-seed validation table should
populate with real mean ± stddev across seeds.
