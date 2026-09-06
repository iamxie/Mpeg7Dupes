#ifndef VERSION
#define VERSION

/* Bump MPEG7DUPES_BUILD by one in the same commit that changes the binary.
 *
 * The number exists so a saved run can be traced back to the code that made
 * it. Every run logs it, and the benchmark harness keeps that log beside the
 * results, so a result and the build that produced it never drift apart. A
 * comparison whose provenance is unknown has to be run again, which is the
 * cost this is here to avoid.
 *
 * It is not derived from git on purpose: the Docker build excludes .git, so
 * anything read from the repository would be absent in exactly the builds
 * being traced. Kept by hand, it is correct wherever the source goes.
 *
 * A commit touching only documentation does not need a bump. Bumping anyway
 * costs nothing, and being wrong about which build made a result costs a rerun.
 */
#define MPEG7DUPES_VERSION "0.1"
#define MPEG7DUPES_BUILD 2

/* Two levels, because a macro argument is only expanded before stringifying if
 * it passes through a second macro first. One level would give "b
 * MPEG7DUPES_BUILD". */
#define MPEG7DUPES_STR_(x) #x
#define MPEG7DUPES_STR(x) MPEG7DUPES_STR_(x)

#define MPEG7DUPES_VERSION_STRING \
    "v" MPEG7DUPES_VERSION " b" MPEG7DUPES_STR(MPEG7DUPES_BUILD)

#endif
