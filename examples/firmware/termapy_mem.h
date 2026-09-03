/**
 * @file termapy_mem.h
 * @brief Reference implementation of the termapy native memory spec.
 *
 * The device side of termapy's /mem.* commands (help topic "memory"):
 *
 *     MEM.R <addr> <len>          ->  <ADDR>: <XX XX ...>   (rows of 16 bytes)
 *                                     OK
 *     MEM.W <addr> <hex>          ->  OK
 *     MEM.M <addr> <and> <or>     ->  <ADDR>: <old word bytes>
 *                                     OK                    (atomic masked write)
 *     MEM.INFO                    ->  {"max_block": 64, "address_bits": 32,
 *                                      "endian": "le", "modify": true}
 *                                     OK
 *     any failure                 ->  ERR <reason>          (usage | length | range)
 *
 * The module owns ONLY the wire grammar.  How bytes are actually read and
 * written -- and which addresses are allowed -- is the platform's, supplied
 * as two callbacks.  That is the whole design: reading bytes given
 * (address, length) and writing bytes given (address, length, bytes) is
 * the base case; flat pointers, banked memory, external EEPROM/SPI flash
 * and host test buffers are all just implementations of those two ops.
 *
 * Integration with a monitor-style command table:
 *
 *     #include "termapy_mem.h"
 *
 *     static void Lock(void)   { __disable_irq(); }
 *     static void Unlock(void) { __enable_irq(); }
 *
 *     static const TermapyMem_Ops sMemOps = {
 *         TermapyMem_DirectRead,     // or your own read(addr, dst, count)
 *         TermapyMem_DirectWrite,    // or your own write(addr, src, count)
 *         Lock, Unlock,              // NULL, NULL = no MEM.M
 *         putchar,                   // or your UART's int put(int)
 *     };
 *
 *     static void MonMemNative(const char *argP)   // table: { "MEM", ... }
 *     {
 *         TermapyMem_Cmd(argP, &sMemOps);
 *     }
 *
 * The input string is everything AFTER your dispatcher's command token;
 * the leading "MEM" and "." are optional and case-insensitive, so
 * ".R 0 16", "R 0 16" and a whole "MEM.R 0 16" line all work.
 *
 * No dynamic allocation, no printf; RAM cost is one TERMAPY_MEM_MAX_BLOCK
 * buffer on the stack during a write, a 16-byte row during a read.
 */

#ifndef TERMAPY_MEM_H
#define TERMAPY_MEM_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief The platform: byte access, an optional critical section, serial out.
 */
typedef struct {
	/** Read count bytes at addr into dstP.  False = unmapped/refused
	 *  ("ERR range").  The platform owns range policy and access width. */
	bool (*read)(uint32_t addr, uint8_t *dstP, uint32_t count);

	/** Write count bytes from srcP to addr.  False = unmapped/refused. */
	bool (*write)(uint32_t addr, const uint8_t *srcP, uint32_t count);

	/** Optional critical section bracketing MEM.M's read-modify-write
	 *  (IRQ mask/restore).  Both NULL: MEM.M answers "ERR usage" and
	 *  MEM.INFO omits "modify". */
	void (*lock)(void);
	void (*unlock)(void);

	/** Serial output, putchar-compatible.  The return value is ignored:
	 *  a blocking TX has nothing to report, and truncated output shows
	 *  up host-side as an incomplete reply. */
	int (*put)(int c);
} TermapyMem_Ops;

/**
 * @brief Handle one MEM command line.
 *
 * @param inputP The received line after your command token.
 * @param opsP   The platform operations (must outlive the call only).
 */
void TermapyMem_Cmd(const char *inputP, const TermapyMem_Ops *opsP);

/**
 * @brief Flat-MCU byte access: direct pointers gated by a region table.
 *
 * The region table lives in termapy_mem.c and is THE part you edit for
 * your part's memory map.  Reads and writes use the natural width where
 * alignment and count allow (word, then half-word, then byte) -- a byte
 * access to a peripheral register is a bus fault on several cores.
 */
bool TermapyMem_DirectRead(uint32_t addr, uint8_t *dstP, uint32_t count);
bool TermapyMem_DirectWrite(uint32_t addr, const uint8_t *srcP, uint32_t count);

#ifdef __cplusplus
}
#endif

#endif /* TERMAPY_MEM_H */
