/* Pins the behaviour of the two functions every comparison runs through,
 * because both turned out to work differently from what their names and the
 * documented defaults suggest, and that cost a long time to establish. Written
 * down here, the next reader gets it in a second. suiteEvaluate below walks
 * constructed candidates through evaluate_parameters, which is where -b and
 * -i take effect.
 *
 * get_jaccarddist, get_l1dist and evaluate_parameters are static, so this
 * includes the translation unit rather than linking against it.
 * signature_lookup.o is therefore left out of the unit test binary. */

#include "harness.h"

#include "signature_lookup.c"

/* A signature element is a value in 0..242, five ternary digits. Filling with
 * distinct patterns is enough; the exact content does not matter to any of
 * these properties. */
static void
fillFrame(FineSignature *sig, unsigned int seed) {
    for (unsigned int i = 0; i < SIGELEM_SIZE / 5; ++i)
        sig->framesig[i] = (uint8_t) ((seed * 7 + i * 13) % 243);
}

static void
fillCoarse(CoarseSignature *sig, unsigned int seed) {
    for (unsigned int w = 0; w < 5; ++w)
        for (unsigned int i = 0; i < 31; ++i)
            sig->data[w][i] = (uint8_t) ((seed * 11 + w * 5 + i * 3) % 256);
}

void
suiteLookup(void) {
    SignatureContext sc = {0};
    FineSignature a = {0}, b = {0};

    fill_l1distlut(sc.l1distlut);
    fillFrame(&a, 1);
    fillFrame(&b, 2);

    /* The distance reads nothing from sc but the lookup table, which is a
       table of ternary digit distances and does not depend on any option. Every
       threshold is applied after the distance is computed. That is what makes
       it possible to sweep thXh, thDi, thIt and minScore over one recorded run
       instead of comparing again for each value. */
    {
        SignatureContext tight = {0}, loose = {0};

        fill_l1distlut(tight.l1distlut);
        fill_l1distlut(loose.l1distlut);
        tight.thl1 = 1;   tight.thdi = 1;    tight.thit = 0.9;
        loose.thl1 = 999; loose.thdi = 9999; loose.thit = 0.0;

        CHECK_EQ("the frame distance ignores every threshold",
            get_l1dist(&tight, a.framesig, b.framesig),
            get_l1dist(&loose, a.framesig, b.framesig));
    }

    CHECK_EQ("a frame is at distance 0 from itself",
        get_l1dist(&sc, a.framesig, a.framesig), 0);
    CHECK_EQ("the frame distance is symmetric",
        get_l1dist(&sc, a.framesig, b.framesig),
        get_l1dist(&sc, b.framesig, a.framesig));

    /* The coarse filter compares the five words of two coarse signatures by
       Jaccard distance in ten-thousandths, 0 for the same set and 10000 for
       sets that share nothing. A pair is a candidate unless three or more
       words are at or beyond -d, or the five distances together exceed -c.
       At the defaults of 9000 and 60000 that rejects a pair only when three
       of its words share a tenth or less of their bits. It used to be an
       integer division of two popcounts, 0 or 1, that never reached the
       thresholds and, turned down to 1, rejected the pairs that agreed. */
    {
        CoarseSignature first = {0}, second = {0};

        sc.thworddist = 9000;
        sc.thcomposdist = 60000;

        fillCoarse(&first, 1);
        fillCoarse(&second, 1);
        CHECK("two identical coarse signatures pass at the defaults",
            get_jaccarddist(&sc, &first, &second));

        /* Bits in the first fifteen bytes of every word against bits in
           the last fifteen: nothing in common, distance 10000. */
        memset(&first, 0, sizeof first);
        memset(&second, 0, sizeof second);
        for (unsigned int w = 0; w < 5; ++w) {
            memset(first.data[w], 0xFF, 15);
            memset(second.data[w] + 15, 0xFF, 15);
        }
        CHECK("and two that share nothing are rejected",
            !get_jaccarddist(&sc, &first, &second));

        /* Bytes 0 to 19 against bytes 10 to 29: 80 bits shared of the 240
           in either, a third, distance 6667. */
        memset(&first, 0, sizeof first);
        memset(&second, 0, sizeof second);
        for (unsigned int w = 0; w < 5; ++w) {
            memset(first.data[w], 0xFF, 20);
            memset(second.data[w] + 10, 0xFF, 20);
        }
        CHECK("two that share a third of their bits pass at the defaults",
            get_jaccarddist(&sc, &first, &second));
        sc.thworddist = 6000;
        CHECK("and are rejected once -d is below their distance",
            !get_jaccarddist(&sc, &first, &second));
        sc.thworddist = 9000;
        sc.thcomposdist = 30000;
        CHECK("or once -c is below the sum of the five",
            !get_jaccarddist(&sc, &first, &second));
        sc.thcomposdist = 60000;

        memset(&second, 0, sizeof second);
        CHECK("an empty coarse signature against a full one is rejected",
            !get_jaccarddist(&sc, &first, &second));

        /* The direction: a threshold of 1 keeps the identical pair and
           rejects a different one, where it used to be the other way. */
        sc.thworddist = 1;
        fillCoarse(&first, 1);
        fillCoarse(&second, 1);
        CHECK("at thworddist 1 identical coarse signatures still pass",
            get_jaccarddist(&sc, &first, &second));
        fillCoarse(&second, 2);
        CHECK("and different ones are rejected",
            !get_jaccarddist(&sc, &first, &second));
    }
}

