#include "compute_with_chain_levels_impl.hpp"
// Case B (boundary, well-behaved): f and g fuse at the same level (y). All three
// bodies are siblings in the shared loops. See
// compute_with_chain_levels_impl.hpp for the full account.
int main() {
    return main_impl(/*f_level=*/1 /*y*/, /*g_level=*/1 /*y*/);
}
