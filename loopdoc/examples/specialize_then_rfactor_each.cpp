#include "specialize_then_rfactor_each_impl.hpp"
// Member 1 (no tile): branch intermediate split, fallback intermediate plain.
// RED scaffold — see specialize_then_rfactor_each_impl.hpp (§6 base-before-
// specialization visitation order; micro currently orders the two g_intm's the
// other way).
int main() { return main_impl(/*tile=*/false); }