/* Two streams of n frames as doubly linked lists, identical except at the
 * positions in badAt, where the second stream carries a different frame.
 * Confidence is high everywhere: a bad frame only counts against the walk's
 * tolerance when it is confident, and a good frame with low confidence counts
 * against the candidate. pts is the position, which is what the walk reads. */
static void
buildStreams(FineSignature *a, FineSignature *b, int n,
    const int *badAt, int badCount) {
    for (int i = 0; i < n; ++i) {
        memset(&a[i], 0, sizeof a[i]);
        memset(&b[i], 0, sizeof b[i]);
        fillFrame(&a[i], 1);
        fillFrame(&b[i], 1);
        a[i].pts = b[i].pts = (uint64_t) i;
        a[i].confidence = b[i].confidence = 5;
        a[i].prev = i > 0 ? &a[i - 1] : NULL;
        a[i].next = i + 1 < n ? &a[i + 1] : NULL;
        b[i].prev = i > 0 ? &b[i - 1] : NULL;
        b[i].next = i + 1 < n ? &b[i + 1] : NULL;
    }
    for (int k = 0; k < badCount; ++k)
        fillFrame(&b[badAt[k]], 2);
}

/* One candidate seeded at frame seed of both streams, walked with the given
 * thresholds. What comes back is what evaluate_parameters stored: a score of
 * 0 means the candidate was rejected. */
static MatchingInfo
walk(int mode, int thdi, double thit, FineSignature *a, FineSignature *b,
    int seed) {
    SignatureContext sc = {0};
    MatchingInfo cand = {0}, best = {0};

    fill_l1distlut(sc.l1distlut);
    sc.mode = mode;
    sc.thl1 = 1;    /* identical frames are at 0, anything else is bad */
    sc.thdi = thdi;
    sc.thit = thit;
    cand.first = &a[seed];
    cand.second = &b[seed];
    cand.framerateratio = 1.0;
    cand.score = 100;
    best.meandist = 99999;
    return evaluate_parameters(&sc, &cand, best);
}

