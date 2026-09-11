/* The option value parsers. atoi and atof used to turn "abc" into 0 and
 * "0.5x" into 0.5 without a word, and a threshold that quietly became 0 is
 * what made -b a no-op for as long as it existed. Every case here is a value
 * the old parsing accepted, or the boundary of one. */

#include "harness.h"
#include "ArgumentParsing.h"

void
suiteArgs(void) {
    long n = -1;
    double r = -1.0;

    CHECK("a whole number parses", parseIntOption("290", 0, 100000, &n) && n == 290);
    CHECK("zero parses", parseIntOption("0", 0, 10, &n) && n == 0);
    CHECK("the top of the range parses", parseIntOption("100", 0, 100, &n) && n == 100);
    CHECK("trailing junk is refused", !parseIntOption("12abc", 0, 100, &n));
    CHECK("a fraction is refused where a whole number is due", !parseIntOption("1.5", 0, 100, &n));
    CHECK("exponent form is refused there too", !parseIntOption("1e3", 0, 10000, &n));
    CHECK("an empty value is refused", !parseIntOption("", 0, 100, &n));
    CHECK("a leading space is refused", !parseIntOption(" 5", 0, 100, &n));
    CHECK("a negative count is refused", !parseIntOption("-1", 0, 100, &n));
    CHECK("a value above the range is refused", !parseIntOption("101", 0, 100, &n));
    CHECK("below a range that starts at 1 is refused", !parseIntOption("0", 1, 100, &n));
    CHECK("letters are refused", !parseIntOption("abc", 0, 100, &n));
    CHECK_EQ("a refused value leaves the output as it was", n, 100);

    CHECK("a ratio parses", parseRatioOption("0.1", &r) && r == 0.1);
    CHECK("zero is allowed", parseRatioOption("0", &r) && r == 0.0);
    CHECK("one is allowed", parseRatioOption("1", &r) && r == 1.0);
    CHECK("exponent form parses", parseRatioOption("5e-1", &r) && r == 0.5);
    CHECK("above 1 is refused", !parseRatioOption("1.5", &r));
    CHECK("below 0 is refused", !parseRatioOption("-0.1", &r));
    CHECK("nan is refused", !parseRatioOption("nan", &r));
    CHECK("inf is refused", !parseRatioOption("inf", &r));
    CHECK("trailing junk is refused", !parseRatioOption("0.5x", &r));
    CHECK("letters are refused", !parseRatioOption("abc", &r));
    CHECK("an empty value is refused", !parseRatioOption("", &r));
    CHECK("a refused ratio leaves the output as it was", r == 0.5);
}
