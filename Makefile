# libavcodec and libavfilter are needed for their headers at compile time, but
# not one of their symbols is referenced, so the linker dropped them anyway.
# Naming them here only misleads.
LIBS  = -lslog -lpthread -lm
DEBUG_LIBS = -lasan
INCLUDES = -I src/includes -I /usr/include/x86_64-linux-gnu
CFLAGS = -Wall -Wextra -std=c11 -fopenmp
CRELEASEFLAGS = -O2 -march=native -floop-unroll-and-jam -fno-trapping-math
CDEBUGFLAGS = -g3 -fsanitize=address -fno-trapping-math
# Writes a .d file beside each object naming the headers it was compiled from,
# so that editing a header rebuilds whatever includes it.
DEPFLAGS = -MMD -MP

# `make static` produces a binary that runs anywhere, with libslog, glibc and
# libgomp all linked in. -march=native is dropped on purpose: it targets the
# CPU that happens to build the image, and a binary carrying instructions the
# destination lacks dies with SIGILL, which defeats the point.
ifdef STATIC
CRELEASEFLAGS = -O2 -floop-unroll-and-jam -fno-trapping-math
LINK_EXTRA = -static
endif
BUILD_DIR = build
BIN_DIR = bin
SRC_DIR = src

# Every set of compiler flags is a variant with its own object directory under
# build/, so a `make release` can never hand its -march=native objects to a
# later `make static`, and a debug object never ends up in a release link. The
# variant also names the binary, which each target used to pass in by hand.
ifdef DEBUG
ifdef OPTIDEBUG
VARIANT = optiDebug
EXE_NAME = mpeg7DupesOptiDebug.elf
else ifdef NVDEBUG
VARIANT = nonVerboseDebug
EXE_NAME = mpeg7DupesNVDebug.elf
else
VARIANT = debug
EXE_NAME = mpeg7DupesDebug.elf
endif
else ifdef SYMBOLS
VARIANT = releaseWithSymbols
EXE_NAME = mpeg7DupesSymbols.elf
else ifdef STATIC
VARIANT = static
EXE_NAME = mpeg7Dupes.elf
else
VARIANT = release
EXE_NAME = mpeg7Dupes.elf
endif

OBJ_DIR = $(BUILD_DIR)/$(VARIANT)
EXE_PATH ?= $(BIN_DIR)/$(EXE_NAME)

# Should be equivalent to your list of C files, if you don't build selectively
SRCS=$(shell find $(SRC_DIR) -type f -name '*.c')
OBJS=$(addprefix $(OBJ_DIR)/,$(SRCS:$(SRC_DIR)/%.c=%.o))
DEPS=$(OBJS:.o=.d)

all: release

$(OBJ_DIR) $(BIN_DIR):
	@mkdir -p $@

.PHONY: buildDirs
buildDirs: | $(OBJ_DIR) $(BIN_DIR)

.PHONY: release
release:
	@echo Building release
	@$(MAKE) link

.PHONY: static
static:
	@echo Building static
	@$(MAKE) STATIC="1" link

.PHONY: releaseWithSymbols
releaseWithSymbols:
	@echo Building release
	@$(MAKE) SYMBOLS="1" link

.PHONY: debug
debug:
	@echo Building debug
	@$(MAKE) DEBUG="1" link

.PHONY: optiDebug
optiDebug:
	@echo Building optimized debug
	@$(MAKE) DEBUG="1" OPTIDEBUG="1" link

.PHONY: nonVerboseDebug
nonVerboseDebug:
	@echo Building non verbose debug
	@$(MAKE) DEBUG="1" NVDEBUG="1" link

.PHONY: compile
compile: ${OBJS}

# Builds the binary the shell suites run, then runs everything: the C unit
# tests, the fixture comparison, the ledger check and the Python tool suites.
# The variant is whatever the variables select, so `make test` builds and tests
# the release binary and `make test STATIC=1` the static one; CI uses the
# latter to test the binary it is about to publish rather than a release build
# that would overwrite it. Under -j the build and the unit tests run side by
# side and the suites wait for both.
#
# tools.sh uses MPEG7DUPES for loader/ledger integration tests and needs no
# ffmpeg. It skips itself when there is no interpreter,
# unless REQUIRE_PYTHON=1, which CI sets so that a runner without Python fails
# instead of quietly testing less.
.PHONY: test
test: link unit
	@echo
	@MPEG7DUPES=$(abspath $(EXE_PATH)) sh tests/run.sh
	@echo
	@MPEG7DUPES=$(abspath $(EXE_PATH)) sh tests/ledger.sh
	@echo
	@MPEG7DUPES=$(abspath $(EXE_PATH)) sh tests/cli.sh
	@echo
	@MPEG7DUPES=$(abspath $(EXE_PATH)) sh tests/tools.sh

