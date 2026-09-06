#include "printers.h"

FILE *resultStream = NULL;

void
printBeautifulHeader () {
    char strBuffer[170] = { 0 };
    fprintf(resultStream, "%46.46s %46.46s %9.9s %12.12s %12.12s %5.5s\n",
        padStr("First signature", strBuffer, 40, ' '),
        padStr("Second signature",  &strBuffer[40], 51, ' '),
        padStr("score",  &strBuffer[91], 9, ' '),
        padStr("time 1 [s]", &strBuffer[100], 12, ' '),
        padStr("time 2 [s]", &strBuffer[112], 12, ' '),
        "whole");
}

void
printBeautiful(MatchingInfo *info, StreamContext* sc, char *file1,\
    char *file2, int isFirst, int isLast, int isMoreThanOne) {

    unsigned int selectedFormatStr = 0;
    char *firstFilePath = file1;
    char *formatStrings[] = {
        // First more than one
        "%-46.46s \u2533 %-46.46s %7d %12.2f %12.2f %1d\n",
        "%-46.46s \u2501 %-46.46s %7d %12.2f %12.2f %1d\n",
        // last
        "%-46.46s \u2517 %-46.46s %7d %12.2f %12.2f %1d\n",
        "%-46.46s \u2523 %-46.46s %7d %12.2f %12.2f %1d\n"
    };

    if (info->score) {
        if (isFirst) {
            if (isMoreThanOne) {
                selectedFormatStr = 0;
            } else {
                selectedFormatStr = 1;
            }
        } else if (isLast) {
            // We don't want to always print the first file path
            firstFilePath = " ";
            selectedFormatStr = 2;
        } else {
            firstFilePath = " ";
            selectedFormatStr = 3;
        }

        fprintf(resultStream, formatStrings[selectedFormatStr], firstFilePath, file2, info->score,\
                ((double) info->first->pts * sc[0].time_base.num) / sc[0].time_base.den,
                ((double) info->second->pts * sc[1].time_base.num) / sc[1].time_base.den,
                info->whole);
    }
}

/* Seconds for one frame, or -1 when there is no frame to report. A stored
   match always has at least one good frame under any thIt above zero, so the
   -1 is there to keep a NULL out of the arithmetic rather than because it is
   expected. */
static double
frameSeconds(FineSignature *frame, StreamContext *stream) {
    if (!frame)
        return -1.0;
    return ((double) frame->pts * stream->time_base.num) / stream->time_base.den;
}

// TO update with new offsets
void
printCSVHeader () {
    /* whole stays last: the smoke test in the nightly workflow reads it with
       awk $NF, so new columns go before it, not after. */
    fprintf(resultStream, "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n",
        "First signature", "Second signature",\
        "score", "matchframes", "goodframes", "totalframes",\
        "offset", "framerateratio", "meandist",\
        "time 1 [s]", "time 2 [s]",\
        "begin 1 [s]", "end 1 [s]", "begin 2 [s]", "end 2 [s]", "whole");
}

void
printCSV(MatchingInfo *info, StreamContext* sc, char *file1, char *file2,\
    int isFirst, int isLast, int isMoreThanOne) {
    if (info->score)
        fprintf(resultStream,
                "%s,%s,%d,%d,%d,%d,%d,%.6f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%d\n",
                file1, file2,
                info->score,
                info->matchframes,
                info->goodframes,
                info->totalframes,
                info->offset,
                info->framerateratio,
                info->meandist,
                // the frame the candidate was seeded on, somewhere inside the
                // match rather than at either end of it
                frameSeconds(info->first, &sc[0]),
                frameSeconds(info->second, &sc[1]),
                // where the match itself starts and ends in each file
                frameSeconds(info->firstBegin, &sc[0]),
                frameSeconds(info->firstEnd, &sc[0]),
                frameSeconds(info->secondBegin, &sc[1]),
                frameSeconds(info->secondEnd, &sc[1]),\
                info->whole);
}

void
printFineSigList(FineSignature *list, FineSignature *end, int lastCoarse) {
    for (FineSignature *i = list; i != end && i; i = i->next) {
        if (lastCoarse) {
            if (i->next != end) {
                slog_debug(7,"  \u2523\u2501\u2533 Fine signature at %p", i);
                slog_debug(7,"  \u2503 \u2517\u2501 Pts: %lu\t"\
                    "Confidence: %hhu", i->pts, i->confidence);
            } else {
                slog_debug(7,"  \u2517\u2501\u2533 Fine signature at %p", i);
                slog_debug(7,"    \u2517\u2501 Pts: %lu\t"\
                    "Confidence: %hhu", i->pts, i->confidence);
            }
        } else {
            if (i->next != end) {
                slog_debug(7,"\u2503 \u2523\u2501\u2533 Fine signature at %p",\
                        i);
                slog_debug(7,"\u2503 \u2503 \u2517\u2501 Pts: %lu\t\t"\
                    "Confidence: %hhu", i->pts, i->confidence);
            } else {
                slog_debug(7,"\u2503 \u2517\u2501\u2533 Fine signature at %p",\
                        i);
                slog_debug(7,"\u2503   \u2517\u2501 Pts: %lu\t"\
                    "Confidence: %hhu", i->pts, i->confidence);
            }
        }
    }
}

