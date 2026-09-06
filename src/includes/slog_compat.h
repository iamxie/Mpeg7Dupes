#ifndef MPEG7DUPES_SLOG_COMPAT
#define MPEG7DUPES_SLOG_COMPAT

/*
 * Compatibility layer over slog.
 *
 * This project was written against slog 1.6.x, whose logging entry point was
 *
 *     void slog(int level, int flag, const char *pMsg, ...);
 *
 * wrapped by macros that took a numeric verbosity level as their first
 * argument. slog itself dropped anything above the level handed to slog_init.
 *
 * Current slog has no numeric levels. It exposes
 *
 *     void slog_display(slog_flag_t eFlag, uint8_t nNewLine, const char *pFmt, ...);
 *
 * and filters by a bitmask of enabled tags instead. It also no longer defines
 * slog(), so linking against it failed with "undefined reference to `slog'".
 *
 * Repository history vendored slog 1.6.2's header here and required the library
 * to be checked out at that tag, which is from 2018 and keeps its Makefile in a
 * different place from current slog. A fresh clone therefore did not build
 * unless you already knew which tag to pick.
 *
 * Instead, keep the numeric level in this header, let every tag through to
 * slog, and map the level names onto the tags current slog does have. Call
 * sites are untouched.
 */

#include <slog.h>

/* Stop here rather than partway through the first source file. Built against
 * slog 1.6 this header collides with the macros that version defines and then
 * calls slog_display, which does not exist there, and the compiler reports
 * that as a wall of redefinition warnings followed by an implicit declaration
 * a hundred lines later. Neither says which slog is installed.
 *
 * A machine that built this project before the switch to current slog still
 * has 1.6 in /usr/local, which is exactly where this bites. */
#if !defined(SLOG_VERSION_MAJOR) || !defined(SLOG_VERSION_MINOR) \
    || SLOG_VERSION_MAJOR < 1 \
    || (SLOG_VERSION_MAJOR == 1 && SLOG_VERSION_MINOR < 9)
#error "slog 1.9 or newer is required. The header found is older, most likely \
left in /usr/local by an earlier build. Reinstall it: \
git clone --depth 1 https://github.com/kala13x/slog /tmp/slog && \
cd /tmp/slog && make && sudo make install"
#endif

/* Current slog defines these as level-less macros. Take the names over. */
#undef slog
#undef slog_note
#undef slog_info
#undef slog_warn
#undef slog_debug
#undef slog_error
#undef slog_trace
#undef slog_fatal

/* Highest level that still prints; anything above it is dropped. */
extern int g_slogVerbosity;

/*
 * Old slog_init took a log file name, a config file path, a level and a thread
 * safety flag. Current slog_init takes a tag bitmask in place of the config and
 * level. Enable every tag and filter by level here.
 */
void slog_compat_init(const char *pName, int nLogLevel, int nTdSafe);

#define SLOG_COMPAT_LVL1(x) #x
#define SLOG_COMPAT_LVL2(x) SLOG_COMPAT_LVL1(x)
#define SLOG_COMPAT_LOCATION "<" __FILE__ ":" SLOG_COMPAT_LVL2(__LINE__) "> -- "

#define SLOG_COMPAT_EMIT(LEVEL, TAG, ...)                                     \
    do {                                                                      \
        if ((int) (LEVEL) <= g_slogVerbosity)                                 \
            slog_display((TAG), 1, __VA_ARGS__);                              \
    } while (0)

/*
 * Current slog has no NONE, LIVE or PANIC tag. NOTAG, NOTE and FATAL are the
 * closest matches; the level argument still decides what is shown, so only the
 * label printed alongside the message changes.
 */
#define slog_none(LEVEL, ...)  SLOG_COMPAT_EMIT(LEVEL, SLOG_NOTAG, __VA_ARGS__)
#define slog_live(LEVEL, ...)  SLOG_COMPAT_EMIT(LEVEL, SLOG_NOTE, __VA_ARGS__)
#define slog_info(LEVEL, ...)  SLOG_COMPAT_EMIT(LEVEL, SLOG_INFO, __VA_ARGS__)
#define slog_warn(LEVEL, ...)  SLOG_COMPAT_EMIT(LEVEL, SLOG_WARN, __VA_ARGS__)
#define slog_debug(LEVEL, ...) SLOG_COMPAT_EMIT(LEVEL, SLOG_DEBUG, __VA_ARGS__)

/* These carried the throwing file and line in the original macros. */
#define slog_error(LEVEL, ...) \
    SLOG_COMPAT_EMIT(LEVEL, SLOG_ERROR, SLOG_COMPAT_LOCATION __VA_ARGS__)
#define slog_fatal(LEVEL, ...) \
    SLOG_COMPAT_EMIT(LEVEL, SLOG_FATAL, SLOG_COMPAT_LOCATION __VA_ARGS__)
#define slog_panic(LEVEL, ...) \
    SLOG_COMPAT_EMIT(LEVEL, SLOG_FATAL, SLOG_COMPAT_LOCATION __VA_ARGS__)

#endif
