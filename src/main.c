#include "main.h"

struct arguments args = {0};
struct ledger ledger = {0};

int
main(int argc, char **argv) {
    struct fileIndex index = {0};
    void (*printFunctionPointer)(MatchingInfo *info, StreamContext* sc,\
        char *file1, char *file2, int isFirst, int isLast, int isMoreThanOne)\
        = printBeautiful;

    /* slog writes with printf and offers no way to retarget it, so results and
       log lines both landed on stdout and `> out.csv` captured a mix of the two.
       Keep a duplicate of the real stdout for results, then point fd 1 at
       stderr. Every printf after this, slog's included, goes to stderr;
       results go through resultStream. */
    {
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
       benchmark harness captures it beside the results it belongs to. */
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
        terminateFileIterator(&index);
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

    if (args.outputFormat == CSV) {
        printCSVHeader();
        printFunctionPointer = printCSV;
    } else {
        printBeautifulHeader();
        printFunctionPointer = printBeautiful;
    }

    processFiles(&index, printFunctionPointer);
    terminateFileIterator(&index);

    slog_info(4, "Signature processing finished");

    return 0;
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
        ledgerOpen(&ledger, args.ledgerFile, (size_t) totalPairs);
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
    }

    long donePairs = 0;
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

    /* Parallelise the outer loop, not the inner one.
       Parallelising the inner loop forked and joined once per outer iteration,
       and its implicit barrier made finished threads wait for the slowest one,
       so utilisation collapsed at the end of every wave. Driving the outer loop
       instead forks once for the whole run: a thread that finishes one i picks
       up the next immediately, with no synchronisation in between. */
    #pragma omp parallel for schedule(dynamic)
    for (int i = index->indexA + 1; i < index->maxIndexA; ++i) {
        StreamContext scontextsBase[NUM_OF_INPUTS] = { 0 };
        char *file1 = &index->pathsMatrix[i*MAX_PATH_LENGTH];
        binary_import(&scontextsBase[0], file1);

        for (int j = i + 1; j < index->maxIndexB; ++j) {

            struct fileIndex tmpIndex = {
                .indexA = i,
                .indexB = j,
                .maxIndexA = index->maxIndexA,
                .maxIndexB = index->maxIndexB,
                .pathsMatrix = index->pathsMatrix
            };
            StreamContext scontexts[NUM_OF_INPUTS];
            MatchingInfo result = {0};
            char *file2 = &tmpIndex.pathsMatrix[tmpIndex.indexB*MAX_PATH_LENGTH];

            if (ledgerHas(&ledger, file1, file2))
                continue;

            scontexts[0] = scontextsBase[0];
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
            fflush(resultStream);
            /* After the result is flushed, so a pair is only ever marked done
               once its output is on its way out. */
            ledgerRecord(&ledger, file1, file2);

            long done;
            #pragma omp atomic capture
            done = ++donePairs;
            if (done % progressStep == 0 || done == totalPairs) {
                double elapsed = difftime(time(NULL), startTime);
                double rate = elapsed > 0.0 ? (double) done / elapsed : 0.0;
                slog_live(5,
                    "Progress %ld/%ld pairs (%.1f%%), %.0f pairs/s, ETA %.0f min",
                    done, totalPairs,
                    100.0 * (double) done / (double) totalPairs, rate,
                    rate > 0.0 ? ((double) (totalPairs - done)) / rate / 60.0 : 0.0);
            }

        }
        signature_unload(&scontextsBase[0]);
    }

    ledgerClose(&ledger);
}

// This function processes the signatures by using index as an iterator
void
processFilePair(
    struct fileIndex *index,
    void (*printFunctionPointer)
    (MatchingInfo *info, StreamContext* sc, char *file1, char *file2, \
     int isFirst, int isLast, int isMoreThanOne)) {

    StreamContext scontexts[NUM_OF_INPUTS] = { 0 };
    MatchingInfo result = {0};
    char *filePath1 = getIteratorIndexFilePath(index, 'a');
    char *filePath2 = getIteratorIndexFilePath(index, 'b');

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


    binary_import(&scontexts[0], filePath1);
    binary_import(&scontexts[1], filePath2);

    slog_debug(6, "Processing %s\t%s", filePath1, filePath2);

    result = processSignaturePair(&scontexts[0], &scontexts[1], sigContext);
    printResult(index, &result, &sigContext, args.minScore,
        printFunctionPointer);

    signature_unload(&scontexts[1]);
    signature_unload(&scontexts[0]);
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
