/* Pins the behaviour of the two functions every comparison runs through,
 * because both turned out to work differently from what their names and the
 * documented defaults suggest, and that cost a long time to establish. Written
 * down here, the next reader gets it in a second.
 *
 * get_jaccarddist and get_l1dist are static, so this includes the translation
 * unit rather than linking against it. signature_lookup.o is therefore left
 * out of the unit test binary. */

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

    /* The coarse filter divides two popcounts as integers, and the union is
       never smaller than the intersection, so the result is only ever 0 or 1.
       Against the documented defaults of 9000 and 60000 it can never trip,
       which means the filter accepts every pair of coarse signatures and does
       no filtering at all. Every pair therefore reaches the expensive stage. */
    {
        CoarseSignature first = {0}, second = {0};

        sc.thworddist = 9000;
        sc.thcomposdist = 60000;

        fillCoarse(&first, 1);
        fillCoarse(&second, 2);
        CHECK("at the default thresholds two different coarse signatures pass",
            get_jaccarddist(&sc, &first, &second));

        fillCoarse(&second, 1);
        CHECK("and so do two identical ones",
            get_jaccarddist(&sc, &first, &second));

        for (unsigned int w = 0; w < 5; ++w)
            for (unsigned int i = 0; i < 31; ++i)
                second.data[w][i] = 0;
        CHECK("and so does an empty one",
            get_jaccarddist(&sc, &first, &second));

        /* Turned down to 1 the test does fire, and it fires on the pairs that
           match: the value is a similarity, so requiring it to stay below the
           threshold rejects the coarse signatures that agree. Lowering -d and
           -c makes the filter throw away the best candidates, not the worst. */
        sc.thworddist = 1;
        sc.thcomposdist = 60000;
        fillCoarse(&second, 1);
        CHECK("at thworddist 1 two identical coarse signatures are rejected",
            !get_jaccarddist(&sc, &first, &second));
    }
}
