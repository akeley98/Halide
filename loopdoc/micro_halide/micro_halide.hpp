#pragma once

// Top-level micro_halide header.
//
// "Drop-in" tiny replacement for a subset of Halide, sufficient only
// to define simple functions, schedule them, and print their loop nest.
//
// micro_halide may skip implementing things only needed for convenience,
// like auto-uniquely-named variables.
//
// The loop nests are compared *structurally* (see ../canonicalize.py):
// loop variable names and constant bounds are normalized away, so
// micro_halide does not need to reproduce them. It only needs to match the
// produce/consume/store nesting, loop ordering, and loop type.

#include <atomic>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace micro_halide
{

// Edit this if needed.
class Var
{
    std::string _name;

  public:
    // With micro_halide, always name variables explicitly.
    Var(std::string n) : _name(std::move(n))
    {
    }

    const std::string &name() const
    {
        return _name;
    }
};

class Rvar
{
    // TODO implement this when update functions enter the picture.
};

class Expr
{
    // TODO implement expr with Halide operator overloading.
    // The print_loop_nest output does not actually show function internals.
    // So the Expr does not have to encode as much info as the real Halide::Expr.
    //
    // We only need to track enough information to deduce
    // producer/consumer relationships between Funcs.
};

struct Type
{
};

template <typename T>
Type type_of()
{
    return Type{};
}

// NOTE: there are many scheduling functions in-common to ImageParam, Stage, Func,
// so we may want to consider common helper code for the three classes.

class Stage
{
    // TODO implement this when update functions enter the picture.
};

class ImageParam
{
    std::string _name;
    int _dims;

  public:
    // With micro_halide, always name ImageParam explicitly.
    // Type is meaningless and just for compatibility with Halide.
    ImageParam(Type t, int d, std::string n) : _name(std::move(n)), _dims(d)
    {
    }
};

class Func
{
    std::string _name;

    explicit Func(std::string name): _name(std::move(name))
    {

    }

    void print_loop_nest()
    {
        throw std::runtime_error("TODO implement Func::print_loop_nest");
    }

    Stage update(int idx = 0)
    {
        throw std::runtime_error("TODO implement Func::update(int)");
    }

    Func &compute_at(const Func &f, const Var &var)
    {
        throw std::runtime_error("TODO implement Func::compute_at(Func, Var)");
    }

    Func &compute_at(const Func &f, const Rvar &var)
    {
        throw std::runtime_error("TODO implement Func::compute_at(Func, Rvar)");
    }
};


}
