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

Use `plot_generate_data.py` and `plot_render.py` 


# Halide Path

The experiment copies a snapshot of the built Halide repo into the new
experiment directory.  This needs to be a build of the `Halide` repo
(this tree), gzip'd into a `Halide.tgz` file here. It needs to be set
up such that `Halide/build` is the CMake build directory when unzipped.

For that tgz, delete `.git`, `apps`, `dendritic_hl`,
and `loopdoc` (if that still exists).



