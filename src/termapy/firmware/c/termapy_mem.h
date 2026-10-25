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
 *                                      "endian": "le", "modify": true,
 *                                      "regions": [{"name": "flash",
 *                                        "start": "0x00000000",
 *                                        "size": 16384, "w": false}]}
 *                                     OK
 *     any failure                 ->  ERR <reason>          (usage | length | range)
 *
 * The module owns ONLY the wire grammar.  How bytes are actually read and
 * written is the platform's, supplied as two callbacks, and which
 * addresses may be read or written is the platform's memory map, a table
 * it hands over beside them.  Reading bytes given (address, length) and
 * writing bytes given (address, length, bytes) is the base case; flat
 * pointers, banked memory, external EEPROM/SPI flash and host test buffers
 * are all just implementations of those two ops.
 *
 * The platform is injected ONCE, as a struct of function pointers that
 * TermapyMem_Init copies: explicit at boot, swappable in a host test, and
 * never threaded through the calls -- one monitor, one link.
 *
 * Integration with a monitor-style command table:
 *
 *     #include "termapy_mem.h"
 *
 *     // The memory map: every address MEM may touch, and whether it may
 *     // write there.  Reads and writes are gated against it before the
 *     // byte ops run, and MEM.INFO reports it.  One small read-only
 *     // region is the safe default; widen it for your part (its flash,
 *     // SRAM, peripheral windows) before you rely on MEM.W.
 *     static const TermapyMem_Region sRegions[] = {
 *         { "flash", 0x00000000u, 0x00004000u, false },   // 16 KB, read-only
 *     };
 *
 *     // Lock/unlock only promise "no one else modifies memory until
 *     // unlock" -- IRQ masking is the bare-metal way, not the contract.
 *     // This naive pair is fine from main-loop context; save/restore
 *     // PRIMASK instead if the monitor can run with IRQs already off.
 *     static void Lock(void)   { __disable_irq(); }
 *     static void Unlock(void) { __enable_irq(); }
 *
 *     static const TermapyMem_Ops sMemOps = {
 *         TermapyMem_DirectRead,     // or your own read(addr, dst, count)
 *         TermapyMem_DirectWrite,    // or your own write(addr, src, count)
 *         Lock, Unlock,              // NULL, NULL = no MEM.M
 *         putchar,                   // or your UART's int put(int)
 *         sRegions,                  // the memory map ...
 *         (uint8_t)(sizeof sRegions / sizeof sRegions[0]),   // ... and its size
 *     };
 *
 *     // At boot, once:
 *     TermapyMem_Init(&sMemOps);
 *
 *     static void MonMemNative(const char *argP)   // table: { "MEM", ... }
 *     {
 *         TermapyMem_Cmd(argP);
 *     }
 *
 * The input string is everything AFTER your dispatcher's command token;
 * the leading "MEM" and "." are optional and case-insensitive, so
 * ".R 0 16", "R 0 16" and a whole "MEM.R 0 16" line all work.
 *
 * No dynamic allocation, no printf; RAM cost is the copied ops struct
 * plus one TERMAPY_MEM_MAX_BLOCK buffer on the stack during MEM.R / MEM.W.
 * The block size, the reported address width and the row width are
 * compile-time knobs (Configuration, below), so a small target can shrink
 * them with -D.
 */

#ifndef TERMAPY_MEM_H
#define TERMAPY_MEM_H

#include <stdbool.h>
#include <stdint.h>

/* -- Configuration (override with -D) ------------------------------------ */

/** Largest MEM.R / MEM.W block in bytes.  Sizes the stack buffer and is
 *  reported as "max_block" by MEM.INFO.  The platform's line buffer must
 *  hold a full MEM.W line: "MEM.W 0x", 8 address digits, a space, three
 *  characters per block byte ("XX "), CR and NUL -- TERMAPY_MEM_LINE_SIZE,
 *  20 + 3 * TERMAPY_MEM_MAX_BLOCK.  Lower this rather than raise the
 *  buffer:
 *
 *      #if MY_RX_BUFFER_SIZE < TERMAPY_MEM_LINE_SIZE
 *      #error "the monitor line buffer cannot hold a full MEM.W line"
 *      #endif
 */
#ifndef TERMAPY_MEM_MAX_BLOCK
#define TERMAPY_MEM_MAX_BLOCK 64u
#endif

/** Address width reported by MEM.INFO, and the digit count of every
 *  printed address.  Parsing is always 32-bit. */
#ifndef TERMAPY_MEM_ADDRESS_BITS
#define TERMAPY_MEM_ADDRESS_BITS 32u
#endif

/** Bytes per MEM.R row.  Wire-visible: the host expects 16, so change it
 *  only together with the host. */
#ifndef TERMAPY_MEM_ROW_BYTES
#define TERMAPY_MEM_ROW_BYTES 16u
#endif

