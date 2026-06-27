# Goal: probe how broken bounds inference is for Halide compute_with

Set up adversarial cases for the real Halide:

    Func parent("parent");
    Func child_1("child_1");
    Func child_2("child_2");
    Func output("output");
    Var x("x"), y("y");

    // No simultaneous-parent-and-child yet.
    parent(x) = x;
    child_1(x) = x;
    child_2(x) = x;
    // Whoa: child_2(y) causes needed bounds of child_2 to be different than child_1 and parent.
    output(x, y) = parent(x) + child_1(x) + child_2(y);
    parent.compute_root();
    child_1.compute_root();
    child_2.compute_root();

    {
        // Case 1. child_1, child_2 are both children of parent.
        child_1.compute_with(parent, x);
        child_2.compute_with(parent, x);

        // Question: when child_1 and parent are fused, no guarding is required.
        // When child_2 gets fused to parent, guarding has to be added to child_2 and parent.
        // BUT ALSO, child_1 has to have guarding added, since it's relying on parent.
        //
        // Also swap orderings of the functions, names, etc. to make sure this doesn't just seem to work due to realization order being lucky.
        //
        // Suggestion: add tracing to Halide to know every time loop adjust + if insertion happens.
        // Look at .stmt output to see if `if` stmts show up in the right places.
        //
        // Please don't put this in /tmp; put stuff somewhere temporarily in this repo so I can see it.
    }
    {
        // Case 2. Chaining, part 1.
        child_1.compute_with(parent, x);
        child_2.compute_with(child_1, x);
        // child_2 bounds-change has to propagate upwards to both child_1 and parent. 
    }
    {
        // Case 2. Chaining, part 2.
        child_2.compute_with(parent, x);
        child_1.compute_with(child_2, x);
        // child_2 bounds-change has to propagate "upwards" to parent and "downwards" to child_1.
    }
    // Also possibly have more loop levels than just `x`, in case that makes a difference.
