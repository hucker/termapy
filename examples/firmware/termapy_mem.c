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
 * - The WIRE MODULE (TermapyMem_Init / TermapyMem_Cmd and everything
 *   static) parses the verb and arguments, calls the platform's read /
 *   write, and formats rows, OK, ERR and the MEM.INFO JSON line.  It
 *   never dereferences memory and holds no address knowledge.  The
 *   platform is injected ONCE: TermapyMem_Init copies the ops struct
 *   into sOps.  One monitor on one link, so nothing threads it through
 *   every call.
 * - The DIRECT HELPERS (TermapyMem_DirectRead / TermapyMem_DirectWrite,
 *   at the bottom) are the flat-MCU implementation of the two byte ops:
 *   direct pointers gated by an editable region table, width-aware so
 *   peripheral windows are accessed at word size where possible.
 *
 * MEM.M is composed from the same two byte ops bracketed by lock /
 * unlock: lock -> read word -> (word & and) | or -> write -> unlock,
 * replying the PRE-modify word as one row so the host's audit gets a
 * true "before" without a separate racy read.  The word is loaded and
 * stored through an integer of its own width, so the masks apply to the
 * value the core sees on either endianness.  Atomicity is a platform
 * concern, exactly like access width.
 *
 * Every verb takes exactly its arguments: trailing text is "ERR usage",
 * and a number too wide for its field is "ERR usage" rather than a
 * silently wrapped value.  A reply is whole: rows are printed only
 * after the entire block was read, so "ERR range" never follows a
 * partial listing.
 *
 * Footprint: RAM is the copied ops struct plus TERMAPY_MEM_MAX_BLOCK
 * bytes of stack during MEM.R and MEM.W.  Flash is small enough not to
 * be a decision; measure it with your own toolchain rather than trust a
 * number written here for someone else's.
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
#if (TERMAPY_MEM_ADDRESS_BITS > 32) || (TERMAPY_MEM_ADDRESS_BITS < 4) \
	|| (TERMAPY_MEM_ADDRESS_BITS % 4 != 0)
#error "TERMAPY_MEM_ADDRESS_BITS: addresses are uint32_t; use 4..32, a multiple of 4"
#endif

#define ROW_BYTES   16u                                     /* per MEM.R row */
#define ADDR_DIGITS ((uint8_t)(TERMAPY_MEM_ADDRESS_BITS / 4u))

/* ── The platform, injected once ───────────────────────────────────────── */

static TermapyMem_Ops sOps;   /* all NULL until TermapyMem_Init: Cmd answers nothing */

void TermapyMem_Init(const TermapyMem_Ops *opsP)
{
	sOps = *opsP;
}

static bool Initialized(void)
{
	return sOps.put != NULL && sOps.read != NULL && sOps.write != NULL;
}

/* ── Output helpers (everything goes through sOps.put) ─────────────────── */

static void PutChar(char c)
{
	(void)sOps.put((int)(unsigned char)c);
}

static void PutStr(const char *stringP)
{
	while (*stringP != '\0')
	{
		PutChar(*stringP++);
	}
}

/** Upper-case hex, fixed digit count (at most 8), MSB first. */
static void PutHex(uint32_t value, uint8_t digits)
{
	while (digits-- > 0u)
	{
		int nibble = (int)((value >> (4u * digits)) & 0xFu);
		PutChar((char)(nibble < 10 ? ('0' + nibble) : ('A' + nibble - 10)));
	}
}

static void PutDec(uint32_t value)
{
	char buf[10];                       /* 4294967295 is ten digits */
	uint8_t n = 0u;
	do
	{
		buf[n++] = (char)('0' + (value % 10u));
		value /= 10u;
	} while (value != 0u);
	while (n-- > 0u)
	{
		PutChar(buf[n]);
	}
}

static void PutErr(const char *reasonP)
{
	PutStr("ERR ");
	PutStr(reasonP);
	PutStr("\r\n");
}

static void PutOk(void)
{
	PutStr("OK\r\n");
}

/** One data row: "<ADDR>: XX XX ..." for count bytes. */
static void PutRow(uint32_t addr, const uint8_t *bytesP, uint32_t count)
{
	PutHex(addr, ADDR_DIGITS);
	PutChar(':');
	for (uint32_t i = 0u; i < count; i++)
	{
		PutChar(' ');
		PutHex(bytesP[i], 2u);
	}
	PutStr("\r\n");
}

/* ── Input helpers ─────────────────────────────────────────────────────── */

static void SkipSpaces(const char **cursorPP)
{
	while (**cursorPP == ' ')
	{
		(*cursorPP)++;
	}
}

