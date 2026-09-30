/*
 * Host harness for examples/firmware/termapy_mem.c, driven by
 * tests/test_firmware_mem.py.
 *
 * The platform is 256 bytes of fake RAM at address 0, a lock/unlock pair
 * that counts, and stdout as the serial line.  One MEM command per stdin
 * line; each reply is followed by a "--" line so the test can split them,
 * and the lock tally is printed last.  stdout is switched to binary so
 * the module's CRLF reaches the test byte for byte.
 *
 * INFO is sent once BEFORE TermapyMem_Init: the module must print nothing
 * for it, or that text lands in front of the first real reply.
 */

#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "termapy_mem.h"

static uint8_t sRam[256];
static int sLockCalls;
static int sLockDepth;

static bool RangeOk(uint32_t addr, uint32_t count)
{
	return count >= 1u && addr < sizeof sRam && count <= sizeof sRam - addr;
}

static bool FakeRead(uint32_t addr, uint8_t *dstP, uint32_t count)
{
	if (!RangeOk(addr, count)) { return false; }
	memcpy(dstP, &sRam[addr], count);
	return true;
}

static bool FakeWrite(uint32_t addr, const uint8_t *srcP, uint32_t count)
{
	if (!RangeOk(addr, count)) { return false; }
	memcpy(&sRam[addr], srcP, count);
	return true;
}

static void Lock(void)   { sLockCalls++; sLockDepth++; }
static void Unlock(void) { sLockDepth--; }

int main(void)
{
	static const TermapyMem_Ops ops = { FakeRead, FakeWrite, Lock, Unlock, putchar };
	char line[512];

#ifdef _WIN32
	(void)_setmode(_fileno(stdout), _O_BINARY);
#endif

	TermapyMem_Cmd("INFO");                    /* before init: must be silent */
	TermapyMem_Init(&ops);
	while (fgets(line, sizeof line, stdin) != NULL)
	{
		line[strcspn(line, "\r\n")] = '\0';
		TermapyMem_Cmd(line);
		fputs("--\n", stdout);
	}
	printf("locks=%d depth=%d\n", sLockCalls, sLockDepth);
	return 0;
}