/** The longest MEM.W line, terminator included: what a line buffer must hold. */
#define TERMAPY_MEM_LINE_SIZE (20u + 3u * TERMAPY_MEM_MAX_BLOCK)

#if TERMAPY_MEM_MAX_BLOCK < 1
#error "TERMAPY_MEM_MAX_BLOCK must be at least 1"
#endif
#if TERMAPY_MEM_ROW_BYTES < 1
#error "TERMAPY_MEM_ROW_BYTES must be at least 1"
#endif
#if (TERMAPY_MEM_ADDRESS_BITS > 32) || (TERMAPY_MEM_ADDRESS_BITS < 4) \
	|| (TERMAPY_MEM_ADDRESS_BITS % 4 != 0)
#error "TERMAPY_MEM_ADDRESS_BITS: addresses are uint32_t; use 4..32, a multiple of 4"
#endif

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief One entry in the platform's memory map.
 *
 * A read must lie inside one region; a write must also find it writable.
 * MEM.INFO reports the table, so the host sees the map instead of guessing.
 */
typedef struct {
	const char *nameP;    /* short name for MEM.INFO, e.g. "sram"; NULL = "" */
	uint32_t    start;    /* first address                                    */
	uint32_t    size;     /* length in bytes; 0 matches nothing               */
	bool        writable; /* may MEM.W / MEM.M touch it                       */
} TermapyMem_Region;

/**
 * @brief The platform: byte access, an optional critical section, serial
 *        out, and the memory map that gates access and fills MEM.INFO.
 */
typedef struct {
	/** Read count bytes at addr into dstP.  Called only for a range the
	 *  memory map allows; false still means refused ("ERR range") -- a
	 *  platform may know an address it cannot read without a fault.  The
	 *  platform owns access width. */
	bool (*read)(uint32_t addr, uint8_t *dstP, uint32_t count);

	/** Write count bytes from srcP to addr.  Called only for a writable
	 *  region; false = refused. */
	bool (*write)(uint32_t addr, const uint8_t *srcP, uint32_t count);

	/** Optional critical section bracketing MEM.M's read-modify-write.
	 *  The CONTRACT: nothing else may modify the target word between the
	 *  read and the write-back.  The mechanism is the platform's choice
	 *  -- IRQ mask/restore on bare metal, a mutex or scheduler suspend
	 *  under an RTOS, nothing at all if only this monitor touches the
	 *  memory.  Held only for the read-mask-write of one word (never
	 *  during MEM.R/MEM.W block transfers or serial output).  Both NULL:
	 *  MEM.M answers "ERR usage" and MEM.INFO omits "modify". */
	void (*lock)(void);
	void (*unlock)(void);

	/** Serial output, putchar-compatible.  The return value is ignored:
	 *  a blocking TX has nothing to report, and truncated output shows
	 *  up host-side as an incomplete reply. */
	int (*put)(int c);

	/** The memory map, regionCount entries, retained (not copied) by
	 *  TermapyMem_Init: it must outlive every call, so make it static
	 *  const.  A read must lie inside one region, a write must also find
	 *  it writable, else "ERR range"; MEM.INFO reports it in table order.
	 *  regionCount 0 (or regionsP NULL) means every address answers
	 *  "ERR range". */
	const TermapyMem_Region *regionsP;
	uint8_t regionCount;
} TermapyMem_Ops;

/**
 * @brief Install the platform.  Once, at boot, before any TermapyMem_Cmd.
 *
 * The struct is copied, so a temporary is fine; the region table it
 * points to is not, so that must stay.  read, write and put are required;
 * lock and unlock may both be NULL.  Until this is called, or
 * if a required op is NULL, TermapyMem_Cmd answers nothing at all --
 * there is no output to answer on -- which the host reports as a device
 * without MEM support.
 *
 * @param opsP The platform operations; copied, not retained.
 */
void TermapyMem_Init(const TermapyMem_Ops *opsP);

/**
 * @brief Handle one MEM command line.
 *
 * @param inputP The received line after your command token.
 */
void TermapyMem_Cmd(const char *inputP);

/**
 * @brief Flat-MCU byte access: direct pointers, no gating of their own.
 *
 * Pure width-aware movers: word, then half-word, then byte, where
 * alignment and count allow -- a byte access to a peripheral register is
 * a bus fault on several cores.  Range and permission checks run in the
 * wire layer against TermapyMem_Ops.regionsP BEFORE these are called, so
 * the platform's memory map is the single source of truth.  Always true.
 */
bool TermapyMem_DirectRead(uint32_t addr, uint8_t *dstP, uint32_t count);
bool TermapyMem_DirectWrite(uint32_t addr, const uint8_t *srcP, uint32_t count);

#ifdef __cplusplus
}
#endif

#endif /* TERMAPY_MEM_H */