void
printCoarseSigList(CoarseSignature *list) {
    for (CoarseSignature *j = list; j->next ; j = j->next) {
        if (j->next->next) {
            slog_debug(7,"\u2523\u2533 Coarse signature at %p", j);
            slog_debug(7,"\u2503\u2523\u2501\u2578 Coarse signature bounds: "\
                "%lu %lu", j->first ? j->first->pts : -1,\
                j->last ? j->last->pts : -1);
            if (j->first && j->first->next) {
                slog_debug(7,"\u2503\u2517\u2533\u2578 Fine signatures "\
                    "bounds: %p %p", j->first, j->last);
            } else {
                slog_debug(7,"\u2503\u2517\u2501\u2578 Fine signatures "\
                    "bounds: %p %p", j->first, j->last);
            }
            printFineSigList(j->first, j->last, 0);
        } else {
            slog_debug(7,"\u2517\u2533 Coarse signature at %p", j);
            slog_debug(7," \u2523\u2501\u2578 Coarse signature bounds: "\
                "%lu %lu", j->first ? j->first->pts : -1,\
                j->last ? j->last->pts : -1);
            slog_debug(7," \u2517\u2501\u2578 Fine signatures "\
                "bounds: %p %p", list->first, list->last);
            printFineSigList(j->first, j->last, 1);
        }
    }
}

void
printStreamContext(StreamContext* sc) {
    {
        slog_debug(7,"\u250F Time base: %d/%d", sc->time_base.num,\
                sc->time_base.den);
        slog_debug(7,"\u2523 Width: %d\tHeight: %d",sc->w,sc->h);
        slog_debug(7,"\u2523 Overflow protection: %d", sc->divide);
        slog_debug(7,"\u2523 Fine signatures list: %p", sc->finesiglist);
        slog_debug(7,"\u2523 Coarse signature list: %p", sc->coarsesiglist);
        slog_debug(7,"\u2523 Last index: %d", sc->lastindex);
        slog_debug(7,"\u2523 Signatures:", sc->lastindex);
        printCoarseSigList(sc->coarsesiglist);
    }
}


void
printResult(
    struct fileIndex *index,
    MatchingInfo *result,
    SignatureContext *sigContext,
    int minimumScore,
    void (*printFunctionPointer)
    (MatchingInfo *info, StreamContext* sc, char *file1, char *file2, \
     int isFirst, int isLast, int isMoreThanOne)) {

    // These are necessary because otherwhise the correct format string
    // is used only when the first printed value is actually the first
    // file in the list
    static int hasFirstBeenPrinted = 0;


    #pragma omp critical
    {
        Assert(index);
        Assert(result);
        Assert(sigContext);
        Assert(printFunctionPointer);

        if (result->score >= minimumScore) {
            unsigned int i = index->indexA;
            unsigned int j = index->indexB;
            unsigned int maxIndex = FFMAX(index->maxIndexA, index->maxIndexB);
            StreamContext *scontexts = sigContext->streamcontexts;
            char *filePath1 = &index->pathsMatrix[i*MAX_PATH_LENGTH];
            char *filePath2 = &index->pathsMatrix[j*MAX_PATH_LENGTH];

            if (j == i + 1) {
                // Print first element
                if (maxIndex - j > 1) {
                    printFunctionPointer(result, scontexts, filePath1, filePath2,
                            1, 0, 1);
                    hasFirstBeenPrinted = 1;
                } else {
                    printFunctionPointer(result, scontexts, filePath1, filePath2,
                            1, 0, 0);
                }
                hasFirstBeenPrinted = 1;
            } else if (j == maxIndex - 1) {
                // Print last element
                printFunctionPointer(result, scontexts, filePath1, filePath2,
                        0, 1, 0);
            } else {
                // Print intermediate element
                if (hasFirstBeenPrinted)
                    printFunctionPointer(result, scontexts, filePath1, filePath2,
                            0, 0, 1);
                else {
                    if (maxIndex - j > 1) {
                        printFunctionPointer(result, scontexts, filePath1, filePath2,
                                1, 0, 1);
                        hasFirstBeenPrinted = 1;
                    } else {
                        printFunctionPointer(result, scontexts, filePath1, filePath2,
                                1, 0, 0);
                    }
                    hasFirstBeenPrinted = 1;
                }
            }

            fflush(stdout);
        }
    }
}