void
suiteEvaluate(void) {
    static FineSignature a[30], b[30];
    MatchingInfo m;

    /* -b reaches the walk as the ratio it was given. Every other frame of
       the second stream is bad, so a walk over all 20 frames has 10 good
       ones in 20, a ratio of exactly 0.5. While thit was an int, 0.51 became
       0 and accepted this candidate. */
    {
        const int odd[] = {1, 3, 5, 7, 9, 11, 13, 15, 17, 19};
        buildStreams(a, b, 20, odd, 10);
        m = walk(MODE_LONGEST, 0, 0.5, a, b, 10);
        CHECK("a walk with 10 good frames in 20 passes thit 0.5", m.score != 0);
        CHECK_EQ("and reports every frame", m.matchframes, 20);
        CHECK_EQ("with 10 good ones", m.goodframes, 10);
        m = walk(MODE_LONGEST, 0, 0.51, a, b, 10);
        CHECK_EQ("the same walk is rejected at thit 0.51", m.score, 0);
    }

    /* One bad frame in 30 is a ratio of 0.967, which 0.9 must keep and 0.99
       must reject: two fractional thresholds, two different answers. */
    {
        const int one[] = {7};
        buildStreams(a, b, 30, one, 1);
        m = walk(MODE_LONGEST, 0, 0.9, a, b, 15);
        CHECK("29 good frames in 30 pass thit 0.9",
            m.score != 0 && m.goodframes == 29 && m.totalframes == 30);
        m = walk(MODE_LONGEST, 0, 0.99, a, b, 15);
        CHECK_EQ("and are rejected by thit 0.99", m.score, 0);
    }

    /* -i is a filter on the finished walk. It used to end the walk as well,
       so a 30-frame match asked for -i 10 came back exactly 10 frames long
       with whole 0. */
    {
        buildStreams(a, b, 30, NULL, 0);
        m = walk(MODE_LONGEST, 10, 0.5, a, b, 15);
        CHECK_EQ("thdi 10 reports the full 30-frame match", m.matchframes, 30);
        CHECK_EQ("and it reaches both ends", m.whole, 1);
        m = walk(MODE_LONGEST, 30, 0.5, a, b, 15);
        CHECK_EQ("thdi 30 keeps a 30-frame match", m.matchframes, 30);
        m = walk(MODE_LONGEST, 31, 0.5, a, b, 15);
        CHECK_EQ("thdi 31 rejects it", m.score, 0);
    }
}

/* A frame whose distance from another is exactly the difference of their
 * positions: element e is 1 in frame i when e < i and 0 otherwise, so frames
 * i and j differ in |i - j| elements, each at ternary distance 1. At thl1 1
 * a frame is good against its own copy and against a neighbour, and bad two
 * or more frames away, which is what makes a walk that drifts out of step
 * fail while one that keeps step, to within the rounding, does not. i is at
 * most SIGELEM_SIZE / 5. */
static void
unaryFrame(FineSignature *sig, int i) {
    memset(sig, 0, sizeof *sig);
    for (unsigned int e = 0; e < SIGELEM_SIZE / 5; ++e)
        sig->framesig[e] = e < (unsigned int) i ? 1 : 0;
}

static void
linkStream(FineSignature *s, int n) {
    for (int i = 0; i < n; ++i) {
        s[i].pts = (uint64_t) i;
        s[i].confidence = 5;
        s[i].prev = i > 0 ? &s[i - 1] : NULL;
        s[i].next = i + 1 < n ? &s[i + 1] : NULL;
    }
}

/* Stream b as a copy of stream a played at another speed: frame j of b
 * shows frame round(j * aPerB) of a, aPerB being the frames of a that pass
 * for each frame of b, which is 1 / frr. */
static void
speedCopy(FineSignature *a, FineSignature *b, int nb, double aPerB) {
    for (int j = 0; j < nb; ++j) {
        int i = (int) (0.5 + j * aPerB);
        memset(&b[j], 0, sizeof b[j]);
        memcpy(b[j].framesig, a[i].framesig, sizeof b[j].framesig);
    }
    linkStream(b, nb);
}

/* One candidate at the given ratio and seeds, walked in longest mode with
 * thl1 1. */
