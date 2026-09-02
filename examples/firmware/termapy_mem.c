/**
 * @file termapy_mem.c
 * @brief Reference implementation of the termapy native memory spec.
 *
 * See termapy_mem.h for the wire contract and integration notes, and
 * termapy's "memory" help topic for the normative spec.  Portable C99;
 * only <stdint.h>, <stdbool.h>, <stddef.h> and memcpy are used.
 *
 * Safety properties, in order of importance:
 *
 * - Nothing outside the region table is ever dereferenced: every access
 *   is gated by RangeOk() BEFORE the first load or store, so a typo'd
 *   address answers "ERR range" instead of HardFaulting the monitor.
 * - Accesses use the natural width where alignment and count allow
 *   (word, then half-word, then byte).  A byte access to a peripheral
 *   register is a bus fault on several cores (PIC32, some Cortex-M IPs);
 *   reading or writing 4 aligned bytes at a time avoids that.  To poke a
 *   register, write all 4 bytes (termapy's /mem.write does).
 * - Reads and writes are NOT atomic with respect to interrupts.  A
 *   read-modify-write done from the host (termapy's bit operations) can
 *   race an ISR that touches the same register; that caveat belongs in
 *   your project notes, not in code cleverness here.
 *
 * Footprint: ~700 bytes of flash on a Cortex-M0+ at -Os; stack use is
 * TERMAPY_MEM_MAX_BLOCK bytes during MEM.W, a 16-byte row during MEM.R.
 */

#include "termapy_mem.h"

#include <stdbool.h>
#include <stddef.h>
#include <string.h>

/* ── Configuration (override with -D, or edit) ─────────────────────────── */

#ifndef TERMAPY_MEM_MAX_BLOCK
#define TERMAPY_MEM_MAX_BLOCK 64u   /* largest read/write per exchange   */
#endif

#ifndef TERMAPY_MEM_ADDRESS_BITS
#define TERMAPY_MEM_ADDRESS_BITS 32u
#endif

#define ROW_BYTES 16u               /* bytes per MEM.R output row        */

/**
 * The memory map: the ONLY addresses this module will touch.
 * >>> EDIT FOR YOUR PART before shipping. <<<
 * The defaults are a Cortex-M-style map (flash at 0, SRAM, peripherals).
 */
static const struct { uint32_t start; uint32_t size; } sRegionsA[] = {
	{ 0x00000000u, 0x00080000u },   /* flash   512 KB                    */
	{ 0x20000000u, 0x00010000u },   /* SRAM     64 KB                    */
	{ 0x40000000u, 0x03000000u },   /* peripherals                       */
};

/* ── Output helpers (everything goes through the one callback) ─────────── */

static void PutStr(TermapyMem_PutByteFn put, const char *stringP)
{
	while (*stringP != '\0')
	{
		put((uint8_t)*stringP++);
	}
}

static void PutCrlf(TermapyMem_PutByteFn put)
{
	put((uint8_t)'\r');
	put((uint8_t)'\n');
}

/** Upper-case hex, fixed digit count, MSB first. */
static void PutHex(TermapyMem_PutByteFn put, uint32_t value, uint8_t digits)
{
	while (digits-- > 0u)
	{
		uint8_t nibble = (uint8_t)((value >> (4u * digits)) & 0xFu);
		put((uint8_t)(nibble < 10u ? ('0' + nibble) : ('A' + nibble - 10u)));
	}
}

static void PutDec(TermapyMem_PutByteFn put, uint32_t value)
{
	char buf[10];
	uint8_t n = 0u;
	do
	{
		buf[n++] = (char)('0' + (value % 10u));
		value /= 10u;
	} while (value != 0u);
	while (n-- > 0u)
	{
		put((uint8_t)buf[n]);
	}
}

static void PutErr(TermapyMem_PutByteFn put, const char *reasonP)
{
	PutStr(put, "ERR ");
	PutStr(put, reasonP);
	PutCrlf(put);
}

static void PutOk(TermapyMem_PutByteFn put)
{
	PutStr(put, "OK");
	PutCrlf(put);
}

/* ── Input helpers ─────────────────────────────────────────────────────── */

