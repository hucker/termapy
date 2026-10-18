/* Declarations for elf_sample.c.  gDeclared is declared here and defined
   there, which is how GCC comes to describe it with DW_AT_specification. */
#ifndef ELF_SAMPLE_H
#define ELF_SAMPLE_H

#include <stdint.h>

extern uint32_t gDeclared;

uint32_t tick(void);

#endif
