/* The ledger decides which pairs get skipped, so a fault here loses work
 * silently: the pair never appears in the output and nothing says why.
 * tests/ledger.sh covers the behaviour reachable from the command line. What
 * is left, and what this file is for, is the table itself. */

#include "harness.h"
#include "ledger.h"

#include <stdlib.h>
#include <unistd.h>

static char *
tempPath(char *buffer, size_t size) {
    snprintf(buffer, size, "/tmp/ledger_test_%d_%p", (int) getpid(),
        (void*) buffer);
    return buffer;
}

void
suiteLedger(void) {
    char path[128];
    struct ledger ledger = {0};

    /* A pair is unordered, so the two directions have to land on one entry.
       Getting this wrong only shows up on a ledger someone edited by hand,
       which is why it needs a check of its own. */
    tempPath(path, sizeof(path));
    ledgerOpen(&ledger, path, 16);
    ledgerRecord(&ledger, "b.bin", "a.bin");
    CHECK("recorded low to high is found", ledgerHas(&ledger, "a.bin", "b.bin"));
    CHECK("recorded high to low is found", ledgerHas(&ledger, "b.bin", "a.bin"));
    CHECK("an unrecorded pair is not found",
        !ledgerHas(&ledger, "a.bin", "c.bin"));

    /* Recording the same pair again must not add a second entry, or the table
       fills faster than it was sized for. */
    ledgerRecord(&ledger, "a.bin", "b.bin");
    CHECK_EQ("a repeated pair is stored once", ledger.count, 1);
    ledgerClose(&ledger);
    unlink(path);

    /* ledgerInsert probes until it finds an empty slot, so a table that ever
       became full would spin forever. Fill it with the number of pairs it was
       sized for and confirm every one is still findable and the table is at
       most half occupied. */
    {
        const int pairs = 500;
        char first[32], second[32];
        int missing = 0;

        tempPath(path, sizeof(path));
        ledgerOpen(&ledger, path, (size_t) pairs);
        for (int i = 0; i < pairs; ++i) {
            snprintf(first, sizeof(first), "left%04d.bin", i);
            snprintf(second, sizeof(second), "right%04d.bin", i);
            ledgerRecord(&ledger, first, second);
        }
        for (int i = 0; i < pairs; ++i) {
            snprintf(first, sizeof(first), "left%04d.bin", i);
            snprintf(second, sizeof(second), "right%04d.bin", i);
            if (!ledgerHas(&ledger, first, second))
                missing++;
        }
        CHECK_EQ("every recorded pair is found again", missing, 0);
        CHECK_EQ("the table holds them all", ledger.count, pairs);
        CHECK("the table stays at most half full",
            ledger.count * 2 <= ledger.capacity);
        ledgerClose(&ledger);
        unlink(path);
    }

    /* Reopening has to see what the previous run wrote, and has to survive the
       partial last line a killed run leaves behind. */
    {
        FILE *f = NULL;

        tempPath(path, sizeof(path));
        ledgerOpen(&ledger, path, 16);
        ledgerRecord(&ledger, "a.bin", "b.bin");
        ledgerRecord(&ledger, "c.bin", "d.bin");
        ledgerClose(&ledger);

        f = fopen(path, "a");
        fprintf(f, "e.bin");   /* no separator, no newline */
        fclose(f);

        ledgerOpen(&ledger, path, 16);
        CHECK_EQ("reopening restores the recorded pairs", ledger.count, 2);
        CHECK("a pair from the previous run is still known",
            ledgerHas(&ledger, "a.bin", "b.bin"));
        ledgerClose(&ledger);
        unlink(path);
    }

    /* Without -s there is no ledger, and every pair has to look uncompared. */
    {
        struct ledger disabled = {0};
        CHECK("with no ledger nothing counts as done",
            !ledgerHas(&disabled, "a.bin", "b.bin"));
        ledgerRecord(&disabled, "a.bin", "b.bin");
        CHECK("and recording without one is a no-op", disabled.count == 0);
    }
}
