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
 *
 * Lines starting with # are the ledger's record of what it was written for:
 * a "#run" line with the build, the binary's digest and every setting that
 * changes the output, and one "#input" line per input file with its size and
 * content digest. A resume is checked against them before a single pair is
 * reused: another build, other settings or a changed input are refused, since
 * the pairs on record would carry the old results. A ledger with pairs but no
 * record, from build 4 or earlier or written by hand, is reused with a
 * warning and given a record then.
 */

struct ledgerRun {
    char version[64];    /* MPEG7DUPES_VERSION_STRING */
    char binary[24];     /* FNV-1a of the executable's bytes, hex, or unknown */
    char params[256];    /* every setting that changes the output */
};

struct ledgerInput {
    uint64_t pathHash;
    uint64_t contentHash;
    long size;
};

struct ledger {
    FILE *file;          /* open for append, NULL when -s was not given */
    uint64_t *slots;     /* open addressing, 0 marks an empty slot */
    size_t capacity;     /* always a power of two */
    size_t count;
    struct ledgerRun run;
    int hasRun;          /* a #run line was read or written */
    struct ledgerInput *inputs;
    size_t inputCount, inputCapacity;
};

/* Tab, CR/LF and a leading # cannot be encoded by this ledger format. */
int ledgerPathUsable(const char *path);

/* Locks an existing ledger exclusively, discards an unfinished final line,
 * validates complete records, and keeps the descriptor locked for appending. Missing files are
 * created, so the first run and a resume take the same command line. `extra`
 * is how many pairs this run may add, used to size the table up front so it
 * never has to grow while threads are running. `run` describes this run; a
 * ledger written for another one is refused: the return is 0 and `why` says
 * what differs. */
int
ledgerOpen(struct ledger *ledger, const char *path, size_t extra,
    const struct ledgerRun *run, char *why, size_t whySize);

/* Checks one input file against the ledger's record of it, recording it when
 * it is new. Returns 0 with `why` filled when the file changed since it was
 * recorded or cannot be read: the pairs it took part in are stale. */
int
ledgerNoteInput(struct ledger *ledger, const char *path, char *why,
    size_t whySize);

/* Whether this pair has already been compared. False when no ledger is in
 * use, which makes every call site read as "compare unless already done". */
int
ledgerHas(struct ledger *ledger, const char *first, const char *second);

/* Marks a pair as compared and appends it to the file. Returns 0 when the
 * line did not reach the file, in which case the pair is not marked either. */
int
ledgerRecord(struct ledger *ledger, const char *first, const char *second);

/* Returns 0 when the file could not be closed cleanly. */
int
ledgerClose(struct ledger *ledger);

/* FNV-1a of a whole file, never 0; 0 means the file could not be read. The
 * size read is stored through `size`. Public so main can digest the binary. */
uint64_t
ledgerHashFile(const char *path, long *size);

#endif
