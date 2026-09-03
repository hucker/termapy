/**
 * @file termapy_mem.c
 * @brief Reference implementation of the termapy native memory spec.
 *
 * See termapy_mem.h for the wire contract and integration notes, and
 * termapy's "memory" help topic for the normative spec.  Portable C99;
 * only <stdint.h>, <stdbool.h>, <stddef.h> and memcpy are used.
 *
 * Structure:
 *
 * - The WIRE MODULE (TermapyMem_Cmd and everything static) parses the
 *   verb and arguments, calls ops->read / ops->write, and formats rows,
 *   OK, ERR and the MEM.INFO JSON line.  It never dereferences memory
 *   and holds no address knowledge.
 * - The DIRECT HELPERS (TermapyMem_DirectRead / TermapyMem_DirectWrite,
 *   at the bottom) are the flat-MCU implementation of the two byte ops:
 *   direct pointers gated by an editable region table, width-aware so
 *   peripheral windows are accessed at word size where possible.
 *
 * MEM.M is composed from the same two byte ops bracketed by ops->lock /
 * ops->unlock: lock -> read word -> (word & and) | or -> write -> unlock,
 * replying the PRE-modify word as one row so the host's audit gets a
 * true "before" without a separate racy read.  Atomicity is a platform
 * concern, exactly like access width.
 *
 * Footprint: ~900 bytes of flash on a Cortex-M0+ at -Os; stack use is
 * TERMAPY_MEM_MAX_BLOCK bytes during MEM.W, a 16-byte row during MEM.R.
 */

#include "termapy_mem.h"

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

/* ── Output helpers (everything goes through ops->put) ─────────────────── */

static void PutStr(const TermapyMem_Ops *ops, const char *stringP)
{
	while (*stringP != '\0')
	{
		(void)ops->put((int)(unsigned char)*stringP++);
	}
}

static void PutCrlf(const TermapyMem_Ops *ops)
{
	(void)ops->put('\r');
	(void)ops->put('\n');
}

/** Upper-case hex, fixed digit count, MSB first. */
static void PutHex(const TermapyMem_Ops *ops, uint32_t value, uint8_t digits)
{
	while (digits-- > 0u)
	{
		uint8_t nibble = (uint8_t)((value >> (4u * digits)) & 0xFu);
		(void)ops->put(nibble < 10u ? ('0' + nibble) : ('A' + nibble - 10u));
	}
}

static void PutDec(const TermapyMem_Ops *ops, uint32_t value)
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
		(void)ops->put((int)buf[n]);
	}
}

static void PutErr(const TermapyMem_Ops *ops, const char *reasonP)
{
	PutStr(ops, "ERR ");
	PutStr(ops, reasonP);
	PutCrlf(ops);
}

static void PutOk(const TermapyMem_Ops *ops)
{
	PutStr(ops, "OK");
	PutCrlf(ops);
}

/** One data row: "<ADDR>: XX XX ..." for count bytes. */
static void PutRow(const TermapyMem_Ops *ops, uint32_t addr,
	const uint8_t *bytesP, uint32_t count)
{
	PutHex(ops, addr, (uint8_t)(TERMAPY_MEM_ADDRESS_BITS / 4u));
	(void)ops->put(':');
	for (uint32_t i = 0u; i < count; i++)
	{
		(void)ops->put(' ');
		PutHex(ops, bytesP[i], 2u);
	}
	PutCrlf(ops);
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

/** Hex number WITHOUT prefix; also reports the digit count (mask width). */
static bool ParseHexDigits(const char **cursorPP, uint32_t *outP, uint8_t *digitsP)
{
	const char *p = *cursorPP;
	uint32_t value = 0u;
	uint8_t digits = 0u;
	int digit;
	while ((digit = HexVal(*p)) >= 0)
	{
		value = (value << 4) | (uint32_t)digit;
		p++;
		digits++;
	}
	if (digits == 0u || digits > 8u) { return false; }
	*cursorPP = p;
	*outP = value;
	*digitsP = digits;
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

/* ── The four verbs (wire only -- memory access goes through ops) ──────── */

static void CmdRead(const char *argsP, const TermapyMem_Ops *ops)
{
	uint32_t addr;
	uint32_t count;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr(ops, "usage"); return; }
	SkipSpaces(&argsP);
	if (!ParseDecU32(&argsP, &count)) { PutErr(ops, "usage"); return; }
	if (count < 1u || count > TERMAPY_MEM_MAX_BLOCK) { PutErr(ops, "length"); return; }

	for (uint32_t offset = 0u; offset < count; offset += ROW_BYTES)
	{
		uint8_t row[ROW_BYTES];
		uint32_t rowLen = count - offset;
		if (rowLen > ROW_BYTES) { rowLen = ROW_BYTES; }
		if (!ops->read(addr + offset, row, rowLen)) { PutErr(ops, "range"); return; }
		PutRow(ops, addr + offset, row, rowLen);
	}
	PutOk(ops);
}

static void CmdWrite(const char *argsP, const TermapyMem_Ops *ops)
{
	uint32_t addr;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr(ops, "usage"); return; }

	/* Hex pairs; spaces between pairs tolerated for hand typing. */
	uint8_t data[TERMAPY_MEM_MAX_BLOCK];
	uint32_t count = 0u;
	SkipSpaces(&argsP);
	while (*argsP != '\0')
	{
		int high = HexVal(argsP[0]);
		int low = (high >= 0) ? HexVal(argsP[1]) : -1;
		if (low < 0) { PutErr(ops, "usage"); return; }   /* odd or non-hex */
		if (count >= TERMAPY_MEM_MAX_BLOCK) { PutErr(ops, "length"); return; }
		data[count++] = (uint8_t)((high << 4) | low);
		argsP += 2;
		SkipSpaces(&argsP);
	}
	if (count == 0u) { PutErr(ops, "usage"); return; }

	if (!ops->write(addr, data, count)) { PutErr(ops, "range"); return; }
	PutOk(ops);
}

