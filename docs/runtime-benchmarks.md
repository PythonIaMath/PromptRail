# PromptRail runtime benchmarks

This document tracks the overhead of runtime correlation APIs. It is intentionally
focused on correlation overhead only, not client-side optimization, prompt tuning,
or provider latency changes.

## Reproducible command

Run from the repository root after installing the package in your preferred local
environment:

```bash
python -m pyperf timeit \
  --rigorous \
  --setup 'from promptrail import PromptRail, run, event; rail = PromptRail(project="benchmark")' \
  'with run("benchmark", runtime=rail): event("benchmark.event", {"n": 1})'
```

If `pyperf` is not installed:

```bash
python -m pip install pyperf
```

## Results

> **Update required:** The numbers below are placeholders. Replace them with the
> output from an actual benchmark run on the target machine before publishing or
> comparing releases.

| Date | Commit | Python | Platform | Command | Mean | Std dev | Notes |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| TODO: actual run date | TODO: commit SHA | TODO: Python version | TODO: OS/CPU | `python -m pyperf timeit ...` | TODO | TODO | Placeholder, update from actual run |

## Reporting guidance

- Record the exact commit SHA and Python version.
- Keep networked provider calls out of this benchmark.
- Run on an otherwise idle machine when possible.
- Add a note if debug logging, exporters, or tracing sinks are enabled.
- Compare like for like across releases.
