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

/* One run's identity, so the checks below can vary one thing at a time. */
static struct ledgerRun
runNamed(const char *version, const char *binary, const char *params) {
    struct ledgerRun run;
    snprintf(run.version, sizeof(run.version), "%s", version);
    snprintf(run.binary, sizeof(run.binary), "%s", binary);
    snprintf(run.params, sizeof(run.params), "%s", params);
    return run;
}

static int
openAs(struct ledger *ledger, const char *path, size_t extra,
    const char *version, const char *binary, const char *params, char *why) {
    struct ledgerRun run = runNamed(version, binary, params);
    why[0] = '\0';
    return ledgerOpen(ledger, path, extra, &run, why, 1024);
}

/* The usual run, for checks that are not about the record. */
static void
openUsual(struct ledger *ledger, const char *path, size_t extra) {
    char why[1024];
    if (!openAs(ledger, path, extra, "v0.1 b5", "0123456789abcdef",
            "-m longest -x 290", why))
        printf("  FAIL  could not open %s: %s\n", path, why);
}

static int
fileHas(const char *path, const char *text) {
    char line[1024];
    FILE *f = fopen(path, "r");
    int found = 0;
    if (!f)
        return 0;
    while (fgets(line, sizeof(line), f))
        if (strstr(line, text))
            found = 1;
    fclose(f);
    return found;
}

static void
writeFile(const char *path, const char *text) {
    FILE *f = fopen(path, "w");
    fputs(text, f);
    fclose(f);
}