static void CmdModify(const char *argsP, const TermapyMem_Ops *ops)
{
	if (ops->lock == NULL || ops->unlock == NULL) { PutErr(ops, "usage"); return; }

	uint32_t addr;
	uint32_t andMask;
	uint32_t orMask;
	uint8_t andDigits;
	uint8_t orDigits;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr(ops, "usage"); return; }
	SkipSpaces(&argsP);
	if (!ParseHexDigits(&argsP, &andMask, &andDigits)) { PutErr(ops, "usage"); return; }
	SkipSpaces(&argsP);
	if (!ParseHexDigits(&argsP, &orMask, &orDigits)) { PutErr(ops, "usage"); return; }
	SkipSpaces(&argsP);
	if (*argsP != '\0' || andDigits != orDigits) { PutErr(ops, "usage"); return; }

	/* Width from the masks' digit count: 2 / 4 / 8 = u8 / u16 / u32. */
	uint32_t width;
	if (andDigits <= 2u) { width = 1u; }
	else if (andDigits <= 4u) { width = 2u; }
	else { width = 4u; }
	if ((addr & (width - 1u)) != 0u) { PutErr(ops, "usage"); return; }

	uint8_t oldBytes[4];
	uint32_t word = 0u;
	bool ok;
	ops->lock();
	ok = ops->read(addr, oldBytes, width);
	if (ok)
	{
		memcpy(&word, oldBytes, width);        /* native word order */
		word = (word & andMask) | orMask;
		uint8_t newBytes[4];
		memcpy(newBytes, &word, width);
		ok = ops->write(addr, newBytes, width);
	}
	ops->unlock();
	if (!ok) { PutErr(ops, "range"); return; }

	PutRow(ops, addr, oldBytes, width);        /* the PRE-modify word */
	PutOk(ops);
}

static void CmdInfo(const TermapyMem_Ops *ops)
{
	/* Endianness is a fact about the running core; detect, don't declare. */
	uint16_t probe = 1u;
	uint8_t first;
	memcpy(&first, &probe, 1u);

	PutStr(ops, "{\"max_block\": ");
	PutDec(ops, TERMAPY_MEM_MAX_BLOCK);
	PutStr(ops, ", \"address_bits\": ");
	PutDec(ops, TERMAPY_MEM_ADDRESS_BITS);
	PutStr(ops, ", \"endian\": \"");
	PutStr(ops, (first == 1u) ? "le" : "be");
	PutStr(ops, "\"");
	if (ops->lock != NULL && ops->unlock != NULL)
	{
		PutStr(ops, ", \"modify\": true");
	}
	PutStr(ops, "}");
	PutCrlf(ops);
	PutOk(ops);
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

void TermapyMem_Cmd(const char *inputP, const TermapyMem_Ops *opsP)
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

	if (TokenIs(p, "R", &rest))         { CmdRead(rest, opsP); }
	else if (TokenIs(p, "W", &rest))    { CmdWrite(rest, opsP); }
	else if (TokenIs(p, "M", &rest))    { CmdModify(rest, opsP); }
	else if (TokenIs(p, "INFO", &rest)) { CmdInfo(opsP); }
	else                                { PutErr(opsP, "usage"); }
}

/* ── Direct helpers: the flat-MCU implementation of the two byte ops ───── */

/**
 * The memory map: the ONLY addresses the Direct helpers will touch.
 * >>> EDIT FOR YOUR PART before shipping. <<<
 * The defaults are a generic Cortex-M map (flash at 0, SRAM, peripherals,
 * the system control space).
 */
static const struct { uint32_t start; uint32_t size; } sRegionsA[] = {
	{ 0x00000000u, 0x00080000u },   /* flash   512 KB                    */
	{ 0x20000000u, 0x00010000u },   /* SRAM     64 KB                    */
	{ 0x40000000u, 0x03000000u },   /* peripherals                       */
	{ 0xE0000000u, 0x00100000u },   /* SCS / NVIC / SysTick              */
};

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

bool TermapyMem_DirectRead(uint32_t addr, uint8_t *dstP, uint32_t count)
{
	if (!RangeOk(addr, count)) { return false; }
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
	return true;
}

bool TermapyMem_DirectWrite(uint32_t addr, const uint8_t *srcP, uint32_t count)
{
	if (!RangeOk(addr, count)) { return false; }
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
	return true;
}
