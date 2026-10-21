/* Bare-metal DRAM layout, with complete section and TLS coverage for GCC. */
OUTPUT_ARCH("riscv")
ENTRY(_start)
SECTIONS
{
  . = 0x80000000;
  .text.init : { KEEP(*(.text.init)) }
  .tohost ALIGN(0x1000) : { KEEP(*(.tohost)) }
  .text : { *(.text .text.*) }
  .rodata : { *(.rodata .rodata.*) }
  .data ALIGN(64) : { *(.data .data.*) *(.got .got.*) }
  .sdata : {
    __global_pointer$ = . + 0x800;
    *(.srodata .srodata.*) *(.sdata .sdata.*)
  }
  .sbss : { *(.sbss .sbss.*) *(.scommon) }
  .bss ALIGN(64) : { *(.bss .bss.*) *(COMMON) }
  .tdata ALIGN(64) : {
    _tls_data = .;
    KEEP(*(.tdata.begin))
    *(.tdata)
    KEEP(*(.tdata.end))
  }
  .tbss : { *(.tbss) KEEP(*(.tbss.end)) }
  _end = .;
}
