#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <math.h>

#include "ArgumentParsing.h"
#include "version.h"

/* argp looks this up by name at file scope. It was declared inside
   parseArguments, where argp could not see it, so --version printed nothing
   and the string sat at 0.0.1 unnoticed. */
const char *argp_program_version = "mpeg7dupes " MPEG7DUPES_VERSION_STRING;

static int
numberForKeyIn(const struct entry *table, const char *key)
{
    for (int i = 0; table[i].str; ++i)
        if (strcmp(table[i].str, key) == 0)
            return table[i].n;
    return -1;
}

int
numberForKey(char *key)
{
    return numberForKeyIn(dict, key);
}

/* One table per option. The shared dict let -m accept "csv", which happens to
   share a number with "full", and -f accept "fast". */
static const struct entry modeWords[] = {
    {"fast", MODE_FAST}, {"full", MODE_FULL}, {"longest", MODE_LONGEST},
    {NULL, 0},
};
static const struct entry formatWords[] = {
    {"csv", CSV}, {NULL, 0},
};
static const struct entry typeWords[] = {
    {"binary", BINARY}, {"xml", XML}, {NULL, 0},
};

int
parseIntOption(const char *text, long lo, long hi, long *out)
{
    char *end = NULL;
    long value;

    if (!text || !*text || isspace((unsigned char) *text))
        return 0;
    errno = 0;
    value = strtol(text, &end, 10);
    if (errno || end == text || *end != '\0')
        return 0;
    if (value < lo || value > hi)
        return 0;
    *out = value;
    return 1;
}

int
parseRatioOption(const char *text, double *out)
{
    char *end = NULL;
    double value;

    if (!text || !*text || isspace((unsigned char) *text))
        return 0;
    errno = 0;
    value = strtod(text, &end);
    if (errno || end == text || *end != '\0')
        return 0;
    if (!isfinite(value) || value < 0.0 || value > 1.0)
        return 0;
    *out = value;
    return 1;
}

/* The three below store a value or stop with a usage error that names the
   option and the value, so nothing the program cannot honour is quietly
   replaced by something else. argp_error does not return. */
static void
intOption(struct argp_state *state, const char *option, const char *arg,
    long lo, long hi, int *out)
{
    long value = 0;
    if (!parseIntOption(arg, lo, hi, &value))
        argp_error(state, "%s: '%s' is not a whole number between %ld and %ld",
            option, arg, lo, hi);
    *out = (int) value;
}

static void
ratioOption(struct argp_state *state, const char *option, const char *arg,
    double *out)
{
    if (!parseRatioOption(arg, out))
        argp_error(state, "%s: '%s' is not a number between 0 and 1",
            option, arg);
}

static void
keywordOption(struct argp_state *state, const char *option, const char *arg,
    const struct entry *table, const char *choices, int *out)
{
    int value = numberForKeyIn(table, arg);
    if (value < 0)
        argp_error(state, "%s: '%s' is not one of %s", option, arg, choices);
    *out = value;
}

/* A path has to fit the fixed-width table the comparison keeps its inputs in,
   and it has to survive as one line of CSV and one line of the ledger, so a
   line break is out. Refusing here beats truncating and comparing the wrong
   file. */
static void
checkPathUsable(struct argp_state *state, const char *path)
{
    if (strlen(path) >= MAX_PATH_LENGTH)
        argp_error(state, "path is %zu bytes long, the limit is %d: %.40s...",
            strlen(path), MAX_PATH_LENGTH - 1, path);
    if (strpbrk(path, "\r\n"))
        argp_error(state, "path contains a line break, which neither the "
            "output nor the ledger can hold: %.40s...", path);
}



