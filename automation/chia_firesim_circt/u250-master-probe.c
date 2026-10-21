/* Read-only probe for the U250 Rocket CIRCT checkpoint built 2026-10-08.
 * Addresses were checked against its generated header and built RTL.
 * No register writes, target stepping, or fingerprint bypasses.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv) {
  if (argc > 2) {
    fprintf(stderr, "usage: %s [/dev/xdma0_user]\n", argv[0]);
    return 2;
  }
  const char *path = argc == 2 ? argv[1] : "/dev/xdma0_user";
  int fd = open(path, O_RDONLY | O_SYNC);
  if (fd < 0) {
    perror(path);
    return 2;
  }
  void *base = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0);
  if (base == MAP_FAILED) {
    perror("read-only BAR mmap");
    close(fd);
    return 2;
  }
  volatile const uint32_t *regs = base;
  uint32_t init = 0, read = 0, write = 0;
  int good = 1;
  printf("%s: reading master registers only; expected fingerprint 0x46697265\n", path);
  for (int sample = 0; sample < 3; ++sample) {
    init = regs[0x220 / 4];
    read = regs[0x224 / 4];
    write = regs[0x228 / 4];
    printf("sample %d: INIT_DONE[0x220]=0x%08x "
           "PRESENCE_READ[0x224]=0x%08x PRESENCE_WRITE[0x228]=0x%08x\n",
           sample + 1, init, read, write);
    good &= init == 1 && read == 0x46697265 && write == 0x46697265;
    usleep(10000);
  }
  if (read == 0xdeadbeef && write == 0xdeadbeef)
    puts("Both fingerprint registers contain the FPGA enumeration marker. "
         "Verify the board mapping and reprogram the matching bitstream, "
         "then probe again before running the workload.");
  else if (!good)
    puts("Master values do not match the compiled checkpoint. "
         "Check board/bitstream identity, reset, and AXI control routing; "
         "do not bypass the fingerprint check.");
  else
    puts("Master registers match. Run the slot driver's +check-fingerprint "
         "before retrying the workload.");
  munmap(base, 4096);
  close(fd);
  return good ? 0 : 1;
}
