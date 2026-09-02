/**
 * @file termapy_mem.h
 * @brief Reference implementation of the termapy native memory spec.
 *
 * The device side of termapy's /mem.* commands (help topic "memory"):
 *
 *     MEM.R <addr> <len>   ->  <ADDR>: <XX XX ...>   (rows of 16 bytes)
 *                              OK
 *     MEM.W <addr> <hex>   ->  OK
 *     MEM.INFO             ->  {"max_block": 64, "address_bits": 32, "endian": "le"}
 *                              OK
 *     any failure          ->  ERR <reason>          (usage | length | range)
 *
 * Integration: route the received command line to TermapyMem_Cmd() with a
 * function that writes one byte out the serial port.  The input string is
 * everything AFTER your dispatcher's command token -- all of these work:
 *
 *     { "MEM.R",    "<addr> <len>", ..., MonMemRead  }   -> TermapyMem_Cmd("R <args>", put)
 *     { "MEM",      "...",          ..., MonMemNative}   -> TermapyMem_Cmd(argP, put)   ("." + verb + args)
 *     whole line                                         -> TermapyMem_Cmd("MEM.R <args>", put)
 *
 * The leading "MEM" and "." are optional and case-insensitive, so pass
 * whatever your parser has left over.
 *
 * No dynamic allocation, no printf; RAM cost is one TERMAPY_MEM_MAX_BLOCK
 * buffer on the stack during a write.  Edit the region table in
 * termapy_mem.c for your part's memory map before shipping.
 */

#ifndef TERMAPY_MEM_H
#define TERMAPY_MEM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Writes one byte out the serial port (blocking is fine). */
typedef void (*TermapyMem_PutByteFn)(uint8_t byte);

/**
 * @brief Handle one MEM command line.
 *
 * @param inputP    The received line after your command token: "R 0 16",
 *                  ".W 20000000 A5", "INFO", or the whole "MEM.R 0 16".
 * @param putByteFn Serial output, one byte at a time.
 */
void TermapyMem_Cmd(const char *inputP, TermapyMem_PutByteFn putByteFn);

#ifdef __cplusplus
}
#endif

#endif /* TERMAPY_MEM_H */
