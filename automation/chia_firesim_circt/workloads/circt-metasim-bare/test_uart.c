#include "fame_test.h"

/* Addresses and clock from the saved FireSimRocketConfig DTS. */
#ifndef FAME_UART_BASE
#define FAME_UART_BASE UINT64_C(0x10020000)
#endif
#ifndef FAME_UART_DIVISOR
#define FAME_UART_DIVISOR 270u /* Golden UARTKey.div=271 target clocks per bit. */
#endif
#ifndef FAME_UART_FIFO_DEPTH
#define FAME_UART_FIFO_DEPTH 256u
#endif
static volatile uint32_t *const uart=(volatile uint32_t *)(uintptr_t)FAME_UART_BASE;

static int transmit(unsigned char c)
{
  for(unsigned retry=0;retry<1000000;++retry) {
    if(!(uart[0]&UINT32_C(0x80000000))) { uart[0]=c; return 1; }
  }
  return 0;
}

void fame_uart(void)
{
  static const char prefix[]="FAME_UART: DATA ";
  static const char alphabet[]="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz\n";
  static const char end[]="FAME_UART: END\n";
  uint32_t saved_control=uart[2],saved_divisor=uart[6];
  uart[6]=FAME_UART_DIVISOR; uart[2]=saved_control|1;
  __asm__ volatile ("fence iorw, iorw" ::: "memory");
  FAME_CHECK("UART TX enabled",uart[2]&1,1);
  /* More than twice the 256-entry FIFO: exercise full/retry behavior. */
  for(unsigned epoch=0;epoch<8;++epoch) {
    for(unsigned i=0;i<sizeof(prefix)-1;++i)
      FAME_CHECK("UART header byte",transmit((unsigned char)prefix[i]),1);
    FAME_CHECK("UART epoch byte",transmit((unsigned char)('0'+epoch)),1);
    FAME_CHECK("UART separator byte",transmit(' '),1);
    for(unsigned i=0;i<sizeof(alphabet)-1;++i)
      FAME_CHECK("UART FIFO accepts ordered byte",transmit((unsigned char)alphabet[i]),1);
  }
  for(unsigned i=0;i<sizeof(end)-1;++i)
    FAME_CHECK("UART end marker byte",transmit((unsigned char)end[i]),1);
  /* Drain the whole FIFO before the host observes tohost exit. */
  uint64_t start=FAME_CSR_READ(mcycle);
  while(FAME_CSR_READ(mcycle)-start<(uint64_t)(FAME_UART_DIVISOR+1)*10*(FAME_UART_FIFO_DEPTH+4))
    __asm__ volatile ("nop");
  uart[2]=saved_control; uart[6]=saved_divisor;
  __asm__ volatile ("fence iorw, iorw" ::: "memory");
}
