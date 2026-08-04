# bench_tools — profiler-campaign statistics & presentation (prototype)

Reference tooling for turning noisy `dh_hl` profiling into **confident**
schedule comparisons and readable campaign reports. Built to be **drop-in-able**
into the eventual harness: the tools read plain files, and the only harness-
specific seam is one loader + a manifest (see *Integration seam* below).

Everything here is a prototype living outside the catalog. The statistics live
here on purpose (Python iterates without rebuilding libHalide, and aggregating
across a campaign's many runs is only possible out here).

## Profiler prerequisites (the temporary side-branch changes)

These tools depend on fields added to the profiler on the side branch (all
`[TEMPORARY]`), consumed from each benchmark's JSON:

- pipeline: `profiler_version`, `wall_time_min`, `wall_time_max`,
  `wall_time_mean`, `wall_time_m2`, `wall_time_smallest[]`
- per-func: `recompute_ratio`, `parallel_loops`, `parallel_tasks` (plus the
  pre-existing counters)
- warnings: written to the path in `HL_PROFILER_JSON_TEMPORARY_WARNINGS` as
  **JSON Lines** (one `{"pipeline","warnings":[...]}` object per pipeline)

`wall_time_min` is the low-noise comparison stat (fastest of a record's ~hundreds
of runs). `mean`/`m2` are a Welford accumulator (`stddev = sqrt(m2/runs)`) kept
only as a noise diagnostic. `smallest[]` gives an outlier-robust error bar
(`tail_spread`).

## Two tool families

Both share the pairwise-significance core in `bench_analyze.py`.

### A. CSV path — quick timing-only ranking
- `bench_driver.py` — profiles schedules in **interleaved rounds** (shuffled
  order per round, to spread system drift), pulling `wall_time_*` from each
  schedule's latest benchmark JSON. Writes one CSV row per profile.
- `bench_analyze.py` — ranks into a frontier by median `min_ms`, with a
  **paired bootstrap** significance test on each adjacent boundary and an
  obsoletion gate. Also the home of the reusable primitives.

```bash
python3 bench_tools/bench_driver.py  --handle tmp.XX --rounds 8 --out bench_tools/results.csv
python3 bench_tools/bench_analyze.py bench_tools/results.csv           # ranked frontier
python3 bench_tools/bench_analyze.py bench_tools/results.csv --conf 99.9   # stricter ties
```

### B. JSON/manifest path — full per-func campaign presentation
- `campaign_run.py` — runs an interleaved campaign and writes the **drop-in file
  layout**: per invocation a raw profiler JSON + a warnings JSONL, plus a
  `manifest.json`.
- `campaign_lib.py` — shared loader (manifest → records), comparability gates,
  per-func pooling, warning dedup. Reuses `bench_analyze` primitives.
- `campaign_overview.py` — per-schedule runtime + variance + deduped warnings +
  hottest funcs (the "look at the slowest func" idiom, automated).
- `campaign_diff.py` — paired runtime verdict + per-func "what moved" between two
  schedules, aligned by func name (`+`/`-` for added/removed funcs).

```bash
python3 bench_tools/campaign_run.py      --handle tmp.XX --rounds 4 --out-dir bench_tools/campaign
python3 bench_tools/campaign_overview.py bench_tools/campaign/manifest.json
python3 bench_tools/campaign_diff.py     bench_tools/campaign/manifest.json opus_no_peek answer_key_wfix
```

> The schedule short-IDs to profile are **hardcoded** in the `SCHEDULES` lists of
> `bench_driver.py` and `campaign_run.py`; they are specific to this demo catalog.

## Manifest schema (`campaign/manifest.json`)

The manifest supplies the metadata the raw profiler/warnings files lack.

```json
{
  "campaign": "hist_demo",
  "machine": "Davids-MacBook-Pro",                   // stable human-readable FYI (see below)
  "records": [
    {
      "label": "opus_no_peek",                      // schedule identity (grouping)
      "round": 0,                                    // interleave batch = PAIRING key
      "hostname": "Mac.lan",                         // provenance only, unused (see below)
      "cpu_count": 11,                               // provenance only
      "profiler_json": "r00_opus_no_peek.profiler.json",   // raw HL_PROFILER_JSON_OUTPUT
      "warnings_json": "r00_opus_no_peek.warnings.jsonl"   // the smuggle JSONL (optional)
    }
  ]
}
```

Paths are relative to the manifest's directory. `profiler_json` may be either
`{"pipelines":[obj]}` (raw file) **or** a bare pipeline object (harness-stored);
the loader accepts both. `profiler_version` is read from the JSON, not the
manifest. `machine` is a **stable, network-independent** name
(`scutil --get LocalHostName` on macOS), shown as an FYI only — the per-record
`hostname` from the harness is `socket.gethostname()`, which drifts across
networks (`Davids-MacBook-Pro.local` ⇄ `Mac.lan`), so it is *not* used for
anything.

## Key concepts (why the numbers are trustworthy)

- **Rank on the robust min, not the mean.** `wall_time_min` has ~1% CV; the mean
  is outlier-contaminated (a 20 ms OS hiccup wrecks it).
- **Paired by round.** Significance always compares schedules measured in the
  *same* round, cancelling common-mode drift. Marginal-CI overlap is the wrong
  test (interleaving shrinks the real difference variance); use the paired-diff
  CI. See `paired_diff_ci`, `obsoletion_justified`, `possible_tie`.
- **Two orders.** A scalar (median `min_ms`) gives the *total order* for
  sorting/frontier; the paired significance is a *partial order* used to flag
  ties (`*`) and to **gate** obsoletion (only a significant win obsoletes).
- **Exact vs sampled columns.** Per-func `time%`/`threads` are sampled (shown
  `~`); `recompute_ratio`/`parallel_*`/mem/allocs are exact. Sampled per-func
  time is noisy per record but converges when pooled across the campaign.
- **Comparability gate.** Records with a mismatched `profiler_version` are
  discarded (with a note). **Cross-machine gating is out of scope** for the
  prototype (deciding whether two machines are "close enough" — GPU, memory
  bandwidth/capacity, power mode, ambient temperature — is genuinely hard, and
  untestable with one machine). A single machine per campaign is assumed;
  `machine` is carried as a stable human-readable FYI only.

## Integration seam (replacing the prototype with the harness)

To back these tools with the real catalog instead of files, replace **only**
`campaign_lib.load_campaign` (and the manifest) with a query that returns the
same per-record objects: `(label, round, profiler pipeline obj, warnings
list)`, gated to one `profiler_version` (a single machine is assumed;
cross-machine aggregation is out of scope).
`campaign_overview`, `campaign_diff`, `bench_analyze`, and all the primitives are
harness-agnostic and port unchanged.

The `round`/interleave-batch tag is the one thing the campaign **must** record on
each benchmark so paired differences are reconstructable; the temporary CSV/
manifest carry it explicitly.

## File listing

| file | role |
|------|------|
| `bench_driver.py`      | interleaved profiler → `results.csv` (timings only) |
| `bench_analyze.py`     | ranked frontier + paired bootstrap + primitives |
| `campaign_run.py`      | interleaved campaign → profiler JSON + warnings JSONL + manifest |
| `campaign_lib.py`      | shared loader / gates / per-func pooling (the seam) |
| `campaign_overview.py` | per-schedule runtime + variance + warnings + hottest funcs |
| `campaign_diff.py`     | paired verdict + per-func "what moved" |
| `results.csv`          | example CSV dataset |
| `campaign/`            | example manifest + per-record JSON files |
