#include "compute_with_chain_levels_impl.hpp"
// Case A (THE SURPRISE): f's fuse level (z) is ABOVE g's fuse level (y).
// f hits g's collapsed z dummy, and f's own y loop re-materializes inside the
// shared fused.y. See compute_with_chain_levels_impl.hpp for the full account.
int main() {
    return main_impl(/*f_level=*/0 /*z*/, /*g_level=*/1 /*y*/);
}