static error_t parse_opt(int key, char *arg, struct argp_state *state) {
    struct arguments *arguments = state->input;
    // Key and param seems to be mutually exclusive, so we use this flag
    // to remember the last used key so that we can assign the correct
    // value
    switch (key) {
        case 'v': ++arguments->verbose; break;
        case 'j': intOption(state, "-j/--jobs", arg, 0, INT_MAX,
                      &arguments->jobs); break;
        case 'm': keywordOption(state, "-m/--lookup_mode", arg, modeWords,
                      "fast, full, longest", (int *) &arguments->mode); break;
        case 't': keywordOption(state, "-t/--signature_type", arg, typeWords,
                      "binary, xml", (int *) &arguments->sigType); break;
        case 'd': intOption(state, "-d/--thD", arg, 0, INT_MAX,
                      &arguments->thD); break;
        case 'c': intOption(state, "-c/--thDc", arg, 0, INT_MAX,
                      &arguments->thDc); break;
        case 'x': intOption(state, "-x/--thXh", arg, 0, INT_MAX,
                      &arguments->thXh); break;
        case 'i': intOption(state, "-i/--thDi", arg, 0, INT_MAX,
                      &arguments->thDi); break;
        case 'b': ratioOption(state, "-b/--thIt", arg, &arguments->thIt);
                      break;
        case 'k': intOption(state, "-k/--minimum_score", arg, 1, INT_MAX,
                      &arguments->minScore); break;
        case 'f': keywordOption(state, "-f/--output_format", arg, formatWords,
                      "csv", (int *) &arguments->outputFormat);
                      break;
        case 'l': if (arg) arguments->listFile = arg; break;
        case 's': if (arg) arguments->ledgerFile = arg; break;
        case 'n': if (arg) arguments->incrementalFile = arg; break;
        case ARGP_KEY_ARG:
            break;
        case ARGP_KEY_INIT:
            slog_debug(6, "Initializing arg parsing");
            arguments->verbose = 0;
            arguments->listFile = NULL;
            arguments->ledgerFile = NULL;
            arguments->incrementalFile = NULL;
            /* longest, csv and no minimum length, which is what every guide
               told people to pass by hand. fast and full stay available. */
            arguments->mode = MODE_LONGEST;
            arguments->sigType = BINARY;
            arguments->outputFormat = CSV;
            arguments->thD = 9000;
            arguments->thDc = 60000;
            arguments->thXh = 290;
            arguments->thDi = 0;
            arguments->thIt = 0.5;
            arguments->numberOfPaths = 0;
            arguments->jobs = 0;
            arguments->filePaths = NULL;
            /* 1, not 49. The score is Hough votes, which say how many frame
               pairs agreed on the alignment, not how long the match is: on
               300 benchmark pairs the old default hid 11 of 60 genuine
               duplicates, each over 900 frames long, at scores of 7 to 45.
               Filtering belongs to the caller, on coverage. */
            arguments->minScore = 1;
            break;

        case ARGP_KEY_END:
            /* Numbers and keywords were checked as they were read. Clamping
               -j to the number of cores happens in main, which is where
               omp_get_num_procs is available. */
            LoggedAssert(arguments->sigType == BINARY,
                "Only binary signatures are supported");

            if (arguments->incrementalFile) {
                AssertFileExistence(arguments->incrementalFile,
                    "Incremental file list not found");
            }

            if (state->arg_num < 2 && !arguments->listFile) {
                slog_error(2, "You should supply at least 2 files");
                argp_usage(state);
            } else if (arguments->listFile){
                AssertFileExistence(arguments->listFile,
                    "List file not found");

                /* Two files in all, so that there is a pair to compare. The
                   entries -n brings count towards that: one source against
                   one candidate is a run, and requiring two in -l alone made
                   tools/find_reuse.py fail on a folder holding one video. */
                unsigned int listed =
                    getNumberOfLinesFromFilename(arguments->listFile);
                unsigned int total = listed;
                if (arguments->incrementalFile)
                    total += getNumberOfLinesFromFilename(
                        arguments->incrementalFile);
                LoggedAssert(listed >= 1 && total >= 2,
                    "File list invalid, at least two entries are required "
                    "between -l and -n");

            } else {
                // First path is executable
                arguments->numberOfPaths = state->arg_num;
                arguments->filePaths = state->argv + state->argc -\
                    arguments->numberOfPaths;

                for (unsigned int i = 0; i < arguments->numberOfPaths; ++i) {
                    checkPathUsable(state, arguments->filePaths[i]);
                    AssertFileExistence(arguments->filePaths[i],
                        "File %s not found, aborting",
                        arguments->filePaths[i]);
                }
            }

            break;
        default: return ARGP_ERR_UNKNOWN;
    }
    return 0;
}



