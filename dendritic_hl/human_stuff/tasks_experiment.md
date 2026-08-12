Experiment entry points

    dh_hl experiment -C ... add_schedule_node {generator} {generator parameters JSON}
    dh_hl experiment -C ... begin {label}  # timestamp
    dh_hl experiment -C ... get_begin_timestamp
    dh_hl experiment -C ... get_label
    dh_hl experiment -C ... json_test_schedules

External script:

    Inputs: catalog path, baseline, json path
    dh_hl find terminus session
    dh_hl new_sub_session ...
    label, begin_timestamp = ...
    Print session handle
    node_list = json_major_schedule_nodes
    anchor = known schedule
    for i in range(8):
        for s in node_list:
            if s hash matches anchor:
                skip
            init_build --target s --anchor anchor --other none
            build --profile 1
    Give back session handle

External script 2:

    Input: JSON
    Plot each session best cost vs time, considering only major schedule nodes
