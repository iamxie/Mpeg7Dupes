#include "harness.h"

int testChecks = 0;
int testFailures = 0;

int
main(void) {
    printf("Ledger\n");
    suiteLedger();
    printf("\nLookup\n");
    suiteLookup();

    printf("\n%d checks", testChecks);
    if (testFailures)
        printf(", %d failed\n", testFailures);
    else
        printf(", all passed\n");
    return testFailures ? 1 : 0;
}
