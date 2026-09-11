#include "utils.h"

// Merges 2 fileIndex structs into one and returns a new fileIndex struct
// where the default new indexA and indexB are taken from the first
// struct and the default maxIndexes are the sum of the maxIndex of both
// structs
struct fileIndex
mergeFileIterators(struct fileIndex *index1, struct fileIndex *index2) {
    struct fileIndex newFileIndex = {0};
    char *newPathsMatrix = NULL;
    unsigned int maxFiles1 = FFMAX(index1->maxIndexA, index1->maxIndexB);
    unsigned int maxFiles2 = FFMAX(index2->maxIndexA, index2->maxIndexB);
    unsigned int maxFiles = maxFiles1 + maxFiles2;

    // Merge paths into memory
    newPathsMatrix = calloc(maxFiles, MAX_PATH_LENGTH);
    memcpy(newPathsMatrix, index1->pathsMatrix, maxFiles1*MAX_PATH_LENGTH);
    memcpy(newPathsMatrix + maxFiles1*MAX_PATH_LENGTH, index2->pathsMatrix,
        maxFiles2*MAX_PATH_LENGTH);

    newFileIndex.indexA = index1->indexA;
    newFileIndex.indexB = index1->indexB;
    newFileIndex.maxIndexA = maxFiles;
    newFileIndex.maxIndexB = maxFiles;
    newFileIndex.pathsMatrix = newPathsMatrix;

    return newFileIndex;
}

/* The next entry of a list file: the next line that is not blank, with its
   line ending removed, CRLF included. Returns 1 with the entry in buf, which
   holds MAX_PATH_LENGTH bytes, or 0 at the end of the file. A line too long
   for buf stops the run naming the list and the line, because the old fgets
   and strtok pair kept the first 319 bytes as one entry and the rest as the
   next, and compared two files nobody had named. lineNumber counts every
   line read so the message can point at the right one. */
static int
readListEntry(FILE *list, char *buf, const char *listName,
    unsigned int *lineNumber) {
    while (fgets(buf, MAX_PATH_LENGTH, list)) {
        size_t len = strlen(buf);
        size_t lead = 0;

        ++*lineNumber;
        if (len == MAX_PATH_LENGTH - 1 && buf[len - 1] != '\n') {
            /* buf is full and the line has not ended. It may end right
               here, which is fine; anything else is a longer path. */
            int c = getc(list);
            if (c != '\n' && c != EOF) {
                slog_fatal(1, "%s line %u: path longer than %d bytes: %.40s...",
                    listName, *lineNumber, MAX_PATH_LENGTH - 1, buf);
                exit(1);
            }
        }
        while (len && (buf[len - 1] == '\n' || buf[len - 1] == '\r'))
            buf[--len] = '\0';
        while (buf[lead] == ' ' || buf[lead] == '\t')
            ++lead;
        if (buf[lead] == '\0')
            continue; /* blank */
        return 1;
    }
    return 0;
}

int
initFileIterator(struct fileIndex *fileIndex, char *fileListName) {
    Assert(fileIndex);
    FILE *listFile = fopen(fileListName, "r");
    Assert(listFile);
    fileIndex->indexA = -1;
    fileIndex->indexB = 0;
    fileIndex->maxIndexA = getNumberOfLinesFromFilename(fileListName);
    fileIndex->maxIndexB = fileIndex->maxIndexA;
    int maxFiles = FFMAX(fileIndex->maxIndexA, fileIndex->maxIndexB);
    unsigned int line = 0;


    // Max path length, 320 chars should be enough for most cases
    // To save more memory the paths could be allocated on request
    // 10 000 file paths should use 3 200 000 chars (3.2 kb)
    char* pathsMatrix = (char*) calloc(maxFiles, MAX_PATH_LENGTH);

    for (int i = 0; i < maxFiles; ++i) {
        /* Counted a moment ago by the same reader, so an entry is there. */
        int got = readListEntry(listFile, &pathsMatrix[MAX_PATH_LENGTH*i],
            fileListName, &line);
        Assert(got);
    }

    fileIndex->pathsMatrix = pathsMatrix;
    fclose(listFile);
    Assert(maxFiles);
    return 1;
}

