#pragma once

// Compatibility shim for the non-micro_halide (real Halide) build of the
// examples.
//
// The examples use `micro_halide_collapses(f, {vars...})` to annotate which
// loops Halide elides (point loops, extent 1). That annotation only matters to
// micro_halide, which cannot derive elision without bounds inference (see
// loopdoc.md and micro_halide.hpp). We deliberately do NOT touch real Halide's
// public interface, so for the Halide build we provide a do-nothing stub here.
//
// Include this right after "Halide.h" in the non-micro branch of an example's
// header block. It assumes Halide.h has already been included.

#include <initializer_list>

namespace Halide
{

// No-op: real Halide ignores the annotation; only the loop structure it
// actually emits is compared.
inline void micro_halide_collapses(const Func &, std::initializer_list<Var>)
{
}

} // namespace Halide
