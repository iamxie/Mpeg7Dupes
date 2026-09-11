#include "ledger.h"

#include <errno.h>
#include <sys/stat.h>
#include <inttypes.h>

#define LEDGER_SEPARATOR '\t'
#define FNV_OFFSET 1469598103934665603ULL
#define FNV_PRIME 1099511628211ULL

/* FNV-1a. The table only ever stores hashes, so a collision would silently
 * skip a pair that was never compared. At 64 bits that needs on the order of
 * a billion pairs before it becomes plausible, and the all-pairs comparison
 * itself gives out long before then. */
static uint64_t
ledgerHash(const char *first, const char *second) {
    uint64_t h = FNV_OFFSET;

    for (const char *p = first; *p; ++p) {
        h ^= (unsigned char) *p;
        h *= FNV_PRIME;
    }
    h ^= (unsigned char) LEDGER_SEPARATOR;
    h *= FNV_PRIME;
    for (const char *p = second; *p; ++p) {
        h ^= (unsigned char) *p;
        h *= FNV_PRIME;
    }

    /* 0 marks an empty slot, so it cannot also be a valid hash */
    return h ? h : 1;
}

static uint64_t
ledgerHashString(const char *text) {
    uint64_t h = FNV_OFFSET;

    for (const char *p = text; *p; ++p) {
        h ^= (unsigned char) *p;
        h *= FNV_PRIME;
    }
    return h ? h : 1;
}

uint64_t
ledgerHashFile(const char *path, long *size) {
    uint64_t h = FNV_OFFSET;
    unsigned char buffer[1 << 16];
    size_t got = 0;
    FILE *f = fopen(path, "rb");

    *size = 0;
    if (!f)
        return 0;
    while ((got = fread(buffer, 1, sizeof(buffer), f)) > 0) {
        for (size_t i = 0; i < got; ++i) {
            h ^= buffer[i];
            h *= FNV_PRIME;
        }
        *size += (long) got;
    }
    if (ferror(f)) {
        fclose(f);
        return 0;
    }
    fclose(f);
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

static struct ledgerInput *
ledgerFindInput(struct ledger *ledger, uint64_t pathHash) {
    for (size_t i = 0; i < ledger->inputCount; ++i)
        if (ledger->inputs[i].pathHash == pathHash)
            return &ledger->inputs[i];
    return NULL;
}

static void
ledgerAddInput(struct ledger *ledger, uint64_t pathHash, long size,
    uint64_t contentHash) {
    if (ledger->inputCount == ledger->inputCapacity) {
        size_t bigger = ledger->inputCapacity ? 2 * ledger->inputCapacity : 64;
        struct ledgerInput *grown = (struct ledgerInput*) realloc(
            ledger->inputs, bigger * sizeof(struct ledgerInput));
        LoggedAssert(grown, "Could not allocate the ledger's input table");
        ledger->inputs = grown;
        ledger->inputCapacity = bigger;
    }
    ledger->inputs[ledger->inputCount].pathHash = pathHash;
    ledger->inputs[ledger->inputCount].size = size;
    ledger->inputs[ledger->inputCount].contentHash = contentHash;
    ledger->inputCount++;
}

/* Copies the value of "key=value" into out, or leaves it empty. */
static void
ledgerField(const char *field, const char *key, char *out, size_t size) {
    size_t length = strlen(key);

    if (strncmp(field, key, length) == 0 && field[length] == '=')
        snprintf(out, size, "%s", field + length + 1);
}

/* One line of the ledger's record. Anything it does not understand is a
 * comment. */
static void
ledgerReadRecord(struct ledger *ledger, char *line) {
    char *fields[8] = {0};
    int count = 0;

    for (char *p = strtok(line, "\t"); p && count < 8; p = strtok(NULL, "\t"))
        fields[count++] = p;
    if (count == 0)
        return;
    if (strcmp(fields[0], "#run") == 0) {
        for (int i = 1; i < count; ++i) {
            ledgerField(fields[i], "version", ledger->run.version,
                sizeof(ledger->run.version));
            ledgerField(fields[i], "binary", ledger->run.binary,
                sizeof(ledger->run.binary));
            ledgerField(fields[i], "params", ledger->run.params,
                sizeof(ledger->run.params));
        }
        ledger->hasRun = 1;
    } else if (strcmp(fields[0], "#input") == 0 && count >= 4) {
        long size = strtol(fields[2], NULL, 10);
        uint64_t contentHash = strtoull(fields[3], NULL, 16);
        ledgerAddInput(ledger, ledgerHashString(fields[1]), size, contentHash);
    }
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

static int
ledgerWriteRun(struct ledger *ledger, int fresh) {
    if (fresh && fprintf(ledger->file,
            "# mpeg7dupes ledger: one compared pair per line, pathA<TAB>pathB. "
            "The # lines record the build, the settings and each input; a "
            "resume with any of them changed is refused.\n") < 0)
        return 0;
    if (fprintf(ledger->file, "#run\tversion=%s\tbinary=%s\tparams=%s\n",
            ledger->run.version, ledger->run.binary, ledger->run.params) < 0)
        return 0;
    return fflush(ledger->file) == 0;
}

int
ledgerOpen(struct ledger *ledger, const char *path, size_t extra,
    const struct ledgerRun *run, char *why, size_t whySize) {
    char line[2 * MAX_PATH_LENGTH + 2];
    size_t existing = 0;
    FILE *reader = NULL;

    ledger->hasRun = 0;
    ledger->inputs = NULL;
    ledger->inputCount = ledger->inputCapacity = 0;
    memset(&ledger->run, 0, sizeof(ledger->run));

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
            if (line[0] == '#')
                ledgerReadRecord(ledger, line);
            else
                ledgerInsertLine(ledger, line);
        }
        fclose(reader);
    }

    if (ledger->hasRun) {
        /* The same run, or not the same ledger. */
        if (strcmp(ledger->run.version, run->version) != 0
                || strcmp(ledger->run.binary, run->binary) != 0
                || strcmp(ledger->run.params, run->params) != 0) {
            /* Which of the two changed decides the way back: a build is
               told apart by version or digest, the same build with other
               settings is the other case. Editing the #run line by hand
               used to be offered as a third way and is not any more: rows
               from two builds are not comparable, a build can change what
               a column means, so the choice is the old build or a rerun. */
            const char *what = (strcmp(ledger->run.version, run->version) != 0
                    || strcmp(ledger->run.binary, run->binary) != 0)
                ? "build" : "settings";
            snprintf(why, whySize,
                "Cannot resume from ledger %s: it was written by %s (binary "
                "%s) with %s, and this run is %s (binary %s) with %s. The "
                "%zu pairs recorded there, and the output written beside "
                "them, were made with the other %s and cannot be continued "
                "with this one: the rows would not be comparable. Either go "
                "back to the %s they were made with, or start over: move the "
                "ledger and its output away and compare the whole list "
                "again.",
                path, ledger->run.version, ledger->run.binary,
                ledger->run.params, run->version, run->binary, run->params,
                ledger->count, what, what);
            free(ledger->slots);
            ledger->slots = NULL;
            free(ledger->inputs);
            ledger->inputs = NULL;
            return 0;
        }
    } else {
        ledger->run = *run;
    }

    LoggedAssert(ledgerMakeParents(path),
        "Cannot create the directory for the ledger: %s", path);
    ledger->file = fopen(path, "a");
    LoggedAssert(ledger->file, "Cannot open ledger for appending: %s", path);
    /* Line buffered, so a pair reaches the file as soon as it is recorded and
       survives a kill. */
    setvbuf(ledger->file, NULL, _IOLBF, 0);

    if (!ledger->hasRun) {
        if (ledger->count)
            slog_warn(3, "Ledger %s has no run record: it was written by "
                "build 4 or earlier, or by hand. Its %zu pairs are reused "
                "unchecked, and it is given a record now.", path,
                ledger->count);
        if (!ledgerWriteRun(ledger, existing == 0)) {
            snprintf(why, whySize, "Cannot write to the ledger %s: %s", path,
                strerror(errno));
            return 0;
        }
        ledger->hasRun = 1;
    }

    slog_info(4, "Ledger %s: %zu pairs already compared", path, ledger->count);
    return 1;
}