static MatchingInfo
walkFrom(double frr, FineSignature *a, FineSignature *b, int seedA, int seedB) {
    SignatureContext sc = {0};
    MatchingInfo cand = {0}, best = {0};

    fill_l1distlut(sc.l1distlut);
    sc.mode = MODE_LONGEST;
    sc.thl1 = 1;
    sc.thit = 0.5;
    cand.first = &a[seedA];
    cand.second = &b[seedB];
    cand.framerateratio = frr;
    cand.score = 100;
    best.meandist = 99999;
    return evaluate_parameters(&sc, &cand, best);
}

/* The walk keeps the two clips in step at ratios other than 1.0. It used to
 * truncate its step to frr, so below 1.0 the first clip moved two frames for
 * every frame of the second and above it the two moved in lockstep, and a
 * copy at another speed was lost within a few frames. Measured on synthetic
 * clips with build 4: a 1.25x copy came back with 117 of 240 frames. */
void
suiteWalk(void) {
    static FineSignature a[76], b[60], c[60], d[75];
    MatchingInfo m;

    /* b is a at 0.8x speed, 60 frames each showing frame round(1.25 j) of
       a; the ratio, second over first, is 0.8 and the slower clip is b. */
    for (int i = 0; i < 76; ++i)
        unaryFrame(&a[i], i);
    linkStream(a, 76);
    speedCopy(a, b, 60, 1.25);

    m = walkFrom(0.8, a, b, 0, 0);
    CHECK("at ratio 0.8 a walk from the start covers the slower clip",
        m.score != 0 && m.matchframes == 60);
    CHECK_EQ("with every frame good", m.goodframes, m.totalframes);
    CHECK_EQ("and reaches both ends", m.whole, 1);
    m = walkFrom(0.8, a, b, 74, 59);
    CHECK("from the end it covers the clip backwards",
        m.score != 0 && m.matchframes == 60 && m.whole == 1);
    m = walkFrom(0.8, a, b, 25, 20);
    CHECK("and from the middle in both directions",
        m.score != 0 && m.matchframes == 60 && m.whole == 1
        && m.goodframes == m.totalframes);

    /* d is c at 1.25x speed, 75 frames each showing frame round(0.8 j) of
       c; the ratio is 1.25 and the slower clip is c. */
    for (int i = 0; i < 60; ++i)
        unaryFrame(&c[i], i);
    linkStream(c, 60);
    speedCopy(c, d, 75, 0.8);

    m = walkFrom(1.25, c, d, 0, 0);
    CHECK("at ratio 1.25 a walk from the start covers the slower clip",
        m.score != 0 && m.matchframes == 60 && m.whole == 1);
    m = walkFrom(1.25, c, d, 59, 74);
    CHECK("from the end it covers it backwards",
        m.score != 0 && m.matchframes == 60 && m.whole == 1);
    m = walkFrom(1.25, c, d, 20, 25);
    CHECK("and from the middle in both directions",
        m.score != 0 && m.matchframes == 60 && m.whole == 1
        && m.goodframes == m.totalframes);

    /* At 1.0 nothing changed: a against its own copy, 76 frames. */
    speedCopy(a, b, 60, 1.0);
    m = walkFrom(1.0, a, b, 30, 30);
    CHECK("at ratio 1.0 a walk covers the copy as before",
        m.score != 0 && m.matchframes == 60 && m.whole == 1);
}

/* The Hough accumulator is 181 offsets wide, and the scan for candidates
 * used to stop at the middle of it, so an alignment whose frame in the
 * second clip sits later than its frame in the first, a positive offset, was
 * never proposed from the segment pair that voted for it. It was found, if
 * at all, from a neighbouring segment pair where the same alignment appeared
 * with a negative offset, and until then a wrong cell could win. */
