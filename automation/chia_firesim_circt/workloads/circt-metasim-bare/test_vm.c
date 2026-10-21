#include "fame_test.h"
#include <string.h>

static uint64_t root[512] __attribute__((aligned(4096)));
static uint64_t middle[512] __attribute__((aligned(4096)));
static uint64_t leaves[512] __attribute__((aligned(4096)));
static volatile uint64_t physical_a[512] __attribute__((aligned(4096)));
static volatile uint64_t physical_b[512] __attribute__((aligned(4096)));

static uint64_t pte(const volatile void *p,uint64_t flags)
{
  return (((uint64_t)(uintptr_t)p>>12)<<10)|flags;
}

/* MPRV applies S-mode page translation to data accesses while executing in M. */
static uint64_t virtual_load(uint64_t address)
{
  uint64_t old=FAME_CSR_READ(mstatus),access=(old&~UINT64_C(0x1800))|UINT64_C(0x20800),value;
  __asm__ volatile (".option push\n.option norvc\n\tcsrw mstatus, %1\n"
                    "\tld %0, 0(%2)\n\tcsrw mstatus, %3\n.option pop"
                    : "=&r"(value) : "r"(access),"r"(address),"r"(old) : "memory");
  return value;
}

static void virtual_store(uint64_t address,uint64_t value)
{
  uint64_t old=FAME_CSR_READ(mstatus),access=(old&~UINT64_C(0x1800))|UINT64_C(0x20800);
  __asm__ volatile (".option push\n.option norvc\n\tcsrw mstatus, %0\n"
                    "\tsd %1, 0(%2)\n\tcsrw mstatus, %3\n.option pop"
                    :: "r"(access),"r"(value),"r"(address),"r"(old) : "memory");
}

void fame_vm(void)
{
  const uint64_t va=UINT64_C(0x40000000);
  uint64_t saved_satp=FAME_CSR_READ(satp),saved_pmpaddr=FAME_CSR_READ(pmpaddr0);
  uint64_t saved_pmpcfg=FAME_CSR_READ(pmpcfg0),saved_mie=FAME_CSR_READ(mie);
  FAME_CSR_WRITE(mie,0);
  FAME_CHECK("S mode available",(FAME_CSR_READ(misa)>>18)&1,1);
  memset(root,0,sizeof(root)); memset(middle,0,sizeof(middle)); memset(leaves,0,sizeof(leaves));
  root[1]=pte(middle,1); middle[0]=pte(leaves,1);
  /* Pre-set A/D bits: tests do not assume hardware-managed accessed/dirty bits. */
  leaves[0]=pte(physical_a,0xc7); leaves[1]=pte(physical_b,0xc7);
  for(unsigned i=0;i<512;++i) { physical_a[i]=fame_mix(i+17); physical_b[i]=fame_mix(i+29); }
  FAME_CSR_WRITE(pmpaddr0,UINT64_MAX);
  FAME_CSR_WRITE(pmpcfg0,(saved_pmpcfg&~UINT64_C(255))|0x1f); /* NAPOT RWX */
  uint64_t satp=(UINT64_C(8)<<60)|((uint64_t)(uintptr_t)root>>12);
  FAME_CSR_WRITE(satp,satp);
  __asm__ volatile ("sfence.vma zero, zero" ::: "memory");
  FAME_CHECK("Sv39 satp accepted",FAME_CSR_READ(satp)>>60,8);
  for(unsigned i=0;i<512;++i) {
    FAME_CHECK("Sv39 page A",virtual_load(va+8*i),fame_mix(i+17));
    FAME_CHECK("Sv39 page B",virtual_load(va+4096+8*i),fame_mix(i+29));
  }
  virtual_store(va+8*33,UINT64_C(0xdecafbad12345678));
  FAME_CHECK("translated store",physical_a[33],UINT64_C(0xdecafbad12345678));
  leaves[0]=pte(physical_b,0xc7);
  __asm__ volatile ("sfence.vma %0, zero" :: "r"(va) : "memory");
  FAME_CHECK("TLB remap after sfence",virtual_load(va+8*33),physical_b[33]);
  leaves[0]=pte(physical_b,0xc3); /* Valid, readable, not writable. */
  __asm__ volatile ("sfence.vma zero, zero" ::: "memory");
  fame_arm_trap(15,va,1,4); virtual_store(va,99); fame_finish_trap();
  FAME_CHECK("read-only page unchanged",physical_b[0],fame_mix(29));
  leaves[0]=0;
  __asm__ volatile ("sfence.vma zero, zero" ::: "memory");
  fame_arm_trap(13,va,1,4); (void)virtual_load(va); fame_finish_trap();
  FAME_CSR_WRITE(satp,saved_satp);
  __asm__ volatile ("sfence.vma zero, zero" ::: "memory");
  FAME_CSR_WRITE(pmpcfg0,saved_pmpcfg); FAME_CSR_WRITE(pmpaddr0,saved_pmpaddr);
  FAME_CSR_WRITE(mie,saved_mie);
}
