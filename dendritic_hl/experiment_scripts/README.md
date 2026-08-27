# Files

There are 4 possible experiment labels / configurations:

* `harness_F_guide_F`

* `harness_F_guide_T`

* `harness_T_guide_F`

* `harness_T_guide_T`

(Harness usage false/true; guide usage false/true).

Pick an experiment directory.
Run `./run_headless.py {experiment dir} {label}` to run one experiment.
Multiple experiments ultimately aggregate into `{experiment dir}/sessions.json`.
Can also use the helper `./run_headless_4.py {experiment dir} {count}`.

Use `plot_generate_data.py` and `plot_render.py`.


# Halide Path

The experiment copies a snapshot of the built Halide repo into the new
experiment directory.  This needs to be a build of the `Halide` repo
(this tree), gzip'd into a `Halide.tgz` file here. It needs to be set
up such that `Halide/build` is the CMake build directory when unzipped.

For that tgz, delete `.git`, `apps`, `dendritic_hl`,
and `loopdoc` (if that still exists).


# Templates

The experiment needs to be seeded with a template.
You only need this information if you want to test out a new Halide pipeline.
Otherwise you can use `--tempate-path [this dir] --app local_laplacian`.

* Experiment generator: C++ generator with no schedule, or a trivial
  `compute_root` schedule (so the agent doesn't start up by profiling
  an inline-explosion schedule that will never terminate).

* Answer key: C++ generator

* Answer key parameters JSON: usually just `[{}]`
  (use all default `GeneratorParam` values)

* argv: CLI args for RunGenMain.
  Use `argv[0]="<RunGenMain>"`.
  TODO medley of problems and external runners.
  For now 1 problem invites overfitting to a single problem size.
  I explicitly authorized this because "don't overfit" is vague
  and could introduce noise by different agents making different judgment calls.

The experiment generator should have strict separation of algorithm and schedule,
with `// TODO: add scheduling (edit below me in this scope)` marking where the
schedule starts. Edit so that

1. There's this code block separating the algorithm above and schedule below.

        if (const char* hlpipe_path = getenv("DENDRITIC_HL_ALGORITHM_HLPIPE")) {
          serialize_pipeline(
              Pipeline(std::vector<Func>{outputs...}),
              std::string(hlpipe_path));
        }

        // TODO: add scheduling (edit below me in this scope)

2. If there's helper `Func` factory functions, remove their schedule,
   and refactor them into structs that hold `Func` members,
   defines the algorithm in the constructor,
   and provides a stub `schedule` function.
   This way the agent can access all Funcs to schedule.

        struct MyHelper
        {
            Var x("x")...
            Func f("f")...

            MyHelper(Func g)
            {
                f(x) = g(x)...
            }

            // TODO: add scheduling (edit below me in this scope)
            void schedule() {
            }
        }

3. Check that all functions are named in the profiler report.
   You can smoke test with `run_headless.py --begin-end --dir-only`;
   these flags prevent the agent from actually starting up.
   With `harness_F_guide_F`, run `build.py` and `runner.py`
   on the initial Halide code in the created experiment directory
   and see if all funcs in the profiler table are named.
   
   NB due to hash collisions, Halide may append `$1`,
   `$2`... disambiguation for functions that in fact don't have name collisions.

4. Recommended: replace all `GeneratorParam` that affect the *algorithm*
   (i.e. not just scheduling knobs) with constants.
