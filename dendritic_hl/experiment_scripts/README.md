# Files

There are 4 possible experiment labels / configurations:

* `harness_F_guide_F`

* `harness_F_guide_T`

* `harness_T_guide_F`

* `harness_T_guide_T`

(Harness usage false/true; guide usage false/true).

Edit `../dendritic_hl_lib/allow_harness_flag.py` and `../dendritic_hl_lib/guide_flag.py`
to have the correct flag values for the experiment label.
Then install the customized `dh_hl` harness to `~/.local/bin` using `../install_snapshot.sh`.

Use `init_dir.py` with the label to create a directory seeded with the experiment files.
This checks that the correct `dh_hl` for the configuration was installed.

Start an agent in that new experiment dir.
Give `top_level_prompt.txt` to the agent.

After the experiment, run `profiler_session.py` in that directory.
This is what will create the "official" profiler run for the experiment.
Add a `--json-append ...` argument to add a record of that profiler run into some "profiler sessions" JSON file.

NOTE: error prone issues: the record will be added to the profiler sessions even if the profiling failed for some reason.

Use `plot.py` on that "profiler sessions" file to plot the results.

TODO split `plot.py` into "aggreate summary stats" and "plot stats"


# Halide Path

Unlike the `dh_hl` harness itself, the experiments hard-wire `~/Halide` as the "Halide path".

I recommend making `~/Halide/` a symlink to either

* your real Halide repo, when developing

* the stripped experiment Halide repo, when running experiments

WARNING: Claude Code does not like symlinks.
Don't move any directories already tied to Claude Code sessions
unless you want to lose all your sessions.