static void SkipSpaces(const char **cursorPP)
{
	while (**cursorPP == ' ')
	{
		(*cursorPP)++;
	}
}

static int HexVal(char c)
{
	if (c >= '0' && c <= '9') { return c - '0'; }
	if (c >= 'a' && c <= 'f') { return c - 'a' + 10; }
	if (c >= 'A' && c <= 'F') { return c - 'A' + 10; }
	return -1;
}

/** Hex number, optional 0x prefix.  False when no digits follow. */
static bool ParseHexU32(const char **cursorPP, uint32_t *outP)
{
	const char *p = *cursorPP;
	if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) { p += 2; }
	uint32_t value = 0u;
	bool any = false;
	int digit;
	while ((digit = HexVal(*p)) >= 0)
	{
		value = (value << 4) | (uint32_t)digit;
		p++;
		any = true;
	}
	if (!any) { return false; }
	*cursorPP = p;
	*outP = value;
	return true;
}

/** Decimal number.  False when no digits follow. */
static bool ParseDecU32(const char **cursorPP, uint32_t *outP)
{
	const char *p = *cursorPP;
	uint32_t value = 0u;
	bool any = false;
	while (*p >= '0' && *p <= '9')
	{
		value = value * 10u + (uint32_t)(*p - '0');
		p++;
		any = true;
	}
	if (!any) { return false; }
	*cursorPP = p;
	*outP = value;
	return true;
}

/* ── The memory map gate ───────────────────────────────────────────────── */

/** True when [addr, addr+count) lies inside ONE region of the table. */
static bool RangeOk(uint32_t addr, uint32_t count)
{
	uint32_t end = addr + count - 1u;
	if (end < addr) { return false; }   /* wrap-around */
	for (size_t i = 0u; i < sizeof(sRegionsA) / sizeof(sRegionsA[0]); i++)
	{
		uint32_t start = sRegionsA[i].start;
		uint32_t last = start + sRegionsA[i].size - 1u;
		if (addr >= start && end <= last) { return true; }
	}
	return false;
}

/* ── Width-aware copies (bus-fault safe on peripheral windows) ─────────── */

static void ReadBlock(uint32_t addr, uint8_t *dstP, uint32_t count)
{
	while (count > 0u)
	{
		if ((addr & 3u) == 0u && count >= 4u)
		{
			uint32_t word = *(volatile const uint32_t *)(uintptr_t)addr;
			memcpy(dstP, &word, 4u);            /* keeps memory byte order */
			addr += 4u; dstP += 4u; count -= 4u;
		}
		else if ((addr & 1u) == 0u && count >= 2u)
		{
			uint16_t half = *(volatile const uint16_t *)(uintptr_t)addr;
			memcpy(dstP, &half, 2u);
			addr += 2u; dstP += 2u; count -= 2u;
		}
		else
		{
			*dstP++ = *(volatile const uint8_t *)(uintptr_t)addr;
			addr += 1u; count -= 1u;
		}
	}
}

static void WriteBlock(uint32_t addr, const uint8_t *srcP, uint32_t count)
{
	while (count > 0u)
	{
		if ((addr & 3u) == 0u && count >= 4u)
		{
			uint32_t word;
			memcpy(&word, srcP, 4u);
			*(volatile uint32_t *)(uintptr_t)addr = word;
			addr += 4u; srcP += 4u; count -= 4u;
		}
		else if ((addr & 1u) == 0u && count >= 2u)
		{
			uint16_t half;
			memcpy(&half, srcP, 2u);
			*(volatile uint16_t *)(uintptr_t)addr = half;
			addr += 2u; srcP += 2u; count -= 2u;
		}
		else
		{
			*(volatile uint8_t *)(uintptr_t)addr = *srcP++;
			addr += 1u; count -= 1u;
		}
	}
}

/* ── The three verbs ───────────────────────────────────────────────────── */

