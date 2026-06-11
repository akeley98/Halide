#!/usr/bin/env python3
"""
Tests for canonicalize.py -- these ARE the normalization policy.

Each test documents one decision about what the structural comparison forgives,
what it catches, and what makes it fail open. Run directly:

    python3 test_canonicalize.py

(also works under pytest, but needs no third-party dependency).

When the policy is intentionally tightened or loosened, update the test that
encodes that decision -- a failing test here should mean "the policy changed",
not "something broke silently".
"""

import os
import textwrap

from canonicalize import canonicalize, ParseError

HERE = os.path.dirname(os.path.abspath(__file__))


def _c(s: str) -> str:
    # Dedent + strip leading newline so tests can use triple-quoted blocks.
    return canonicalize(textwrap.dedent(s).lstrip("\n"))


# --------------------------------------------------------------------------
# FORGIVEN: things that are cosmetic and must NOT cause a mismatch.
# --------------------------------------------------------------------------

def test_func_names_are_positional():
    # Different function names, same structure -> identical canonical form.
    a = _c("""
        produce repeat_edge:
          for _0:
            repeat_edge(...) = ...
        consume repeat_edge:
          produce output:
            for x:
              output(...) = ...
    """)
    b = _c("""
        produce edge_clamp_wrapper:
          for whatever:
            edge_clamp_wrapper(...) = ...
        consume edge_clamp_wrapper:
          produce final:
            for q:
              final(...) = ...
    """)
    assert a == b, f"\n{a!r}\n!=\n{b!r}"


def test_loop_var_names_ignored():
    # Compound, counter-laden Halide names vs plain micro names -> MATCH.
    a = _c("""
        produce f:
          unrolled x.xi.v16:
            gpu_lane r15.r37:
              f(...) = ...
    """)
    b = _c("""
        produce f:
          unrolled i:
            gpu_lane j:
              f(...) = ...
    """)
    assert a == b


def test_constant_bounds_ignored():
    a = _c("""
        produce f:
          for x in [0, 255]:
            f(...) = ...
    """)
    b = _c("""
        produce f:
          for x:
            f(...) = ...
    """)
    assert a == b


def test_producer_consumer_share_func_id():
    # The same name in produce and consume must map to the same Fk, so that
    # producer<->consumer linkage is part of the compared structure.
    out = _c("""
        produce a:
          for x:
            a(...) = ...
        consume a:
          produce b:
            for x:
              b(...) = ...
    """)
    # 'a' first-seen -> F0 (both its produce and consume), 'b' -> F1.
    assert "produce F0" in out and "consume F0" in out
    assert "produce F1" in out
    assert "F2" not in out


# --------------------------------------------------------------------------
# CAUGHT: genuine, name-independent structural differences.
# --------------------------------------------------------------------------

def test_compute_level_change_caught():
    # Same funcs and loops, but the producer of g is hoisted out one level
    # (a compute_at/compute_root difference). Must differ.
    inner = _c("""
        produce f:
          for x:
            produce g:
              for x:
                g(...) = ...
            consume g:
              f(...) = ...
    """)
    hoisted = _c("""
        produce g:
          for x:
            g(...) = ...
        consume g:
          produce f:
            for x:
              f(...) = ...
    """)
    assert inner != hoisted


def test_loop_type_change_caught():
    serial = _c("""
        produce f:
          for x:
            f(...) = ...
    """)
    vectorized = _c("""
        produce f:
          vectorized x:
            f(...) = ...
    """)
    assert serial != vectorized


def test_device_api_change_caught():
    cpu = _c("""
        produce f:
          gpu_block x:
            f(...) = ...
    """)
    gpu = _c("""
        produce f:
          gpu_block x<Default_GPU>:
            f(...) = ...
    """)
    assert cpu != gpu


def test_extra_loop_caught():
    two = _c("""
        produce f:
          for y:
            for x:
              f(...) = ...
    """)
    three = _c("""
        produce f:
          for z:
            for y:
              for x:
                f(...) = ...
    """)
    assert two != three


# --------------------------------------------------------------------------
# KNOWN LIMITATION (documented, intentional): serial reorder is invisible.
# --------------------------------------------------------------------------

def test_serial_reorder_NOT_caught():
    # Both are plain 'for' loops, so dropping var names makes these identical.
    # This pins the documented limitation so it can't change unnoticed.
    xy = _c("""
        produce f:
          for x:
            for y:
              f(...) = ...
    """)
    yx = _c("""
        produce f:
          for y:
            for x:
              f(...) = ...
    """)
    assert xy == yx


# --------------------------------------------------------------------------
# FAIL OPEN: unrecognized input raises rather than guessing.
# --------------------------------------------------------------------------

def test_unrecognized_line_raises():
    try:
        canonicalize("produce f:\n  heap_allocate buf[123]:\n")
    except ParseError:
        return
    raise AssertionError("expected ParseError for unrecognized line")


def test_bad_indent_raises():
    try:
        canonicalize("produce f:\n   for x:\n")  # 3-space indent
    except ParseError:
        return
    raise AssertionError("expected ParseError for odd indentation")


# --------------------------------------------------------------------------
# GOLDEN: a full, complicated real nest (GPU + updates + clone_in + rfactor).
# If this output changes, the policy changed -- review and update deliberately.
# --------------------------------------------------------------------------

HIST_GOLDEN = """\
produce F0
  gpu_block<Default_GPU>
    produce F1
      unrolled
        gpu_lane<Default_GPU>
          vectorized
            def F1
      for
        produce F2
          gpu_lane<Default_GPU>
            def F2
        consume F2
          gpu_lane<Default_GPU>
            def F1
    consume F1
      unrolled
        gpu_lane<Default_GPU>
          vectorized
            def F0
consume F0
  produce F3
    gpu_block<Default_GPU>
      gpu_thread<Default_GPU>
        def F3
    gpu_block<Default_GPU>
      gpu_thread<Default_GPU>
        for
          def F3
  consume F3
    produce F4
      gpu_block<Default_GPU>
        gpu_thread<Default_GPU>
          def F4
      gpu_block<Default_GPU>
        gpu_thread<Default_GPU>
          for
            def F4
    consume F4
      produce F5
        gpu_block<Default_GPU>
          gpu_block<Default_GPU>
            produce F6
              gpu_thread<Default_GPU>
                gpu_thread<Default_GPU>
                  vectorized
                    def F6
            consume F6
              gpu_thread<Default_GPU>
                gpu_thread<Default_GPU>
                  produce F7
                    vectorized
                      def F7
                  consume F7
                    produce F8
                      vectorized
                        def F8
                    consume F8
                      produce F9
                        vectorized
                          def F9
                      consume F9
                        vectorized<Default_GPU>
                          unrolled
                            def F5
"""


def test_hist_golden():
    with open(os.path.join(HERE, "test_hist_loop_nest.txt")) as f:
        got = canonicalize(f.read())
    assert got == HIST_GOLDEN, f"\n--- got ---\n{got}\n--- want ---\n{HIST_GOLDEN}"


# --------------------------------------------------------------------------

def _main() -> int:
    tests = sorted(
        (name, fn) for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    )
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {name}: {e}")
            failures.append(name)
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
