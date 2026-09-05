#include "main.h"

struct arguments args = {0};
struct session session = {0};

void (*oldSEGVhandler)(int) = NULL;

int
main(int argc, char **argv) {
    struct fileIndex index = {0};
    void (*printFunctionPointer)(MatchingInfo *info, StreamContext* sc,\
        char *file1, char *file2, int isFirst, int isLast, int isMoreThanOne)\
        = printBeautiful;

    /* slog writes with printf and offers no way to retarget it, so results and
       log lines both landed on stdout and `> out.csv` captured a mix of the two.
       Keep a duplicate of the real stdout for results, then point fd 1 at
       stderr. Every printf after this, slog's included and the Ctrl+C session
       prompt among them, goes to stderr; results go through resultStream. */
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

    slog_init("logfile", "slog.cfg", 5, 1);
    initSession(&session, &args, &index);


    signal(SIGINT, INThandler);
    oldSEGVhandler = signal(SIGSEGV, SEGVhandler);


    args = parseArguments(argc, argv);

    slog_info(4, "Logging initialized");

    if (args.sessionFile)
        loadSession(&args, &index, args.sessionFile);
    else {
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
    }


    // 0    panic
    // 2    error
    // 3    warn
    // 4    info
    // 5    live
    // 6    debug
    if (__DEBUG || args.verbose) {
        slog_init("logfile", "slog.cfg", 6, 1);
    }

    if (args.useOpenMp)
        slog_info(4, "Using %d threads", omp_get_max_threads());

    if (args.outputFormat == CSV) {
        printCSVHeader();
        printFunctionPointer = printCSV;
    } else {
        printBeautifulHeader();
        printFunctionPointer = printBeautiful;
    }

    processFiles(&index, printFunctionPointer, args.useOpenMp);
    terminateFileIterator(&index);

    if (args.sessionFile)
        deleteSession(args.sessionFile);
    slog_info(4, "Signature processing finished");

    return 0;
}


void
processFiles(struct fileIndex *index, void (*printFunctionPointer)
    (MatchingInfo *info, StreamContext* sc, char *file1, char *file2, \
     int isFirst, int isLast, int isMoreThanOne), int useOpenMp) {

    /* Session-resume bookkeeping. Now that the outer loop runs in parallel the
       iterations no longer complete in increasing order, so only advance the
       saved index once every outer iteration below it has finished. Resuming
       then repeats work at worst, and never skips a pair. */
    char *outerDone = calloc((size_t) index->maxIndexA, 1);
    int firstIncomplete = index->indexA + 1;

    /* Parallelise the outer loop, not the inner one.
       Parallelising the inner loop forked and joined once per outer iteration,
       and its implicit barrier made finished threads wait for the slowest one,
       so utilisation collapsed at the end of every wave. Driving the outer loop
       instead forks once for the whole run: a thread that finishes one i picks
       up the next immediately, with no synchronisation in between. */
    #pragma omp parallel for schedule(dynamic) if(useOpenMp)
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

        }
        signature_unload(&scontextsBase[0]);

        /* Named so it cannot collide with the unnamed critical in printResult. */
        #pragma omp critical (outerProgress)
        {
            if (outerDone) {
                outerDone[i] = 1;
                while (firstIncomplete < index->maxIndexA
                        && outerDone[firstIncomplete])
                    ++firstIncomplete;
                index->indexA = firstIncomplete - 1;
                index->indexB = firstIncomplete;
            }
        }
    }

    free(outerDone);
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

void
INThandler(int sig)
{
     char  c;

     printf(" detected Do you really want to quit or save the session?"
        " [Yes/No/Save] ");
     fflush(stdout);

     c = getchar();
     switch (c) {
         case 'y':
         case 'Y':
             exit(0);
             break;

         case 's':
         case 'S':
             saveSessionPrompt(&session);
             exit(0);
             break;

         case 'n':
         case 'N':
         default:
             break;
     }
     // We have to reset the handler after every catch
     signal(SIGINT, INThandler);
     getchar(); // Get new line character
}

void
SEGVhandler(int sig)
{
     slog_panic(0, "Segfault detected, saving session");
     saveSession(&session,"segfaultedSession.sess");
     // We crash this program, with no handlers!
     signal(SIGSEGV, oldSEGVhandler);
     raise(SIGSEGV);
}