/** True when only spaces remain: a verb takes exactly its arguments. */
static bool AtEnd(const char *cursorP)
{
	SkipSpaces(&cursorP);
	return *cursorP == '\0';
}

static int HexVal(char c)
{
	if (c >= '0' && c <= '9') { return c - '0'; }
	if (c >= 'a' && c <= 'f') { return c - 'a' + 10; }
	if (c >= 'A' && c <= 'F') { return c - 'A' + 10; }
	return -1;
}

/** Hex digits, no prefix: the value and the digit count (1..8).  False
 *  when no digit follows or a ninth digit would overflow uint32_t. */
static bool ParseHexDigits(const char **cursorPP, uint32_t *outP, uint8_t *digitsP)
{
	const char *p = *cursorPP;
	uint32_t value = 0u;
	uint8_t digits = 0u;
	int digit;
	while ((digit = HexVal(*p)) >= 0)
	{
		if (digits == 8u) { return false; }
		value = (value << 4) | (uint32_t)digit;
		digits++;
		p++;
	}
	if (digits == 0u) { return false; }
	*cursorPP = p;
	*outP = value;
	*digitsP = digits;
	return true;
}

/** Hex number, optional 0x prefix, at most eight digits. */
static bool ParseHexU32(const char **cursorPP, uint32_t *outP)
{
	const char *p = *cursorPP;
	uint8_t digits;
	if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) { p += 2; }
	if (!ParseHexDigits(&p, outP, &digits)) { return false; }
	*cursorPP = p;
	return true;
}

/** Decimal number.  False when no digit follows or the value exceeds
 *  uint32_t (checked before each multiply, never wrapped). */
static bool ParseDecU32(const char **cursorPP, uint32_t *outP)
{
	const char *p = *cursorPP;
	uint32_t value = 0u;
	bool any = false;
	while (*p >= '0' && *p <= '9')
	{
		uint32_t digit = (uint32_t)(*p - '0');
		if (value > (0xFFFFFFFFu - digit) / 10u) { return false; }
		value = value * 10u + digit;
		p++;
		any = true;
	}
	if (!any) { return false; }
	*cursorPP = p;
	*outP = value;
	return true;
}

/* ── Native-width word access for MEM.M ────────────────────────────────── */

/** The width-byte integer the core sees at bytesP, as a value.  Through
 *  an integer of exactly that width: copying two bytes into a uint32_t
 *  would land them in the top lanes on a big-endian core and the host's
 *  16-bit masks would then clear the data instead of masking it. */
static uint32_t LoadNative(const uint8_t *bytesP, uint32_t width)
{
	if (width == 1u)
	{
		return bytesP[0];
	}
	if (width == 2u)
	{
		uint16_t half;
		memcpy(&half, bytesP, 2u);
		return half;
	}
	uint32_t word;
	memcpy(&word, bytesP, 4u);
	return word;
}

static void StoreNative(uint8_t *bytesP, uint32_t value, uint32_t width)
{
	if (width == 1u)
	{
		bytesP[0] = (uint8_t)value;
	}
	else if (width == 2u)
	{
		uint16_t half = (uint16_t)value;
		memcpy(bytesP, &half, 2u);
	}
	else
	{
		memcpy(bytesP, &value, 4u);
	}
}

/* ── The four verbs (wire only -- memory access goes through sOps) ─────── */

static void CmdRead(const char *argsP)
{
	uint32_t addr;
	uint32_t count;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr("usage"); return; }
	SkipSpaces(&argsP);
	if (!ParseDecU32(&argsP, &count)) { PutErr("usage"); return; }
	if (!AtEnd(argsP)) { PutErr("usage"); return; }
	if (count < 1u || count > TERMAPY_MEM_MAX_BLOCK) { PutErr("length"); return; }

	/* The whole block first, then the rows: one platform call, and a
	 * refused range is a whole "ERR range", never rows-then-error. */
	uint8_t data[TERMAPY_MEM_MAX_BLOCK];
	if (!sOps.read(addr, data, count)) { PutErr("range"); return; }
	for (uint32_t offset = 0u; offset < count; offset += ROW_BYTES)
	{
		uint32_t rowLen = count - offset;
		if (rowLen > ROW_BYTES) { rowLen = ROW_BYTES; }
		PutRow(addr + offset, &data[offset], rowLen);
	}
	PutOk();
}

