#include "compute_with_chain_levels_impl.hpp"
// Case C (well-behaved): f's fuse level (y) is BELOW g's fuse level (z). f shares
// g's real y loop. See compute_with_chain_levels_impl.hpp for the full account.
int main() {
    return main_impl(/*f_level=*/1 /*y*/, /*g_level=*/0 /*z*/);
}
