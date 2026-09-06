#ifndef TEST_HARNESS
#define TEST_HARNESS

/* Just enough to run assertions and count them. A framework would add a
 * vendored file and a build step for about twenty checks, which is not a
 * trade worth making yet. When a test needs fixtures, setup and teardown, or
 * parameterised cases, replace this. */

#include <stdio.h>
#include <string.h>

extern int testChecks;
extern int testFailures;

#define CHECK(what, cond)                                                     \
    do {                                                                      \
        testChecks++;                                                         \
        if (cond) {                                                           \
            printf("  ok    %s\n", (what));                                   \
        } else {                                                              \
            printf("  FAIL  %s\n    at %s:%d\n", (what), __FILE__, __LINE__); \
            testFailures++;                                                   \
        }                                                                     \
    } while (0)

#define CHECK_EQ(what, got, want)                                             \
    do {                                                                      \
        long g = (long) (got), w = (long) (want);                             \
        testChecks++;                                                         \
        if (g == w) {                                                         \
            printf("  ok    %s\n", (what));                                   \
        } else {                                                              \
            printf("  FAIL  %s\n    expected %ld, got %ld at %s:%d\n",        \
                (what), w, g, __FILE__, __LINE__);                            \
            testFailures++;                                                   \
        }                                                                     \
    } while (0)

void suiteLedger(void);
void suiteLookup(void);

#endif
