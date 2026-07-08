#include "clone_in_but_inlined.hpp"
int main()
{
    // clone_in_both=1 + no_c3 means the original (non-cloned) common func should vanish entirely.
    return clone_in_but_inlined(1, 1, 0, 1);
}
