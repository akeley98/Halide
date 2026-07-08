#include "in_but_inlined.hpp"
int main()
{
    // in_both=1 + no_c3: like the clone no_c3 case, but with a WRAPPER the original
    // `common` still survives (the wrapper reads it), so it does not vanish here.
    return in_but_inlined(1, 1, 0, 1);
}
