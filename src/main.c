#include "main.h"

#include <errno.h>
#include <inttypes.h>

struct arguments args = {0};
struct ledger ledger = {0};

/* What this run is, for the ledger's record: the build as it calls itself,
   a digest of the binary that is actually running, and every setting that
   changes the output. The digest is what tells two builds of uncommitted
   code apart, which the version string cannot. -m and -f are not in it: each
   has had one value since builds 10 and 8. */
static void
describeRun(struct ledgerRun *run) {
    long size = 0;
    uint64_t digest = ledgerHashFile("/proc/self/exe", &size);

    snprintf(run->version, sizeof(run->version), "%s",
        MPEG7DUPES_VERSION_STRING);
    if (digest)
        snprintf(run->binary, sizeof(run->binary), "%016" PRIx64, digest);
    else
        snprintf(run->binary, sizeof(run->binary), "unknown");
    snprintf(run->params, sizeof(run->params),
        "-d %d -c %d -x %d -i %d -b %.17g -k %d",
        args.thD, args.thDc, args.thXh, args.thDi, args.thIt, args.minScore);
}

/* A pair is only done once its row is on its way out and its line is in the
   ledger. A write that fails is not a pair that did not match, so the run
   stops rather than record it as done. */
static void
stopOnWriteError(const char *what, const char *path) {
    slog_fatal(1, "Cannot write %s%s%s: %s", what, path ? " " : "",
        path ? path : "", strerror(errno));
    exit(1);
}

int
main(int argc, char **argv) {
    struct fileIndex index = {0};
    void (*printFunctionPointer)(MatchingInfo *info, StreamContext* sc,\
        char *file1, char *file2, int isFirst, int isLast, int isMoreThanOne)\
        = printCSV;

    /* slog writes with printf and offers no way to retarget it, so results and
       log lines both landed on stdout and `> out.csv` captured a mix of the two.
       Keep a duplicate of the real stdout for results, then point fd 1 at
       stderr. Every printf after this, slog's included, goes to stderr;
       results go through resultStream. */
    /* Except when argp is about to print something and exit. Those flags
       produce no results, so there is nothing to keep stdout clean for, and
       redirecting anyway sent the whole of --help to stderr, where a pipe
       into less or grep could not see it. */
    int printsAndExits = 0;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-?")
                || !strcmp(argv[i], "--usage")
                || !strcmp(argv[i], "--version") || !strcmp(argv[i], "-V"))
            printsAndExits = 1;
    }

    if (!printsAndExits) {
        int savedStdout = dup(STDOUT_FILENO);
        if (savedStdout >= 0) {
            resultStream = fdopen(savedStdout, "w");
            if (resultStream) {
                setvbuf(resultStream, NULL, _IOLBF, 0);
                dup2(STDERR_FILENO, STDOUT_FILENO);
            }
        }
    }
    if (!resultStream)
        resultStream = stdout;

    slog_compat_init("logfile", 5, 1);
    /* First line of every run, on stderr with the rest of the log, so the
       benchmark harness captures it beside the results it belongs to. Skipped
       for the flags that only print: argp says the same thing there, and
       saying it twice is not saying it better. */
    if (!printsAndExits)
        slog_info(4, "mpeg7dupes %s", MPEG7DUPES_VERSION_STRING);

    args = parseArguments(argc, argv);

    slog_info(4, "Logging initialized");

    if (args.listFile)
        initFileIterator(&index, args.listFile);
    else
        initFileIteratorFromCmdLine(&index, args.filePaths,\
            args.numberOfPaths);

    if (args.incrementalFile) {
        struct fileIndex incrementalIndex = {0};
        struct fileIndex tmpIndex = {0};

        slog_info(4, "Incremental mode selected");
        initFileIterator(&incrementalIndex, args.incrementalFile);
        tmpIndex = mergeFileIterators(&incrementalIndex, &index);
        tmpIndex.maxIndexA = getNumberOfLinesFromFilename(args.incrementalFile);
        /* The merge copies both tables, so both inputs go. The incremental
           one used to be kept, which LeakSanitizer reports and which made
           every -n run exit 1 under make test DEBUG=1. */
        terminateFileIterator(&index);
        terminateFileIterator(&incrementalIndex);
        index = tmpIndex;
    }


    // 0    panic
    // 2    error
    // 3    warn
    // 4    info
    // 5    live
    // 6    debug
    // 7    per-frame signature dump
    //
    // -v is cumulative; each extra v opens one more level:
    //   (none)  4  basic information
    //   -v      5  progress reporting
    //   -vv     6  per-pair processing and stage 3 match details
    //   -vvv    7  per-frame dump
    {
        int logLevel = 4 + args.verbose;
        if (logLevel > 7)
            logLevel = 7;
        if (__DEBUG)
            logLevel = 7;
        slog_compat_init("logfile", logLevel, 1);
    }

    /* Every core unless -j says otherwise. Asking for more cores than the
       machine has is a mistake worth pointing out rather than silently
       honouring, since oversubscribing only adds scheduling overhead. */
    {
        int availableJobs = omp_get_num_procs();
        int jobs = args.jobs > 0 ? args.jobs : availableJobs;

        if (jobs > availableJobs) {
            slog_warn(3, "Requested %d jobs but this machine has %d cores, "
                "using %d", args.jobs, availableJobs, availableJobs);
            jobs = availableJobs;
        }

        omp_set_num_threads(jobs);
        slog_info(4, "Using %d of %d cores", jobs, availableJobs);
    }

    printCSVHeader();
    printFunctionPointer = printCSV;

    processFiles(&index, printFunctionPointer);
    terminateFileIterator(&index);

    if (fflush(resultStream) != 0 || ferror(resultStream))
        stopOnWriteError("the results", NULL);
    slog_info(4, "Signature processing finished");

    return 0;
}