int
initFileIteratorFromCmdLine(struct fileIndex *fileIndex,
	char **argv, int argc) {
    fileIndex->indexA = -1;
    fileIndex->indexB = 0;
    fileIndex->maxIndexA = argc;
    fileIndex->maxIndexB = argc;

    fileIndex->pathsMatrix = (char*) calloc(argc, MAX_PATH_LENGTH);
    for (int i = 0; i < argc; ++i) {
        /* The argument parser refused longer paths already; this keeps the
           copy bounded whatever the caller did. */
        LoggedAssert(strlen(argv[i]) < MAX_PATH_LENGTH,
            "Path longer than %d bytes: %.40s...", MAX_PATH_LENGTH - 1, argv[i]);
        snprintf(&fileIndex->pathsMatrix[i*MAX_PATH_LENGTH], MAX_PATH_LENGTH,
            "%s", argv[i]);
    }
    return 1;
}

/* Entries, not lines: blank lines are skipped and a last line without a
   newline still counts. Counting newlines used to drop that last entry, and
   with two entries in the list that meant "at least two entries required". */
unsigned int
getNumberOfLinesFromFilename(char *filename) {
    Assert(filename);
    FILE *listFile = fopen(filename, "r");
    unsigned int numbOfEntries = 0, line = 0;
    char buf[MAX_PATH_LENGTH];
    Assert(listFile);

    while (readListEntry(listFile, buf, filename, &line))
        ++numbOfEntries;
    fclose(listFile);
    return numbOfEntries;
}

int
terminateFileIterator(struct fileIndex *fileIndex) {
    free(fileIndex->pathsMatrix);
    return 1;
}


int
nextFileIterationByIndex(struct fileIndex *fileIndex, char indexSelector) {
    switch (indexSelector) {
        case 'a': {
            ++fileIndex->indexA;
            if (fileIndex->indexA >= fileIndex->maxIndexA)
                return 0;
            break;
        }
        case 'b': {
            ++fileIndex->indexB;
            if (fileIndex->indexB >= fileIndex->maxIndexB) {
                fileIndex->indexB = fileIndex->indexA + 1;
                return 0;
            }
            break;
        }
    }
    return 1;
}

// Given a fileIndex struct
// This function iterates over all the combinations of the lines
// in the file list
int
nextFileIteration(struct fileIndex *fileIndex) {
    fileIndex->indexB++;

    if (fileIndex->indexA >= fileIndex->maxIndexA) {
        return 0;
    }

    if (fileIndex->indexB >= fileIndex->maxIndexB) {
        ++fileIndex->indexA;
        fileIndex->indexB = fileIndex->indexA;
        return nextFileIteration(fileIndex);
    }
    return 1;
}

char *
getIteratorIndexFilePath(struct fileIndex *fileIndex, char indexSelector){
    int effectiveIndex = 0;
    LoggedAssert(fileIndex->indexA >= 0 && fileIndex->indexB >= 0,
        "File iterator next function not called!");
    switch (indexSelector) {
        case 'a': effectiveIndex = fileIndex->indexA*MAX_PATH_LENGTH;
            break;
        case 'b': effectiveIndex = fileIndex->indexB*MAX_PATH_LENGTH;
            break;
    }
    return &fileIndex->pathsMatrix[effectiveIndex];
}


int
fineSignatureCmp(const void* p1, const void* p2) {
    FineSignature *a = (FineSignature*) p1;
    FineSignature *b = (FineSignature*) p2;
    if (a->pts == b->pts)
        return 0;
    else if (a->pts < b->pts)
        return -1;
    else
        return 1;
};


unsigned int
getFileSize(const char *filename) {
    int fileLength = 0;
    FILE *f = NULL;
    f = fopen(filename, "rb");
    LoggedAssert(f, "Can't open %s", filename);
    fseek(f, 0, SEEK_END);
    fileLength = ftell(f);
    fclose(f);
    return fileLength;
};


unsigned int
getPathLastSlashPosition(const char *path) {
    unsigned int pathLen = strlen(path);
    unsigned int lastSlashPosition = 0;

    for (unsigned int i = 0; i < pathLen; ++i) {
        if (i > 0) {
            if (path[i] == '/' && path[i - 1] != '/')
                lastSlashPosition = i;
        } else {
            if (path[i] == '/')
                lastSlashPosition = i;
        }
    }
    return lastSlashPosition;
}
