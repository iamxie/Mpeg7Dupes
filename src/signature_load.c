#include "signature_load.h"
#include "printers.h"
#include <limits.h>

/* Sizes of the pieces of a binary signature in bits, as ffmpeg's signature
   filter writes them. The header runs up to and including NumOfSegments, the
   compression flag sits between the coarse and the fine signatures. Every
   signature in the test corpus is exactly this long. */
#define SIG_HEADER_BITS 274
#define SIG_COARSE_BITS 1344
#define SIG_FLAG_BITS 1
#define SIG_FINE_BITS 689

/* Stops the run with a message naming the file. A signature that cannot be
   read is not a pair that did not match, and reading past the end of a short
   file used to be a segfault with no name in it. */
static void
rejectSignature(const char *filename, const char *why)
{
    slog_fatal(1, "Cannot use signature %s: %s", filename, why);
    exit(1);
}

void
binary_import(StreamContext *sc, const char* filename)
{
    FILE *f = NULL;
    size_t fileLength = 0;
    unsigned int numOfSegments = 0;
    uint8_t *buffer = NULL;
    GetBitContext bitContext = { 0 };
    char why[200];

    slog_debug(6, "Loading signature from: %s", filename);

    Assert(sc);

    f = fopen(filename, "rb");
    if (!f)
        rejectSignature(filename, "cannot open the file");
    /* get_bits uses a signed int bit count. Refuse larger inputs before
       allocation, and measure this same open file rather than reopening it. */
    if (fseek(f, 0, SEEK_END) != 0)
        rejectSignature(filename, "cannot seek the file");
    long length = ftell(f);
    if (length < 0 || fseek(f, 0, SEEK_SET) != 0)
        rejectSignature(filename, "cannot determine the file size");
    if ((unsigned long) length > (INT_MAX - AV_INPUT_BUFFER_PADDING_SIZE * 8) / 8)
        rejectSignature(filename, "the file is too large for the bit reader");
    fileLength = (size_t) length;
    if (fileLength == 0) {
        fclose(f);
        rejectSignature(filename, "the file is empty");
    }
    if (fileLength < (SIG_HEADER_BITS + 7) / 8) {
        fclose(f);
        rejectSignature(filename, "the file is shorter than a signature header");
    }

    buffer = calloc(fileLength + AV_INPUT_BUFFER_PADDING_SIZE, 1);
    LoggedAssert(buffer, "Could not allocate memory buffer");

    // Read entire file into memory
    if (fread(buffer, 1, fileLength, f) != fileLength || ferror(f))
        rejectSignature(filename, "cannot read the complete file");
    // Remove FILE pointer from memory once we're done
    fclose(f);
    f = NULL;

    // BE CAREFUL, THE LENGTH IS SPECIFIED IN BITS NOT BYTES
    if (init_get_bits(&bitContext, buffer, (int) (8 * fileLength)) < 0)
        rejectSignature(filename, "cannot initialize the bit reader");
    // libavcodec

    // Skip the following data:
    // - NumOfSpatial Regions: (32 bits) only 1 supported
    // - SpatialLocationFlag: (1 bit) always the whole image
    // - PixelX_1: (16 bits) always 0
    // - PixelY_1: (16 bits) always 0
    if (get_bits_long(&bitContext, 32) != 1 || get_bits(&bitContext, 1) != 1)
        rejectSignature(filename, "only one explicit spatial region is supported");
    if (get_bits(&bitContext, 16) != 0 || get_bits(&bitContext, 16) != 0)
        rejectSignature(filename, "the spatial region must start at the origin");


	// width - 1, and height - 1
    // PixelX_2: (16 bits) is width - 1
    // PixelY_2: (16 bits) is height - 1
    sc->w = get_bits(&bitContext, 16);
    sc->h = get_bits(&bitContext, 16);
    ++sc->w;
    ++sc->h;

    // StartFrameOfSpatialRegion, always 0
    if (get_bits_long(&bitContext, 32) != 0)
        rejectSignature(filename, "the spatial region must start at frame zero");

    // NumOfFrames
    // it's the number of fine signatures
    sc->lastindex = get_bits_long(&bitContext, 32);

    // sc->time_base.den / sc->time_base.num
    // hoping num is 1, other values are vague
    // den/num might be greater than 16 bit, so cutting it
    //put_bits(&buf, 16, 0xFFFF & (sc->time_base.den / sc->time_base.num));
    // MediaTimeUnit

    sc->time_base.den = get_bits(&bitContext, 16);
	sc->time_base.num = 1;

    // Skip the following data
    // - MediaTimeFlagOfSpatialRegion: (1 bit) always 1
    // - StartMediaTimeOfSpatialRegion: (32 bits) always 0
    if (get_bits(&bitContext, 1) != 1)
        rejectSignature(filename, "the spatial region must carry timestamps");
    uint64_t firstCoarsePts = get_bits_long(&bitContext, 32);

    // EndMediaTimeOfSpatialRegion
    uint64_t lastCoarsePts = get_bits_long(&bitContext, 32);
    if (firstCoarsePts > lastCoarsePts)
        rejectSignature(filename, "the spatial region has reversed timestamps");

    // Coarse signatures
    // numOfSegments = number of coarse signatures
    numOfSegments = get_bits_long(&bitContext, 32);

    // Reading from binary signature return a wrong number of segments
    // numOfSegments = (sc->lastindex + 44)/45;
    //skip_bits(&bitContext, 32);

    /* Everything below reads what the counts promise, so the counts have to
       be backed by bytes before a single one is read or allocated. */
    if (numOfSegments == 0 || sc->lastindex == 0)
        rejectSignature(filename, "it holds no frames");
    if (sc->time_base.den == 0)
        rejectSignature(filename, "its time base is zero");
    {
        uint64_t needBits = SIG_HEADER_BITS
            + (uint64_t) numOfSegments * SIG_COARSE_BITS + SIG_FLAG_BITS
            + (uint64_t) sc->lastindex * SIG_FINE_BITS;
        uint64_t needBytes = (needBits + 7) / 8;
        if (needBytes > fileLength) {
            snprintf(why, sizeof why, "it claims %u coarse and %u fine "
                "signatures, which take %llu bytes, but the file has %zu",
                numOfSegments, sc->lastindex,
                (unsigned long long) needBytes, fileLength);
            rejectSignature(filename, why);
        }
    }

	sc->coarsesiglist = (CoarseSignature*) calloc(numOfSegments,\
            sizeof(CoarseSignature));

	BoundedCoarseSignature *bCoarseList = (BoundedCoarseSignature*)\
        calloc(numOfSegments, sizeof(BoundedCoarseSignature));

    LoggedAssert(sc->coarsesiglist, "Failed to create coarse signature list");
    LoggedAssert(bCoarseList, "Failed to create bounded "\
            "coarse signature list");


    // CoarseSignature loading
    slog_debug(6, "Loading %4d coarse signatures from: %s", numOfSegments,\
        filename);
    for (unsigned int i = 0; i < numOfSegments; ++i) {
        BoundedCoarseSignature *bCs = &bCoarseList[i];
        bCs->cSign = &sc->coarsesiglist[i];

        if (i < numOfSegments - 1)
            bCs->cSign->next = &sc->coarsesiglist[i + 1];

        // each coarse signature is a VSVideoSegment

        // StartFrameOfSegment
        bCs->firstIndex = get_bits_long(&bitContext, 32);
        // EndFrameOfSegment
        bCs->lastIndex = get_bits_long(&bitContext, 32);

        // MediaTimeFlagOfSegment 1 bit, always 1
        if (get_bits(&bitContext, 1) != 1)
            rejectSignature(filename, "a coarse signature has no timestamps");

        // Fine signature pts
        // StartMediaTimeOfSegment 32 bits
        bCs->firstPts = get_bits_long(&bitContext, 32);
        // EndMediaTimeOfSegment 32 bits
        bCs->lastPts = get_bits_long(&bitContext, 32);

        if (bCs->firstIndex > bCs->lastIndex || bCs->lastIndex >= sc->lastindex)
            rejectSignature(filename, "a coarse signature has invalid frame indices");
        if (bCs->firstPts > bCs->lastPts || bCs->firstPts < firstCoarsePts
                || bCs->lastPts > lastCoarsePts)
            rejectSignature(filename, "a coarse signature has invalid timestamps");


		// Bag of words
        for (unsigned int i = 0; i < 5; ++i) {

            // read 243 bits ( = 7 * 32 + 19 = 8 * 28 + 19) into buffer
            for (unsigned int j = 0; j < 30; ++j) {
                // 30*8 bits = 30 bytes
                bCs->cSign->data[i][j] = get_bits(&bitContext, 8);
            }
            bCs->cSign->data[i][30] = get_bits(&bitContext, 3) << 5;
        }
    }
    sc->coarseend = &sc->coarsesiglist[numOfSegments-1];

    // Finesignatures
    // CompressionFlag, only 0 supported
    if (get_bits(&bitContext, 1) != 0)
        rejectSignature(filename, "compressed signatures are not supported");


    sc->finesiglist = (FineSignature*) calloc(sc->lastindex,\
            sizeof(FineSignature));
    LoggedAssert(sc->finesiglist,\
        "Could not allocate FineSignatures memory buffer");

    // Load fine signatures from file
    slog_debug(6, "Loading %4d fine signatures from: %s", sc->lastindex,\
        filename);
    for (unsigned int i = 0; i < sc->lastindex; ++i) {
        FineSignature *fs = &sc->finesiglist[i];

        // MediaTimeFlagOfFrame always 1
        if (get_bits(&bitContext, 1) != 1)
            rejectSignature(filename, "a fine signature has no timestamp");

        // MediaTimeOfFrame (PTS)
        fs->pts = get_bits_long(&bitContext, 32);
        if (fs->pts < firstCoarsePts || fs->pts > lastCoarsePts)
            rejectSignature(filename, "a fine signature timestamp is outside the region");

        // FrameConfidence
        fs->confidence = get_bits(&bitContext, 8);

        // words
        for (unsigned int l = 0; l < 5; l++) {
            fs->words[l] = get_bits(&bitContext, 8);
            if (fs->words[l] > 242)
                rejectSignature(filename, "a fine signature word is outside 0..242");
        }

        // Crashes for some signature, it's a memory adding problems
        // framesignature
        for (unsigned int l = 0; l < SIGELEM_SIZE/5; l++) {
            fs->framesig[l] = get_bits(&bitContext, 8);
            if (fs->framesig[l] > 242)
                rejectSignature(filename, "a packed ternary value is outside 0..242");
        }
    };

    // Sort by frame time (pts)
    qsort(sc->finesiglist, sc->lastindex, sizeof(FineSignature),\
            fineSignatureCmp);

    // Creating FineSignature linked list
    for (unsigned int i = 0; i < sc->lastindex; ++i) {
        FineSignature *fs = &sc->finesiglist[i];
        // Building fine signature list
        // First element prev should be NULL
        // Last element next should be NULL
        fs->next = i + 1 < sc->lastindex ? &fs[1] : NULL;
        fs->prev = i > 0 ? &fs[-1] : NULL;
    }

    // Fine signature ranges DO overlap
    // Assign FineSignatures to CoarseSignature s
    for (unsigned int i = 0; i < numOfSegments; ++i) {
        BoundedCoarseSignature *bCs = &bCoarseList[i];

        // O = n^2 probably it can be done faster
        for (unsigned int j = 0;  j < sc->lastindex  &&\
            sc->finesiglist[j].pts <= bCs->lastPts; ++j) {
            FineSignature *fs = &sc->finesiglist[j];

            if (fs->pts >= bCs->firstPts) {
                // Check if the fragment's pts is inside coarse signature
                // bounds. Upper bound is checked in for loop
                if (!bCs->cSign->first) {
                    bCs->cSign->first = fs;
                }

                if (bCs->cSign->last) {
                    if (bCs->cSign->last->pts <= fs->pts)
                        bCs->cSign->last = fs;
                } else {
                    bCs->cSign->last = fs;
                }
            }

        }
        if (!bCs->cSign->first || !bCs->cSign->last) {
            snprintf(why, sizeof why, "coarse signature %u covers no frame "
                "(pts %llu to %llu)", i, (unsigned long long) bCs->firstPts,
                (unsigned long long) bCs->lastPts);
            rejectSignature(filename, why);
        }
        bCs->cSign->first->index = bCs->firstIndex;
        bCs->cSign->last->index = bCs->lastIndex;
    };

    printStreamContext(sc);
    free(bCoarseList);
    free(buffer);
}

void
signature_unload(StreamContext *sc)
{
    free(sc->coarsesiglist);
    free(sc->finesiglist);
}
