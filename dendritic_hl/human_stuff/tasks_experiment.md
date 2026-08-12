Experiment entry points

    dh_hl experiment -C ... add_schedule_node {generator} {generator parameters JSON}
    dh_hl experiment -C ... begin {label}  # timestamp
    dh_hl experiment -C ... get_begin_timestamp
    dh_hl experiment -C ... get_begin_label  # fixed by human
    dh_hl experiment -C ... json_test_schedules

New script `experiment_scripts/profiler_session.py`:

Usage: `python3 profiler_session.py {catalog_path} {generator.cpp} {generator_parameters.json}`

Optional arguments:

* `--log-cli {file.json}`: log all CLI commands run as a JSON list of list of strings
  (outer list is list of CLI commands, inner list is argv of the command).
  Use some sort of helper to make this systematic.

* `--json-append {list.json}`: file must hold a JSON list.
  Append a 2-list `[catalog_path, session_full_id]` to the list.

Pseudocode:

    # Fail if any CLI commands fail
    _, catalog_path, generator_path, parameters_path = sys.argv

    # Make new sub-session for profiling
    tmp = parse_json(dh_hl list_termini -C catalog_path --json)
    assert len(tmp) == 1
    parent_session = tmp[0]

    # Set anchor node provided from outside
    anchor_id = dh_hl experiment add_schedule_node ... --ignore "anchor schedule for benchmark"

    # Parse for line starting with "Session handle:"
    # Find/add a dh_hl test for this and add a comment the experiment script relies on it.
    # Use this handle for all further dh_hl commands
    handle = dh_hl new_sub_session -C ... -s ... {some proposal name} {some text} {anchor_id}
    print(handle)

    # Init workspace and add "EXPERIMENT IGNORE:" negative commentary to seed schedule.

    # Do this early so we don't unexpectedly die at the end
    # Don't worry about leaking a failed session or such.
    if have session_list_path (--json-append arg):
        session_id = dh_hl session_full_id -s {handle}
        session_list = read_json(session_list_path)
        session_list.append([catalog_path, session_id])
        write_json(session_list_path, session_list)

    # Get nodes to profile
    dh_hl set_current_anchor ... {anchor_id}
    node_list = parse_json(dh_hl experiment json_major_schedule_nodes ... )

    # Profile in batches
    # Note, I decided to avoid the --profile N, N > 1 feature.
    for i in range(8):
        for node in node_list:
            dh_hl init_build --target {node} --other none
            dh_hl build --profile 1