int
ledgerNoteInput(struct ledger *ledger, const char *path, char *why,
    size_t whySize) {
    uint64_t pathHash = 0, contentHash = 0;
    long size = 0;
    struct ledgerInput *known = NULL;

    if (!ledger->file)
        return 1;

    pathHash = ledgerHashString(path);
    contentHash = ledgerHashFile(path, &size);
    if (!contentHash) {
        snprintf(why, whySize, "Cannot read input %s: %s", path,
            strerror(errno));
        return 0;
    }
    known = ledgerFindInput(ledger, pathHash);
    if (known) {
        if (known->size != size || known->contentHash != contentHash) {
            snprintf(why, whySize,
                "Input %s changed since the ledger recorded it (%ld bytes "
                "then, %ld now%s). The pairs it took part in are stale. "
                "Restore the file, or start a new ledger.",
                path, known->size, size,
                known->size == size ? ", same size, other content" : "");
            return 0;
        }
        return 1;
    }
    if (fprintf(ledger->file, "#input\t%s\t%ld\t%016" PRIx64 "\n", path, size,
            contentHash) < 0 || fflush(ledger->file) != 0) {
        snprintf(why, whySize, "Cannot write to the ledger: %s",
            strerror(errno));
        return 0;
    }
    ledgerAddInput(ledger, pathHash, size, contentHash);
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

int
ledgerRecord(struct ledger *ledger, const char *first, const char *second) {
    const char *low = NULL, *high = NULL;
    int written = 1;

    if (!ledger->file)
        return 1;

    ledgerOrder(first, second, &low, &high);
    #pragma omp critical (ledger)
    {
        /* The file first, the table second: a pair the file did not take is
           not done, and must not be skipped by anyone reading the table. */
        if (fprintf(ledger->file, "%s%c%s\n", low, LEDGER_SEPARATOR, high) < 0
                || fflush(ledger->file) != 0)
            written = 0;
        else
            ledgerInsert(ledger, ledgerHash(low, high));
    }
    return written;
}

int
ledgerClose(struct ledger *ledger) {
    int clean = 1;

    if (ledger->file) {
        if (fclose(ledger->file) != 0)
            clean = 0;
        ledger->file = NULL;
    }
    free(ledger->slots);
    ledger->slots = NULL;
    free(ledger->inputs);
    ledger->inputs = NULL;
    ledger->inputCount = ledger->inputCapacity = 0;
    ledger->capacity = 0;
    ledger->count = 0;
    return clean;
}
