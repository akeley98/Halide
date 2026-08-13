`../experiment_scripts/init_dir.py` takes a `data_dir` and a label `harness_{T|F}_guide_{T|F}`
Initializes 4 possible experiments (with `dh_hl`, without `dh_hl`) x (with guide, without guide)

Makes new directory `{data_dir}/{label}_{n}`,
`n` lowest non-negative integer needed to not be a duplicate.
FAIL if `data_dir` isn't already a directory (typo protection).

assert that `dh_hl help detail` returns 0 (success) iff the guide is enabled.
Don't make the directory if the assert fails.
I'll take care of editing `dh_hl` manually for these experiments.

The new directory will contain:

* `original_generator.cpp`,
  which is `../../apps/local_laplacian/local_laplacian_generator.cpp`
  with the schedule and estimates removed.
  Then add a minimal schedule that just `compute_root` at a few critical spots.
  This is less honest than no schedule at all,
  but I don't want to go to bed and find the experiment made no progress
  because an agent is waiting for a fully-inlined program that will never terminate.

  Edit it to be compatible with the `new_golden` tool.
  (i.e. emit the algorithm hlpipe).
  Add a comment to never edit the algorithm above the serialization,
  and place all scheduling below the serialization.

  Note, store the modified generator C++ in a file in `../experiment_scripts/`
  and just copy it over in the `init_dir.py` script.
  I don't mean for the script to make all these edits at runtime.

* `original_generator_parameters.json`, which is just

        [
          {
          }
        ]

* `README.md`, with placeholder contents
  (I will embed the correct readme in the script later).

* `begin_experiment.py`, which when run, will initialize the contents below.
  CRUCIAL POINT, there's 2 levels of scripting here.
  `init_dir.py` generates `begin_experiment.py` which generates the below contents.
  `begin_experiment.py` doesn't have to be pretty.
  You can just embed giant strings in it if you'd like.


# Prompt

`begin_experiment.py` adds a `prompt.md` next to it.
I will write the prompt myself, parameterized on whether the harness/guide are enabled.
Please just add placeholder "add prompt" function for now
where it will be clear how I parameterize the text emitted for which case.
Most of my text will be common to at least 2 of the 4 possible prompts.


# Guide Contents

Only for the `harness_F_guide_T` case, `begin_experiment.py`
adds the following next to it.

* `guide.md`, captured output of `dh_hl prompt --guide-only`

* `detail/`, deep copy of `../detail/`

* `examples/`, deep copy of `../examples/`


# Build Helpers

Only for the no harness cases, `begin_experiment.py` adds files:

* `bin/` directory

* `build.py` which takes a `generator C++` filename and `generator parameters JSON` filename.

My intent is to create the bare minimum build environment
that still allows me to quietly log everything as if using the harness.
The `build.py` file builds using `ninja`

* Halide generator (rebuild if the supplied is out of date)

* Halide RunGenMain binary parameterized from `generator parameters JSON`
  which must contain a length 1 list with exactly one JSON object inside.
  Use the same targets (in particular, the profiler) as `dh_hl` would.
  This is rebuilt if `generator parameters JSON` or the Halide generator is out of date.
  NB you can lean on ninja's magical "rebuild if CLI command changed" feature.

* Halide stmt and conceptual stmt as well as part of the generator run.

* Logs new schedule node to the catalog with

        dh_hl experiment add_schedule_node -C catalog.dh_hl {generator C++} {generator parameters JSON}

  if either file is out of date (i.e. the generator was rerun).

* Can hard wire `~/Halide` as the Halide path.


# Catalog Directory

This is done regardless of the experiment type.
LAST step of `begin_experiment.py`.
It logs the timestamp of when the experiment begins.

* `dh_hl new_catalog -C catalog.dh_hl` with the `original_generator.cpp` and `original_generator_parameters.json` and whatever simple prompt.

* Disable the default problem.

* Add a new problem that configures RunGenMain to test with the 
  problem sizes that used to be set as the estimates in the
  original `local_laplacian_generator.cpp`.
  (NB you control the short name so you know the short ID of the problem).

* `dh_hl experiment begin {label}` 