static void CmdRead(const char *argsP, TermapyMem_PutByteFn put)
{
	uint32_t addr;
	uint32_t count;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr(put, "usage"); return; }
	SkipSpaces(&argsP);
	if (!ParseDecU32(&argsP, &count)) { PutErr(put, "usage"); return; }
	if (count < 1u || count > TERMAPY_MEM_MAX_BLOCK) { PutErr(put, "length"); return; }
	if (!RangeOk(addr, count)) { PutErr(put, "range"); return; }

	for (uint32_t offset = 0u; offset < count; offset += ROW_BYTES)
	{
		uint8_t row[ROW_BYTES];
		uint32_t rowLen = count - offset;
		if (rowLen > ROW_BYTES) { rowLen = ROW_BYTES; }
		ReadBlock(addr + offset, row, rowLen);

		PutHex(put, addr + offset, (uint8_t)(TERMAPY_MEM_ADDRESS_BITS / 4u));
		put((uint8_t)':');
		for (uint32_t i = 0u; i < rowLen; i++)
		{
			put((uint8_t)' ');
			PutHex(put, row[i], 2u);
		}
		PutCrlf(put);
	}
	PutOk(put);
}

static void CmdWrite(const char *argsP, TermapyMem_PutByteFn put)
{
	uint32_t addr;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr(put, "usage"); return; }

	/* Hex pairs; spaces between pairs tolerated for hand typing. */
	uint8_t data[TERMAPY_MEM_MAX_BLOCK];
	uint32_t count = 0u;
	SkipSpaces(&argsP);
	while (*argsP != '\0')
	{
		int high = HexVal(argsP[0]);
		int low = (high >= 0) ? HexVal(argsP[1]) : -1;
		if (low < 0) { PutErr(put, "usage"); return; }   /* odd or non-hex */
		if (count >= TERMAPY_MEM_MAX_BLOCK) { PutErr(put, "length"); return; }
		data[count++] = (uint8_t)((high << 4) | low);
		argsP += 2;
		SkipSpaces(&argsP);
	}
	if (count == 0u) { PutErr(put, "usage"); return; }
	if (!RangeOk(addr, count)) { PutErr(put, "range"); return; }

	WriteBlock(addr, data, count);
	PutOk(put);
}

static void CmdInfo(TermapyMem_PutByteFn put)
{
	/* Endianness is a fact about the running core; detect, don't declare. */
	uint16_t probe = 1u;
	uint8_t first;
	memcpy(&first, &probe, 1u);

	PutStr(put, "{\"max_block\": ");
	PutDec(put, TERMAPY_MEM_MAX_BLOCK);
	PutStr(put, ", \"address_bits\": ");
	PutDec(put, TERMAPY_MEM_ADDRESS_BITS);
	PutStr(put, ", \"endian\": \"");
	PutStr(put, (first == 1u) ? "le" : "be");
	PutStr(put, "\"}");
	PutCrlf(put);
	PutOk(put);
}

/* ── Dispatch ──────────────────────────────────────────────────────────── */

static bool TokenIs(const char *cursorP, const char *wordP, const char **restPP)
{
	size_t n = 0u;
	while (wordP[n] != '\0')
	{
		char have = cursorP[n];
		if (have >= 'a' && have <= 'z') { have = (char)(have - 'a' + 'A'); }
		if (have != wordP[n]) { return false; }
		n++;
	}
	if (cursorP[n] != '\0' && cursorP[n] != ' ') { return false; }
	*restPP = cursorP + n;
	return true;
}

void TermapyMem_Cmd(const char *inputP, TermapyMem_PutByteFn putByteFn)
{
	const char *p = inputP;
	const char *rest;
	SkipSpaces(&p);
	/* Tolerate whatever the dispatcher left: "MEM.R ...", ".R ...", "R ...". */
	if ((p[0] == 'M' || p[0] == 'm') && (p[1] == 'E' || p[1] == 'e')
		&& (p[2] == 'M' || p[2] == 'm'))
	{
		p += 3;
	}
	if (*p == '.') { p++; }

	if (TokenIs(p, "R", &rest))         { CmdRead(rest, putByteFn); }
	else if (TokenIs(p, "W", &rest))    { CmdWrite(rest, putByteFn); }
	else if (TokenIs(p, "INFO", &rest)) { CmdInfo(putByteFn); }
	else                                { PutErr(putByteFn, "usage"); }
}