struct arguments
parseArguments(int argc, char **argv) {
    struct arguments arguments;
    error_t result;
    char doc[] = "Compare binary MPEG7 signatures to find visually"\
        " similar videos";
    char args_doc[] = "[FILE1] [FILE2] ...";
    struct argp_option options[] = {
        { "verbosity", 'v', 0, 0, "Increase output verbosity, repeat for more "
            "detail. -v reports progress, -vv adds per-pair detail, -vvv dumps "
            "every frame"},
        { "jobs", 'j', "{int}", 0, "Number of cores to use. Defaults to every "
            "core on the machine. A count above that is reduced to it"},
        { "lookup_mode", 'm', "{fast,full,longest}", 0, "longest, the default, "
            "ranks candidates by how much matched and never stops early, so it "
            "does not settle for a shared opening when a longer match exists "
            "further down the list. full weighs candidates by mean distance "
            "but stops as soon as one reaches both ends. fast stops at the "
            "first candidate that qualifies."},
        { "signature_type", 't', "{xml,binary}", 0, "Only binary is supported"},
        { "minimum_score", 'k', "{int}", 0, "Rows scoring below this are not "
            "printed. The default, 1, prints every candidate that matched; the "
            "score counts the frame pairs that agreed on the alignment, not the "
            "length of the match, so a long match can score low."},
        { "thD", 'd', "{int}", 0, "Threshold to detect one word as similar. "\
            "The default is 9000."},
        { "thDc", 'c', "{int}", 0, "Threshold to detect all words as similar. "\
            "The default is 60000."},
        { "thXh", 'x', "{int}", 0, "Threshold to detect frames as similar. "\
            "The default is 290."},
        { "thDi", 'i', "{int}", 0, "Minimum length in frames a match must have "\
            "to be reported. Shorter candidates are skipped, longer ones are "\
            "reported in full. The default, 0, keeps every length. Before "\
            "build 4 a non-zero value also ended the walk at that length, "\
            "which is why older guides say -i 0."},
        { "thIt", 'b', "{float}", 0, "Required ratio of good frames to all "\
            "frames walked, between 0 and 1. The default is 0.5. A walk stops "\
            "after three bad frames in a row, so the ratio of a reported "\
            "candidate never falls below about 0.17: values up to that keep "\
            "every candidate."},
        { "output_format", 'f', "{csv}", 0, "csv, the only format. The "\
            "beautiful tree was removed in build 8: it assumed rows arrive in "\
            "order, which the parallel loop does not promise."},
        { "file_list", 'l', "file_list", 0, "Specify a list of signature files"},
        { "incremental_file_list", 'n', "incremental_file_list", 0, "Specify a list of signature files that will be matched between each other and the specified files. Use this mode if you DON'T want to rematch every signature in the file list or argument list."},
        { "ledger", 's', "ledger_file", 0, "Record every compared pair in "\
            "this file and skip the pairs already in it, so an interrupted "\
            "run continues instead of starting over. The file is created if "\
            "it does not exist. Keep it and add files to the list to compare "\
            "only what is new. The file also records the build, the settings "\
            "and each input's content, and a resume with any of them changed "\
            "is refused; a ledger without that record, from build 4 or "\
            "earlier or written by hand, is reused with a warning. A resumed "\
            "run prints only the pairs it compares itself: append its output "\
            "to the earlier one, never overwrite it."},
        { 0 }
    };

    struct argp argp = { options, parse_opt, args_doc, doc, 0, 0, 0 };
    result = argp_parse(&argp, argc, argv, 0, 0, &arguments);
    LoggedAssert(!result, "Argument parsing failed");

    return arguments;
};
