#ifndef ARGUMENT_PARSING
#define ARGUMENT_PARSING


#include <string.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <argp.h>

#include "slog_compat.h"
#include "customAssert.h"
#include "signature.h"
#include "utils.h"




enum signatureType {
	BINARY, XML
};


enum formatTypes {
	BEAUTIFUL, CSV
};

struct arguments {
    int verbose;
    char *listFile;
    char *ledgerFile;
    char *incrementalFile;
    enum lookup_mode mode;
    enum signatureType sigType;
    enum formatTypes outputFormat;
	double thD, thDc, thXh, thDi, thIt, minScore;
    char **filePaths;
    unsigned int numberOfPaths;
    /* 0 means every core; -j sets an explicit count. */
    int jobs;
};

// https://stackoverflow.com/questions/6669842/how-to-best-achieve-string-
// to-number-mapping-in-a-c-program
struct entry {
    char *str;
    int n;
};

static struct entry dict[] = {
    {"binary", BINARY},
    {"xml", XML},
    {"fast", MODE_FAST},
    {"full", MODE_FULL},
    {"longest", MODE_LONGEST},
    {"csv", CSV},
    {"beautiful", BEAUTIFUL},
    /* numberForKey walks until it reads a null name, so the list has to end
       with one. Without it an unrecognised keyword read past the array and
       took the process with it. */
    {NULL, 0},
};


int numberForKey(char *key);

struct arguments parseArguments(int, char**);

#endif