void
suiteHough(void) {
    static FineSignature a[90], b[90];
    SignatureContext sc = {0};
    MatchingInfo *cands;
    int found, negative;

    fill_l1distlut(sc.l1distlut);
    sc.thl1 = 1;

    /* a: ninety distinct frames. b: a delayed by twenty, its first twenty
       frames being a flat filler, so frame i of a is frame i + 20 of b. */
    for (int i = 0; i < 90; ++i) {
        memset(&a[i], 0, sizeof a[i]);
        fillFrame(&a[i], 1000 + i);
    }
    linkStream(a, 90);
    for (int k = 0; k < 90; ++k) {
        memset(&b[k], 0, sizeof b[k]);
        if (k >= 20)
            memcpy(b[k].framesig, a[k - 20].framesig, sizeof b[k].framesig);
        else
            memset(b[k].framesig, 200 + (k & 1), sizeof b[k].framesig);
    }
    linkStream(b, 90);

    cands = get_matching_parameters(&sc, &a[0], &b[0]);
    found = negative = 0;
    for (MatchingInfo *c = cands; c; c = c->next) {
        if (c->framerateratio == 1.0 && (c->offset == 20 || c->offset == 19))
            found = 1;
        if (c->offset < 0)
            negative = 1;
    }
    CHECK("a copy delayed by twenty frames is a candidate at ratio 1.0, offset 20",
        found);
    CHECK("and nothing at a negative offset is proposed for it", !negative);
    sll_free(cands);

    /* The other way round: b runs twenty frames ahead of a. */
    for (int k = 0; k < 90; ++k) {
        memset(&b[k], 0, sizeof b[k]);
        if (k < 70)
            memcpy(b[k].framesig, a[k + 20].framesig, sizeof b[k].framesig);
        else
            memset(b[k].framesig, 200 + (k & 1), sizeof b[k].framesig);
    }
    linkStream(b, 90);
    cands = get_matching_parameters(&sc, &a[0], &b[0]);
    found = 0;
    for (MatchingInfo *c = cands; c; c = c->next)
        if (c->framerateratio == 1.0 && (c->offset == -20 || c->offset == -21))
            found = 1;
    CHECK("a copy twenty frames ahead is a candidate at offset -20", found);
    sll_free(cands);
}

/* Among candidates of the same length in longest mode the closer one wins,
 * whichever came up first. It used to be the first one evaluated, which is
 * whatever order the search happened to produce. */
void
suiteTie(void) {
    static FineSignature a[30], b[30], c[30], d[30];
    SignatureContext sc = {0};
    MatchingInfo close = {0}, far = {0}, best = {0}, m;

    fill_l1distlut(sc.l1distlut);
    sc.mode = MODE_LONGEST;
    sc.thl1 = 1;
    sc.thit = 0.5;

    /* Two candidates of thirty frames, each reaching both ends: one over
       identical streams, mean distance 0; one over streams a distance of 1
       apart on every frame, since 7 and 8 are 021 and 022 in ternary. */
    buildStreams(a, b, 30, NULL, 0);
    buildStreams(c, d, 30, NULL, 0);
    for (int i = 0; i < 30; ++i)
        d[i].framesig[0] = 8;
    close.first = &a[15];
    close.second = &b[15];
    close.framerateratio = 1.0;
    close.score = 100;
    far.first = &c[15];
    far.second = &d[15];
    far.framerateratio = 1.0;
    far.score = 50;

    close.next = &far;
    far.next = NULL;
    best.meandist = 99999;
    m = evaluate_parameters(&sc, &close, best);
    CHECK("the closer of two equally long candidates wins when it comes first",
        m.matchframes == 30 && m.meandist == 0.0 && m.score == 100);

    far.next = &close;
    close.next = NULL;
    memset(&best, 0, sizeof best);
    best.meandist = 99999;
    m = evaluate_parameters(&sc, &far, best);
    CHECK("and when it comes second",
        m.matchframes == 30 && m.meandist == 0.0 && m.score == 100);
}
