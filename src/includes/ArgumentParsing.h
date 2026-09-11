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
	CSV
};

struct arguments {
    int verbose;
    char *listFile;
    char *ledgerFile;
    char *incrementalFile;
    enum lookup_mode mode;
    enum signatureType sigType;
    enum formatTypes outputFormat;
    /* Integers where the core holds integers, so a value cannot change on the
       way in. thIt is the one ratio. */
    int thD, thDc, thXh, thDi, minScore;
    double thIt;
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
    {"longest", MODE_LONGEST},
    {"csv", CSV},
    /* numberForKey walks until it reads a null name, so the list has to end
       with one. Without it an unrecognised keyword read past the array and
       took the process with it. */
    {NULL, 0},
};


int numberForKey(char *key);

/* Strict number parsing for option values. Each returns 1 and stores the
   value, or returns 0 when text is empty, has anything after the number, is
   not finite, or lies outside the range: [lo, hi] for the integer, [0, 1] for
   the ratio. atoi and atof used to turn "abc" into 0 and "0.5x" into 0.5
   without a word. */
int parseIntOption(const char *text, long lo, long hi, long *out);
int parseRatioOption(const char *text, double *out);

struct arguments parseArguments(int, char**);

#endif
