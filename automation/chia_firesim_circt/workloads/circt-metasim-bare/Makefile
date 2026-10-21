CC = riscv64-unknown-elf-gcc
SUPPORT_DIR := $(abspath ../../../../software/firemarshal/test/bare)
CFLAGS := -mcmodel=medany -Wall -Wextra -O2 -fno-common -fno-builtin -I$(SUPPORT_DIR)
LDFLAGS := -static -nostdlib -nostartfiles -lgcc

TARGET := circt-metasim-bare
OBJECTS := circt-metasim-bare.o crt.o syscalls.o

# The live Chia gate continues to build the original smoke workload by default.
# Invoke `make suite` to build the new combined and standalone test programs.
.DEFAULT_GOAL := all
.PHONY: all suite validate-spike manifests
all: $(TARGET)

TOOLCHAIN_DIR := $(abspath ../../../../.conda-env/riscv-tools/bin)
ifeq ($(shell command -v $(CC) 2>/dev/null),)
CC := $(TOOLCHAIN_DIR)/riscv64-unknown-elf-gcc
endif
SUITE_ARCH := -march=rv64gc -mabi=lp64d
SUITE_CFLAGS := $(SUITE_ARCH) -mcmodel=medany -O2 -g -Wall -Wextra -Werror \
  -ffreestanding -fno-common -fno-builtin -ffunction-sections -fdata-sections \
  -fno-strict-aliasing -I$(SUPPORT_DIR)
FAME_GROUPS := integer memory control atomics csr traps vm interrupts fp icache workloads uart
FAME_OBJECTS := $(addprefix .fame-build/test_,$(addsuffix .o,$(FAME_GROUPS)))
FAME_RUNTIME := .fame-build/crt.o .fame-build/syscalls.o
FAME_BINARIES := circt-fame-suite circt-fame-rocket-suite $(addprefix circt-fame-,$(FAME_GROUPS))
suite: $(FAME_BINARIES)

.fame-build:
	mkdir -p $@

.fame-build/test_%.o: test_%.c fame_test.h Makefile | .fame-build
	$(CC) $(SUITE_CFLAGS) -c $< -o $@

.fame-build/crt.o: $(SUPPORT_DIR)/crt.S $(SUPPORT_DIR)/encoding.h Makefile | .fame-build
	$(CC) $(SUITE_CFLAGS) -c $< -o $@

.fame-build/syscalls.o: $(SUPPORT_DIR)/syscalls.c $(SUPPORT_DIR)/util.h $(SUPPORT_DIR)/encoding.h Makefile | .fame-build
	$(CC) $(filter-out -Werror -ffunction-sections -fdata-sections,$(SUITE_CFLAGS)) -c $< -o $@

define FAME_BINARY
.fame-build/runner-$(1).o: fame_runner.c fame_test.h Makefile | .fame-build
	$$(CC) $$(SUITE_CFLAGS) -DFAME_TEST_MASK=$(2) -c $$< -o $$@
circt-fame-$(1): .fame-build/runner-$(1).o $$(FAME_OBJECTS) $$(FAME_RUNTIME) fame_link.ld
	$$(CC) $$(SUITE_ARCH) -mcmodel=medany -static -nostdlib -nostartfiles \
      -Wl,--gc-sections,-Map,.fame-build/$(1).map -T fame_link.ld \
      $$(filter %.o,$$^) -lgcc -o $$@
endef
$(eval $(call FAME_BINARY,suite,2047))
$(eval $(call FAME_BINARY,rocket-suite,4095))
$(eval $(call FAME_BINARY,integer,1))
$(eval $(call FAME_BINARY,memory,2))
$(eval $(call FAME_BINARY,control,4))
$(eval $(call FAME_BINARY,atomics,8))
$(eval $(call FAME_BINARY,csr,16))
$(eval $(call FAME_BINARY,traps,32))
$(eval $(call FAME_BINARY,vm,64))
$(eval $(call FAME_BINARY,interrupts,128))
$(eval $(call FAME_BINARY,fp,256))
$(eval $(call FAME_BINARY,icache,512))
$(eval $(call FAME_BINARY,workloads,1024))
$(eval $(call FAME_BINARY,uart,2048))

validate-spike: suite
	python3 validate_spike.py --spike $(TOOLCHAIN_DIR)/spike

manifests: suite
	python3 prepare_workloads.py

$(TARGET): $(OBJECTS)
	$(CC) -T $(SUPPORT_DIR)/link.ld $(LDFLAGS) $^ -o $@

circt-metasim-bare.o: circt-metasim-bare.c
	$(CC) $(CFLAGS) -c $< -o $@

crt.o: $(SUPPORT_DIR)/crt.S
	$(CC) $(CFLAGS) -c $< -o $@

syscalls.o: $(SUPPORT_DIR)/syscalls.c $(SUPPORT_DIR)/util.h $(SUPPORT_DIR)/encoding.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJECTS) $(TARGET)
