#include <stdint.h>
#include <stdio.h>

/*
 * A small deterministic target program for the CIRCT FireSim metasim gate.
 * The volatile array prevents the compiler from reducing the test to a
 * compile-time constant: the transformed model must execute stores, loads,
 * arithmetic, UART writes, and a clean tohost exit correctly.
 */
static volatile uint64_t scratch[256];

static uint64_t mix(uint64_t value)
{
  value ^= value >> 30;
  value *= UINT64_C(0xbf58476d1ce4e5b9);
  value ^= value >> 27;
  value *= UINT64_C(0x94d049bb133111eb);
  return value ^ (value >> 31);
}

int main(void)
{
  for (uint64_t index = 0; index < 256; ++index)
    scratch[(index * 37) & 255] = mix(UINT64_C(0x123456789abcdef0) +
                                      index * UINT64_C(0x9e3779b97f4a7c15));

  uint64_t checksum = 0;
  for (uint64_t index = 0; index < 256; ++index)
    checksum ^= mix(scratch[index] + index * UINT64_C(0xd6e8feb86659fd93));

  printf("CIRCT_METASIM_BARE: checksum=0x%016lx\n", (unsigned long)checksum);
  if (checksum != UINT64_C(0x4d81038d93425513)) {
    printf("CIRCT_METASIM_BARE: FAIL\n");
    return 1;
  }
  printf("CIRCT_METASIM_BARE: PASS\n");
  return 0;
}
