# libavcodec and libavfilter are needed for their headers at compile time, but
# not one of their symbols is referenced, so the linker dropped them anyway.
# Naming them here only misleads.
LIBS  = -lslog -lpthread -lm
DEBUG_LIBS = -lasan
INCLUDES = -I src/includes -I /usr/include/x86_64-linux-gnu
CFLAGS = -Wall -Wextra -std=c11 -fopenmp
CRELEASEFLAGS = -O2 -march=native -floop-unroll-and-jam -fno-trapping-math
CDEBUGFLAGS = -g3 -fsanitize=address -fno-trapping-math

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

# Should be equivalent to your list of C files, if you don't build selectively
SRCS=$(shell find src/ -type f -name '*.c')
HEADERS=$(shell find src/includes -type f -name '*.h')
OBJS=$(addprefix ${BUILD_DIR}/,$(SRCS:src/%.c=%.o))

EXE_PATH ?= "${BIN_DIR}/mpeg7Dupes.elf"

all: release

.PHONY: buildDirs
buildDirs:
	@mkdir -p ${BIN_DIR}
	@mkdir -p $(BUILD_DIR)

.PHONY: release
release:
	@echo Building release
	@$(MAKE) $(MAKEFILE) \
		EXE_PATH="${BIN_DIR}/mpeg7Dupes.elf" link

.PHONY: static
static:
	@echo Building static
	@$(MAKE) $(MAKEFILE) STATIC="1" \
		EXE_PATH="${BIN_DIR}/mpeg7Dupes.elf" link

.PHONY: releaseWithSymbols
releaseWithSymbols:
	@echo Building release
	@$(MAKE) $(MAKEFILE) SYMBOLS="1"\
		EXE_PATH="${BIN_DIR}/mpeg7DupesSymbols.elf" link

.PHONY: debug
debug:
	@echo Building debug
	@$(MAKE) $(MAKEFILE) DEBUG="1"\
		EXE_PATH="${BIN_DIR}/mpeg7DupesDebug.elf" link

.PHONY: optiDebug
optiDebug:
	@echo Building optimized debug
	@$(MAKE) $(MAKEFILE) DEBUG="1" OPTIDEBUG="1" \
		EXE_PATH="${BIN_DIR}/mpeg7DupesOptiDebug.elf" link

.PHONY: nonVerboseDebug
nonVerboseDebug:
	@echo Building non verbose debug
	@$(MAKE) $(MAKEFILE) DEBUG="1" NVDEBUG="1" \
		EXE_PATH="${BIN_DIR}/mpeg7DupesNVDebug.elf" link

.PHONY: compile
compile: buildDirs ${HEADERS} ${OBJS}

# Compares the checked-in fixtures and checks the result against a recorded
# copy, then checks that -s makes a run resumable. Needs a built binary.
.PHONY: test
test: unit
	@echo
	@sh tests/run.sh
	@echo
	@sh tests/ledger.sh

UNIT_SRCS = $(shell find tests/unit -type f -name '*.c')
# main.c has its own main(), and test_lookup.c includes signature_lookup.c
# because the functions it covers have internal linkage, so neither belongs in
# the link. Sources rather than objects on purpose: sharing ${BUILD_DIR} would
# let a `make unit` leave -march=native objects behind for a later `make
# static` to reuse, which is exactly what that target avoids.
UNIT_LIB_SRCS = $(filter-out ${SRC_DIR}/main.c ${SRC_DIR}/signature_lookup.c,${SRCS})

.PHONY: unit
unit: buildDirs
	@echo Building unit tests
	@$(CC) ${CFLAGS} -O2 -I tests/unit -I ${SRC_DIR} ${INCLUDES} \
		-o ${BIN_DIR}/unitTests ${UNIT_SRCS} ${UNIT_LIB_SRCS} ${LIBS}
	@${BIN_DIR}/unitTests

.PHONY: clean
clean:
	@echo "Cleaning files"
	@$(RM) -r $(BUILD_DIR) $(BIN_DIR)
	@echo "Cleaning finished"

$(BUILD_DIR)/%.o: $(SRC_DIR)/%.c
	@# DO NOT change the options order
	@echo Compiling
ifdef DEBUG
ifdef OPTIDEBUG
	$(CC) -c -D DEBUG ${CFLAGS} ${CDEBUGFLAGS} -Og $< -o $@ ${INCLUDES}
else ifdef NVDEBUG
	$(CC) -c -g3 ${CFLAGS} ${CDEBUGFLAGS} $< -o $@ ${INCLUDES}
else
	$(CC) -c -g3 -D DEBUG ${CFLAGS} ${CDEBUGFLAGS}  $< -o $@ ${INCLUDES}
endif
else
ifdef SYMBOLS
	$(CC) -g3 -c  ${CFLAGS} ${CRELEASEFLAGS} $< -O2 -o $@ ${INCLUDES}
else
	$(CC) -c  ${CFLAGS} ${CRELEASEFLAGS} $< -O2 -o $@ ${INCLUDES}
endif
endif

link: compile
	@echo Linking
ifdef DEBUG
	$(CC) -g3 -o ${EXE_PATH} ${OBJS} ${DEBUG_LIBS} ${CFLAGS} ${LIBS}
else
ifdef SYMBOLS
	$(CC) -g3 -o ${EXE_PATH} ${OBJS} ${CFLAGS} ${LIBS}
else
	$(CC) -o ${EXE_PATH} ${OBJS} ${CFLAGS} ${LINK_EXTRA} ${LIBS}
ifdef STATIC
	@strip ${EXE_PATH}
	@echo "Static binary: `ls -lh ${EXE_PATH} | awk '{print $$5}'`, no runtime dependencies"
endif
endif
endif