static void CmdWrite(const char *argsP)
{
	uint32_t addr;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr("usage"); return; }

	/* Hex pairs; spaces between pairs tolerated for hand typing. */
	uint8_t data[TERMAPY_MEM_MAX_BLOCK];
	uint32_t count = 0u;
	SkipSpaces(&argsP);
	while (*argsP != '\0')
	{
		int high = HexVal(argsP[0]);
		int low = (high >= 0) ? HexVal(argsP[1]) : -1;
		if (low < 0) { PutErr("usage"); return; }   /* odd or non-hex */
		if (count >= TERMAPY_MEM_MAX_BLOCK) { PutErr("length"); return; }
		data[count++] = (uint8_t)((high << 4) | low);
		argsP += 2;
		SkipSpaces(&argsP);
	}
	if (count == 0u) { PutErr("usage"); return; }

	if (!sOps.write(addr, data, count)) { PutErr("range"); return; }
	PutOk();
}

static void CmdModify(const char *argsP)
{
	if (sOps.lock == NULL || sOps.unlock == NULL) { PutErr("usage"); return; }

	uint32_t addr;
	uint32_t andMask;
	uint32_t orMask;
	uint8_t andDigits;
	uint8_t orDigits;
	SkipSpaces(&argsP);
	if (!ParseHexU32(&argsP, &addr)) { PutErr("usage"); return; }
	SkipSpaces(&argsP);
	if (!ParseHexDigits(&argsP, &andMask, &andDigits)) { PutErr("usage"); return; }
	SkipSpaces(&argsP);
	if (!ParseHexDigits(&argsP, &orMask, &orDigits)) { PutErr("usage"); return; }
	if (!AtEnd(argsP) || andDigits != orDigits) { PutErr("usage"); return; }

	/* Width from the masks' digit count: 2 / 4 / 8 = u8 / u16 / u32. */
	uint32_t width;
	if (andDigits <= 2u) { width = 1u; }
	else if (andDigits <= 4u) { width = 2u; }
	else { width = 4u; }
	if ((addr & (width - 1u)) != 0u) { PutErr("usage"); return; }

	uint8_t oldBytes[4];
	uint8_t newBytes[4];
	bool ok;
	sOps.lock();
	ok = sOps.read(addr, oldBytes, width);
	if (ok)
	{
		uint32_t value = LoadNative(oldBytes, width);
		StoreNative(newBytes, (value & andMask) | orMask, width);
		ok = sOps.write(addr, newBytes, width);
	}
	sOps.unlock();
	if (!ok) { PutErr("range"); return; }

	PutRow(addr, oldBytes, width);             /* the PRE-modify word */
	PutOk();
}

static void CmdInfo(const char *argsP)
{
	if (!AtEnd(argsP)) { PutErr("usage"); return; }

	/* Endianness is a fact about the running core; detect, don't declare. */
	uint16_t probe = 1u;
	uint8_t first;
	memcpy(&first, &probe, 1u);

	PutStr("{\"max_block\": ");
	PutDec(TERMAPY_MEM_MAX_BLOCK);
	PutStr(", \"address_bits\": ");
	PutDec(TERMAPY_MEM_ADDRESS_BITS);
	PutStr(", \"endian\": \"");
	PutStr((first == 1u) ? "le" : "be");
	PutStr("\"");
	if (sOps.lock != NULL && sOps.unlock != NULL)
	{
		PutStr(", \"modify\": true");
	}
	PutStr("}\r\n");
	PutOk();
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

void TermapyMem_Cmd(const char *inputP)
{
	const char *p = inputP;
	const char *rest;
	if (!Initialized()) { return; }        /* no platform: nothing to answer on */
	SkipSpaces(&p);
	/* Tolerate whatever the dispatcher left: "MEM.R ...", ".R ...", "R ...". */
	if ((p[0] == 'M' || p[0] == 'm') && (p[1] == 'E' || p[1] == 'e')
		&& (p[2] == 'M' || p[2] == 'm'))
	{
		p += 3;
	}
	if (*p == '.') { p++; }

	if (TokenIs(p, "R", &rest))         { CmdRead(rest); }
	else if (TokenIs(p, "W", &rest))    { CmdWrite(rest); }
	else if (TokenIs(p, "M", &rest))    { CmdModify(rest); }
	else if (TokenIs(p, "INFO", &rest)) { CmdInfo(rest); }
	else                                { PutErr("usage"); }
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

/** True when [addr, addr+count) lies inside ONE region of the table.
 *  count is at least 1 from every wire path; 0 is refused, not allowed. */
static bool RangeOk(uint32_t addr, uint32_t count)
{
	uint32_t end = addr + count - 1u;
	if (count == 0u || end < addr) { return false; }   /* empty, or wrap-around */
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