# Real ffmpeg tests: short/multitrack/rotated input contracts and three clips through
# tools/find_reuse.py, twice, the second time without reading any video. Not
# part of `test`, which is documented to need no ffmpeg; CI runs both.
# REQUIRE_FFMPEG=1 turns the skip on a machine without ffmpeg into a failure.
.PHONY: smoke
smoke: link
	@MPEG7DUPES=$(abspath $(EXE_PATH)) sh tests/smoke.sh

UNIT_SRCS = $(shell find tests/unit -type f -name '*.c')
# main.c has its own main(), and test_lookup.c includes signature_lookup.c
# because the functions it covers have internal linkage, so neither belongs in
# the link. Sources rather than objects on purpose: the test binary has its own
# flags and include path, so no variant's objects are the right ones.
UNIT_LIB_SRCS = $(filter-out ${SRC_DIR}/main.c ${SRC_DIR}/signature_lookup.c,${SRCS})

.PHONY: unit
UNIT_FLAGS = -O2
ifdef DEBUG
UNIT_FLAGS += $(CDEBUGFLAGS)
endif
unit: | $(BIN_DIR)
	@echo Building unit tests
	@$(CC) ${CFLAGS} $(UNIT_FLAGS) -I tests/unit -I ${SRC_DIR} ${INCLUDES} \
		-o ${BIN_DIR}/unitTests ${UNIT_SRCS} ${UNIT_LIB_SRCS} ${LIBS}
	@${BIN_DIR}/unitTests

.PHONY: clean
clean:
	@echo "Cleaning files"
	@$(RM) -r $(BUILD_DIR) $(BIN_DIR)
	@echo "Cleaning finished"

$(OBJ_DIR)/%.o: $(SRC_DIR)/%.c | $(OBJ_DIR)
	@# DO NOT change the options order
	@echo Compiling $<
ifdef DEBUG
ifdef OPTIDEBUG
	$(CC) -c ${DEPFLAGS} -D DEBUG ${CFLAGS} ${CDEBUGFLAGS} -Og $< -o $@ ${INCLUDES}
else ifdef NVDEBUG
	$(CC) -c ${DEPFLAGS} -g3 ${CFLAGS} ${CDEBUGFLAGS} $< -o $@ ${INCLUDES}
else
	$(CC) -c ${DEPFLAGS} -g3 -D DEBUG ${CFLAGS} ${CDEBUGFLAGS}  $< -o $@ ${INCLUDES}
endif
else
ifdef SYMBOLS
	$(CC) -g3 -c ${DEPFLAGS} ${CFLAGS} ${CRELEASEFLAGS} $< -O2 -o $@ ${INCLUDES}
else
	$(CC) -c ${DEPFLAGS} ${CFLAGS} ${CRELEASEFLAGS} $< -O2 -o $@ ${INCLUDES}
endif
endif

# The binary is linked inside the variant's directory and copied to EXE_PATH,
# because release and static share bin/mpeg7Dupes.elf: linked there directly,
# a `make release` after a `make static` would find a binary newer than its
# objects and leave the static one in place.
VARIANT_EXE = $(OBJ_DIR)/$(EXE_NAME)

$(VARIANT_EXE): ${OBJS}
	@echo Linking
ifdef DEBUG
	$(CC) -g3 -o $@ ${OBJS} ${DEBUG_LIBS} ${CFLAGS} ${LIBS}
else
ifdef SYMBOLS
	$(CC) -g3 -o $@ ${OBJS} ${CFLAGS} ${LIBS}
else
	$(CC) -o $@ ${OBJS} ${CFLAGS} ${LINK_EXTRA} ${LIBS}
ifdef STATIC
	@strip $@
	@echo "Static binary: `ls -lh $@ | awk '{print $$5}'`, no runtime dependencies"
endif
endif
endif

.PHONY: link
link: $(VARIANT_EXE) | $(BIN_DIR)
	@cp -f $(VARIANT_EXE) $(EXE_PATH)

-include $(DEPS)
