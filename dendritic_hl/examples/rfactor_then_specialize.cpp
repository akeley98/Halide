#include "rfactor_then_specialize_impl.hpp"
// Member 1 (no reduction tiling): rfactor the whole r.y, then specialize. Both
// branches of the specialize read the shared, scheduled intermediate. See
// rfactor_then_specialize_impl.hpp.
int main() { return main_impl(/*tile_reduction=*/false); }
