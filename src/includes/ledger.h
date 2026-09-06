#ifndef LEDGER
#define LEDGER

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "customAssert.h"
#include "slog_compat.h"
#include "utils.h"

/* A record of which pairs have already been compared, so an interrupted run
 * can continue instead of starting over.
 *
 * One line per pair, appended as soon as that pair finishes, whether or not it
 * matched. Recording only the matches would be useless: a pair missing from
 * the output could mean "not compared yet" or "compared, nothing found", and
 * resuming cannot tell those apart.
 *
 * Lines are "pathA\tpathB", the two paths in ascending order so that a pair is
 * written the same way whichever side it is reached from. Lookups go through a
 * 64-bit hash of that line, held in an open-addressed table; the paths stay in
 * the file so it can be read and edited by hand.
 */
struct ledger {
    FILE *file;          /* open for append, NULL when -s was not given */
    uint64_t *slots;     /* open addressing, 0 marks an empty slot */
    size_t capacity;     /* always a power of two */
    size_t count;
};

/* Reads an existing ledger and opens it for appending. Missing files are
 * created, so the first run and a resume take the same command line. `extra`
 * is how many pairs this run may add, used to size the table up front so it
 * never has to grow while threads are running. */
int
ledgerOpen(struct ledger *ledger, const char *path, size_t extra);

/* Whether this pair has already been compared. False when no ledger is in
 * use, which makes every call site read as "compare unless already done". */
int
ledgerHas(struct ledger *ledger, const char *first, const char *second);

/* Marks a pair as compared and appends it to the file. */
void
ledgerRecord(struct ledger *ledger, const char *first, const char *second);

void
ledgerClose(struct ledger *ledger);

#endif
