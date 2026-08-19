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

Use `plot_generate_data.py` and `plot_render.py` 


# Halide Path

The experiment copies a snapshot of the built Halide repo into the new
experiment directory.  This needs to be a build of the `Halide` repo
(this tree), gzip'd into a `Halide.tgz` file here. It needs to be set
up such that `Halide/build` is the CMake build directory when unzipped.

For that tgz, delete `.git`, `apps`, `dendritic_hl`,
and `loopdoc` (if that still exists).