typedef void (*PairPrinter)(MatchingInfo *, StreamContext *, char *, char *, int, int, int);

struct pairProgress {
    long done, total, step;
    time_t start;
};

/* Both scheduling paths use exactly this completion protocol. The source
   lists are shared read-only; lookup's LUT and candidates belong to this pair. */
static void
comparePair(struct fileIndex *index, int i, int j, const StreamContext *source,
            struct pairProgress *progress, PairPrinter printFunctionPointer) {
    char *file1 = &index->pathsMatrix[i * MAX_PATH_LENGTH];
    struct fileIndex tmpIndex = {
        .indexA = i,
        .indexB = j,
        .maxIndexA = index->maxIndexA,
        .maxIndexB = index->maxIndexB,
        .pathsMatrix = index->pathsMatrix
    };
    StreamContext scontexts[NUM_OF_INPUTS] = {0};
    MatchingInfo result = {0};
    char *file2 = &tmpIndex.pathsMatrix[tmpIndex.indexB*MAX_PATH_LENGTH];

    if (ledgerHas(&ledger, file1, file2))
        return;

    slog_debug(6, "Worker %d: pair %d,%d", omp_get_thread_num(), i, j);
    /* Only the context shell is copied; the loaded signature lists are read-only. */
    scontexts[0] = *source;
    binary_import(&scontexts[1], file2);

    SignatureContext sigContext = {
        .class = NULL,
        .mode = args.mode,
        .nb_inputs = NUM_OF_INPUTS,
        .filename = "",
        .thworddist = args.thD,
        .thcomposdist = args.thDc,
        .thl1 = args.thXh,
        .thdi = args.thDi,
        .thit = args.thIt,
        .streamcontexts = scontexts
    };

    result = processSignaturePair(&scontexts[0], &scontexts[1],
        sigContext);
    printResult(&tmpIndex, &result, &sigContext, args.minScore,
        printFunctionPointer);

    signature_unload(&scontexts[1]);
    if (fflush(resultStream) != 0 || ferror(resultStream))
        stopOnWriteError("the results", NULL);
    /* After the result is flushed, so a pair is only ever marked done
       once its output is on its way out. */
    if (!ledgerRecord(&ledger, file1, file2))
        stopOnWriteError("the ledger", args.ledgerFile);

    long done;
    #pragma omp atomic capture
    done = ++progress->done;
    if (done % progress->step == 0 || done == progress->total) {
        double elapsed = difftime(time(NULL), progress->start);
        double rate = elapsed > 0.0 ? (double) done / elapsed : 0.0;
        slog_live(5,
            "Progress %ld/%ld pairs (%.1f%%), %.0f pairs/s, ETA %.0f min",
            done, progress->total,
            100.0 * (double) done / (double) progress->total, rate,
            rate > 0.0 ? ((double) (progress->total - done)) / rate / 60.0 : 0.0);
    }
}

