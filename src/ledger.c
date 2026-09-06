#include "ledger.h"

#include <errno.h>

#define LEDGER_SEPARATOR '\t'

/* FNV-1a. The table only ever stores hashes, so a collision would silently
 * skip a pair that was never compared. At 64 bits that needs on the order of
 * a billion pairs before it becomes plausible, and the all-pairs comparison
 * itself gives out long before then. */
static uint64_t
ledgerHash(const char *first, const char *second) {
    uint64_t h = 1469598103934665603ULL;

    for (const char *p = first; *p; ++p) {
        h ^= (unsigned char) *p;
        h *= 1099511628211ULL;
    }
    h ^= (unsigned char) LEDGER_SEPARATOR;
    h *= 1099511628211ULL;
    for (const char *p = second; *p; ++p) {
        h ^= (unsigned char) *p;
        h *= 1099511628211ULL;
    }

    /* 0 marks an empty slot, so it cannot also be a valid hash */
    return h ? h : 1;
}

/* The pair is unordered, so fix an order before hashing. Otherwise the same
 * two files hash differently depending on which one the loop reached first. */
static void
ledgerOrder(const char *first, const char *second,
    const char **low, const char **high) {
    if (strcmp(first, second) <= 0) {
        *low = first;
        *high = second;
    } else {
        *low = second;
        *high = first;
    }
}

static void
ledgerInsert(struct ledger *ledger, uint64_t hash) {
    size_t i = hash & (ledger->capacity - 1);

    while (ledger->slots[i]) {
        if (ledger->slots[i] == hash)
            return;
        i = (i + 1) & (ledger->capacity - 1);
    }
    ledger->slots[i] = hash;
    ledger->count++;
}

static int
ledgerContains(const struct ledger *ledger, uint64_t hash) {
    size_t i = hash & (ledger->capacity - 1);

    while (ledger->slots[i]) {
        if (ledger->slots[i] == hash)
            return 1;
        i = (i + 1) & (ledger->capacity - 1);
    }
    return 0;
}

/* Splits "pathA\tpathB" in place and inserts it. Lines without a separator are
 * skipped rather than treated as fatal, so a truncated last line left by a
 * killed run costs one recomputed pair instead of refusing to start. */
static void
ledgerInsertLine(struct ledger *ledger, char *line) {
    char *tab = strchr(line, LEDGER_SEPARATOR);
    const char *low = NULL, *high = NULL;

    if (!tab)
        return;
    *tab = '\0';
    /* Ordered on the way in as well as on the way out. Lines this program
       wrote are already in order, but a hand-edited one need not be, and a
       pair listed the other way round has to count as the same pair. */
    ledgerOrder(line, tab + 1, &low, &high);
    ledgerInsert(ledger, ledgerHash(low, high));
}

/* Creates the directories leading up to the ledger, so -s can name a place
 * that does not exist yet. Only the directories: the last component is the
 * ledger itself. A directory that is already there is success, not failure,
 * which is the whole difference between this and mkdir called once. */
static int
ledgerMakeParents(const char *path) {
    char work[2 * MAX_PATH_LENGTH];
    size_t length = strlen(path);

    if (length >= sizeof(work))
        return 0;
    memcpy(work, path, length + 1);

    /* From the second character, so the leading slash of an absolute path is
       not read as an empty directory name. */
    for (char *p = work + 1; *p; ++p) {
        if (*p != '/')
            continue;
        *p = '\0';
        if (mkdir(work, 0777) != 0 && errno != EEXIST)
            return 0;
        *p = '/';
    }
    return 1;
}

int
ledgerOpen(struct ledger *ledger, const char *path, size_t extra) {
    char line[2 * MAX_PATH_LENGTH + 2];
    size_t existing = 0;
    FILE *reader = NULL;

    /* Half full at worst, so linear probing stays short */
    ledger->capacity = 1024;
    while (ledger->capacity < 2 * (extra + 1))
        ledger->capacity *= 2;

    reader = fopen(path, "r");
    if (reader) {
        while (fgets(line, sizeof(line), reader)) {
            existing++;
            /* Grow before loading, for the same reason: no reallocation once
               the comparison threads are running. */
            if (2 * (existing + extra) > ledger->capacity)
                ledger->capacity *= 2;
        }
        rewind(reader);
    }

    ledger->slots = (uint64_t*) calloc(ledger->capacity, sizeof(uint64_t));
    LoggedAssert(ledger->slots, "Could not allocate the ledger table");
    ledger->count = 0;

    if (reader) {
        while (fgets(line, sizeof(line), reader)) {
            line[strcspn(line, "\r\n")] = '\0';
            ledgerInsertLine(ledger, line);
        }
        fclose(reader);
    }

    LoggedAssert(ledgerMakeParents(path),
        "Cannot create the directory for the ledger: %s", path);
    ledger->file = fopen(path, "a");
    LoggedAssert(ledger->file, "Cannot open ledger for appending: %s", path);
    /* Line buffered, so a pair reaches the file as soon as it is recorded and
       survives a kill. */
    setvbuf(ledger->file, NULL, _IOLBF, 0);

    slog_info(4, "Ledger %s: %zu pairs already compared", path, ledger->count);
    return 1;
}

int
ledgerHas(struct ledger *ledger, const char *first, const char *second) {
    const char *low = NULL, *high = NULL;
    int found = 0;

    if (!ledger->file)
        return 0;

    ledgerOrder(first, second, &low, &high);
    #pragma omp critical (ledger)
    {
        found = ledgerContains(ledger, ledgerHash(low, high));
    }
    return found;
}

void
ledgerRecord(struct ledger *ledger, const char *first, const char *second) {
    const char *low = NULL, *high = NULL;

    if (!ledger->file)
        return;

    ledgerOrder(first, second, &low, &high);
    #pragma omp critical (ledger)
    {
        ledgerInsert(ledger, ledgerHash(low, high));
        fprintf(ledger->file, "%s%c%s\n", low, LEDGER_SEPARATOR, high);
    }
}

void
ledgerClose(struct ledger *ledger) {
    if (ledger->file) {
        fclose(ledger->file);
        ledger->file = NULL;
    }
    free(ledger->slots);
    ledger->slots = NULL;
    ledger->capacity = 0;
    ledger->count = 0;
}