void
suiteLedger(void) {
    char path[128];
    struct ledger ledger = {0};

    /* A pair is unordered, so the two directions have to land on one entry.
       Getting this wrong only shows up on a ledger someone edited by hand,
       which is why it needs a check of its own. */
    tempPath(path, sizeof(path));
    openUsual(&ledger, path, 16);
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
        openUsual(&ledger, path, (size_t) pairs);
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
        openUsual(&ledger, path, 16);
        ledgerRecord(&ledger, "a.bin", "b.bin");
        ledgerRecord(&ledger, "c.bin", "d.bin");
        ledgerClose(&ledger);

        f = fopen(path, "a");
        fprintf(f, "e.bin");   /* no separator, no newline */
        fclose(f);

        openUsual(&ledger, path, 16);
        CHECK_EQ("reopening restores the recorded pairs", ledger.count, 2);
        CHECK("a pair from the previous run is still known",
            ledgerHas(&ledger, "a.bin", "b.bin"));
        ledgerClose(&ledger);
        unlink(path);
    }

    /* -s can name a place that does not exist yet, so the directories leading
       to it are created. Two levels deep, because creating only the immediate
       parent would pass a one-level test and still fail here. */
    {
        char dir[160], deeper[200];

        snprintf(dir, sizeof(dir), "/tmp/ledger_dirs_%d", (int) getpid());
        snprintf(path, sizeof(path), "%s/a/b/pairs.ledger", dir);
        openUsual(&ledger, path, 8);
        ledgerRecord(&ledger, "a.bin", "b.bin");
        ledgerClose(&ledger);

        openUsual(&ledger, path, 8);
        CHECK("a ledger under directories that did not exist is created",
            ledgerHas(&ledger, "a.bin", "b.bin"));
        ledgerClose(&ledger);

        unlink(path);
        snprintf(deeper, sizeof(deeper), "%s/a/b", dir);
        rmdir(deeper);
        snprintf(deeper, sizeof(deeper), "%s/a", dir);
        rmdir(deeper);
        rmdir(dir);
    }

    /* Without -s there is no ledger, and every pair has to look uncompared. */
    {
        struct ledger disabled = {0};
        CHECK("with no ledger nothing counts as done",
            !ledgerHas(&disabled, "a.bin", "b.bin"));
        CHECK("and recording without one is a no-op that succeeds",
            ledgerRecord(&disabled, "a.bin", "b.bin") && disabled.count == 0);
    }

    /* The record. A ledger says what it was written for, and a resume that
       is not the same run is refused before a single pair is reused. */
    {
        char why[1024];

        tempPath(path, sizeof(path));
        CHECK("a fresh ledger opens", openAs(&ledger, path, 8, "v0.1 b5",
            "0123456789abcdef", "-m longest -x 290", why));
        ledgerRecord(&ledger, "a.bin", "b.bin");
        ledgerClose(&ledger);
        CHECK("and starts with a comment for the reader",
            fileHas(path, "# mpeg7dupes ledger"));
        CHECK("and carries the run record",
            fileHas(path, "#run\tversion=v0.1 b5\tbinary=0123456789abcdef"
                "\tparams=-m longest -x 290"));

        CHECK("the same run reopens it", openAs(&ledger, path, 8, "v0.1 b5",
            "0123456789abcdef", "-m longest -x 290", why));
        CHECK("with its pairs", ledgerHas(&ledger, "a.bin", "b.bin"));
        ledgerClose(&ledger);

        CHECK("other settings are refused", !openAs(&ledger, path, 8,
            "v0.1 b5", "0123456789abcdef", "-m longest -x 250", why));
        CHECK("and the reason names both", strstr(why, "-x 250")
            && strstr(why, "-x 290"));
        CHECK("another binary is refused", !openAs(&ledger, path, 8,
            "v0.1 b5", "fedcba9876543210", "-m longest -x 290", why));
        CHECK("another build is refused", !openAs(&ledger, path, 8,
            "v0.1 b6", "0123456789abcdef", "-m longest -x 290", why));
        CHECK("a refused open leaves nothing to close", ledger.slots == NULL);
        CHECK("and the file is untouched",
            fileHas(path, "params=-m longest -x 290")
            && !fileHas(path, "-x 250"));
        unlink(path);
    }

    /* A ledger with pairs and no record, from build 4 or earlier or written
       by hand, is reused as it always was, and given a record. */
    {
        char why[1024];

        tempPath(path, sizeof(path));
        writeFile(path, "a.bin\tb.bin\nc.bin\td.bin\n");
        CHECK("a recordless ledger is accepted", openAs(&ledger, path, 8,
            "v0.1 b5", "0123456789abcdef", "-m longest", why));
        CHECK("with its pairs", ledgerHas(&ledger, "c.bin", "d.bin"));
        ledgerClose(&ledger);
        CHECK("and it carries a record afterwards",
            fileHas(path, "#run\tversion=v0.1 b5"));
        CHECK("which a different run is then refused by", !openAs(&ledger,
            path, 8, "v0.1 b5", "0123456789abcdef", "-m full", why));
        unlink(path);
    }

    /* Inputs. Each is recorded with its size and content the first time it
       is seen; a changed one makes the pairs it was in stale, so it is
       refused. */
    {
        char why[1024], input[128];

        tempPath(path, sizeof(path));
        snprintf(input, sizeof(input), "%s.input", path);
        writeFile(input, "signature bytes");
        openUsual(&ledger, path, 8);
        CHECK("a new input is noted", ledgerNoteInput(&ledger, input, why,
            sizeof(why)));
        CHECK("and noting it again in the same run is fine",
            ledgerNoteInput(&ledger, input, why, sizeof(why)));
        CHECK_EQ("once", ledger.inputCount, 1);
        ledgerClose(&ledger);
        CHECK("the file carries it", fileHas(path, "#input\t")
            && fileHas(path, ".input\t15\t"));

        openUsual(&ledger, path, 8);
        CHECK("an unchanged input passes on the next run",
            ledgerNoteInput(&ledger, input, why, sizeof(why)));
        writeFile(input, "other bytes ok!");   /* same length, other content */
        CHECK("a rewrite of the same length is refused",
            !ledgerNoteInput(&ledger, input, why, sizeof(why)));
        CHECK("and the reason names the file and says the content differs",
            strstr(why, input) && strstr(why, "other content"));
        writeFile(input, "longer than it was before");
        CHECK("a rewrite of another length is refused",
            !ledgerNoteInput(&ledger, input, why, sizeof(why)));
        CHECK("a missing input is refused with its name",
            !ledgerNoteInput(&ledger, "/nonexistent/x.bin", why, sizeof(why))
            && strstr(why, "/nonexistent/x.bin"));
        ledgerClose(&ledger);
        unlink(input);
        unlink(path);
    }

    /* The digest that identifies the binary and the inputs. */
    {
        long size = 0;
        char input[128], other[128];
        uint64_t one, two, again;

        tempPath(path, sizeof(path));
        snprintf(input, sizeof(input), "%s.a", path);
        snprintf(other, sizeof(other), "%s.b", path);
        writeFile(input, "the same");
        writeFile(other, "the same");
        one = ledgerHashFile(input, &size);
        two = ledgerHashFile(other, &size);
        CHECK("equal content has an equal digest and the size read", one == two
            && size == 8);
        writeFile(other, "not same");
        again = ledgerHashFile(other, &size);
        CHECK("other content of the same length differs", again != one);
        CHECK_EQ("an unreadable file digests to 0",
            ledgerHashFile("/nonexistent/x", &size), 0);
        unlink(input);
        unlink(other);
    }
}