void
processFiles(struct fileIndex *index, void (*printFunctionPointer)
    (MatchingInfo *info, StreamContext* sc, char *file1, char *file2, \
     int isFirst, int isLast, int isMoreThanOne)) {

    /* Total pairs is the sum of each outer iteration's inner trip count. For the
       usual case (indexA = -1, maxIndexA = maxIndexB = N) that is N*(N-1)/2, and
       it stays correct in incremental mode where the two bounds differ. */
    long totalPairs = 0;
    for (int i = index->indexA + 1; i < index->maxIndexA; ++i) {
        long remaining = (long) index->maxIndexB - (i + 1);
        if (remaining > 0)
            totalPairs += remaining;
    }

    long skippedPairs = 0;

    if (args.ledgerFile) {
        char why[2 * MAX_PATH_LENGTH + 1024];
        struct ledgerRun run;

        for (int i = 0; i < index->maxIndexB; ++i) {
            const char *path = &index->pathsMatrix[i * MAX_PATH_LENGTH];
            if (!ledgerPathUsable(path)) {
                slog_fatal(1, "Path cannot be represented in a ledger (Tab, CR/LF or leading #): %s", path);
                exit(1);
            }
        }
        describeRun(&run);
        if (!ledgerOpen(&ledger, args.ledgerFile, (size_t) totalPairs, &run,
                why, sizeof(why))) {
            slog_fatal(1, "%s", why);
            exit(1);
        }
        /* Every input, before a single pair is skipped: a changed signature
           makes every recorded pair it took part in stale. */
        for (int i = 0; i < index->maxIndexB; ++i)
            if (!ledgerNoteInput(&ledger, &index->pathsMatrix[i*MAX_PATH_LENGTH],
                    why, sizeof(why))) {
                slog_fatal(1, "%s", why);
                exit(1);
            }
        /* Counted up front so the progress line and the ETA describe the work
           this run will actually do, not the work the whole batch would. */
        for (int i = index->indexA + 1; i < index->maxIndexA; ++i) {
            char *first = &index->pathsMatrix[i*MAX_PATH_LENGTH];
            for (int j = i + 1; j < index->maxIndexB; ++j)
                if (ledgerHas(&ledger, first,
                        &index->pathsMatrix[j*MAX_PATH_LENGTH]))
                    skippedPairs++;
        }
        totalPairs -= skippedPairs;
        if (skippedPairs)
            slog_info(4, "Resuming: %ld pairs already in the ledger are "
                "skipped, so this run's output holds only the rest. Append "
                "it to the earlier output rather than overwriting it.",
                skippedPairs);
    }

    /* Report every 1%, but no more often than every 50 pairs. */
    long progressStep = totalPairs / 100;
    if (progressStep < 50)
        progressStep = 50;
    time_t startTime = time(NULL);

    if (skippedPairs)
        slog_live(5, "Comparing %ld file pairs, skipping %ld already in the "
            "ledger", totalPairs, skippedPairs);
    else
        slog_live(5, "Comparing %ld file pairs", totalPairs);

    struct pairProgress progress = {0, totalPairs, progressStep, startTime};
    if (totalPairs > 0 && args.incrementalFile && index->maxIndexA == 1
            && index->indexA == -1) {
        /* One source otherwise leaves only one outer iteration for OpenMP.
           Load it once, then create one team for all candidate pairs. The
           barrier finishes every reader before unloading the source. */
        StreamContext source = {0};
        binary_import(&source, index->pathsMatrix);
        #pragma omp parallel for schedule(dynamic)
        for (int j = 1; j < index->maxIndexB; ++j)
            comparePair(index, 0, j, &source, &progress, printFunctionPointer);
        signature_unload(&source);
    } else if (totalPairs > 0) {
        /* Keep the full-library/multiple-incremental outer-loop schedule:
           one team, one loaded source per active outer iteration. */
        #pragma omp parallel for schedule(dynamic)
        for (int i = index->indexA + 1; i < index->maxIndexA; ++i) {
            StreamContext source = {0};
            binary_import(&source, &index->pathsMatrix[i * MAX_PATH_LENGTH]);
            for (int j = i + 1; j < index->maxIndexB; ++j)
                comparePair(index, i, j, &source, &progress, printFunctionPointer);
            signature_unload(&source);
        }
    }

    if (!ledgerClose(&ledger))
        stopOnWriteError("the ledger", args.ledgerFile);
}

MatchingInfo
processSignaturePair(
    struct StreamContext *signatureA,
    struct StreamContext *signatureB,
    struct SignatureContext sigContext) {

    MatchingInfo result = {0};

    result = lookup_signatures(&sigContext, signatureA, signatureB);
    return result;
}
