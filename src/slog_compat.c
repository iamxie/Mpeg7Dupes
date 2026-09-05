#include "slog_compat.h"

/*
 * Default matches what slog 1.6.x was initialised with before the arguments
 * were parsed, so anything logged during argument parsing still appears.
 */
int g_slogVerbosity = 5;

void
slog_compat_init(const char *pName, int nLogLevel, int nTdSafe) {
    g_slogVerbosity = nLogLevel;
    /* Let every tag through; SLOG_COMPAT_EMIT decides what is actually shown. */
    slog_init(pName, SLOG_FLAGS_ALL, (uint8_t) nTdSafe);
    /*
     * Current slog prints the time without the date by default, where slog
     * 1.6.x always printed both. A full comparison run can span midnight, so
     * ask for the date back.
     */
    slog_date_format_set(SLOG_DATE_FULL);
}
