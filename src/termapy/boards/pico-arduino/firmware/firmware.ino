// termapy's starter for a Raspberry Pi Pico running Arduino (the arduino-pico core).
//
// One command per line over USB serial, ended by CR, LF or both.  It runs on
// the board, not in termapy: build this folder with arduino-cli and upload
// it, and the board answers at every power-up.  Arduino builds a folder whose
// .ino is named for it, so termapy copies this file under the folder's name:
// firmware.ino in a config's firmware/ folder.
//
// The commands are the rows of kCommands below, each a name, a one-line
// help and a handler; a new command is a handler and a row.  The monitor
// (termapy_monitor.c) answers two of its own: help lists every command with
// its help line, and json {on|off} switches the reply framing.  The MEM
// rows give termapy's /mem.* the board's memory, inside kRegions.
//
// termapy_monitor.c is the C twin of the monitor termapy's MicroPython
// starters run, on the same wire: in text framing a reply is the value and a
// failure ERR <reason>; in JSON framing each reply is one JSON-RPC response
// line, and a {...} request line switches to it by itself.  termapy's
// profile for this board describes these commands, so they show in /help and
// reach an MCP agent as typed tools.

#include "termapy_json.h"
#include "termapy_mem.h"
#include "termapy_monitor.h"

#include <hardware/regs/addressmap.h>

static bool ledOn = false;

// -- The platform the replies go out through --------------------------------

static int SerialPut(int c)
{
	Serial.write((uint8_t)c);
	return c;
}

static bool FormatFloat(double value, uint8_t decimals, char *bufP, size_t cap)
{
	return snprintf(bufP, cap, "%.*f", (int)decimals, value) > 0;
}

// -- Commands ----------------------------------------------------------------

static void CmdLed(const char *argsP)
{
	char word[8];
	char extra[2];
	const char *restP = TermapyMonitor_NextWord(argsP, word, sizeof word);
	if (restP != NULL && TermapyMonitor_NextWord(restP, extra, sizeof extra) != NULL) {
		TermapyMonitor_Fail("usage: led {on|off|1|0}", TERMAPY_MONITOR_BAD_ARGS);
		return;
	}
	if (restP != NULL) {
		if (!TermapyMonitor_OnOffArg(word, "state", &ledOn)) {
			return;
		}
		digitalWrite(LED_BUILTIN, ledOn ? HIGH : LOW);
	}
	TermapyJson_Reply(ledOn ? "on" : "off");
}

static void CmdTemp(const char *argsP)
{
	(void)argsP;
	// The core reads the RP2040's on-chip sensor and converts it to degrees C.
	TermapyJson_ReplyFloat(analogReadTemp(), 1);
}

static void CmdId(const char *argsP)
{
	(void)argsP;
	// The flash chip's unique ID in hex, which the core also gives USB as the
	// board's serial number, so it is what termapy's config connects by.
	TermapyJson_Reply(rp2040.getChipID());
}

// -- MEM: termapy's /mem.* ----------------------------------------------------

// The memory MEM may touch, from the SDK's address map: the 16 kB boot ROM
// (RP2040 datasheet, 2.8) to read, and all 264 kB of SRAM to read and write.
// Writing SRAM can change anything the sketch holds there, its stack too.
static const TermapyMem_Region kRegions[] = {
	{ "rom", ROM_BASE, 0x4000u, false },
	{ "sram", SRAM_BASE, SRAM_END - SRAM_BASE, true },
};

// MEM.M holds interrupts off for the read, mask and write of one word.
static void Lock(void)
{
	noInterrupts();
}

static void Unlock(void)
{
	interrupts();
}

static void MemNative(const char *lineP)
{
	TermapyMem_Cmd(lineP);
}

// -- The table -----------------------------------------------------------------

static const TermapyMonitor_Command kCommands[] = {
	{ "led", "the onboard LED: led on|off|1|0 sets it, bare led reads it", CmdLed, false },
	{ "temp", "the on-chip temperature in degrees C", CmdTemp, false },
	{ "id", "the board's unique ID in hex, also its USB serial number", CmdId, false },
	{ "MEM.R", "read memory: MEM.R <addr> <len>, the address in hex", MemNative, true },
	{ "MEM.W", "write memory: MEM.W <addr> <hex bytes>", MemNative, true },
	{ "MEM.M", "masked write with IRQs off: MEM.M <addr> <and> <or>", MemNative, true },
	{ "MEM.INFO", "the memory interface: block limit, address width, byte order", MemNative, true },
};

void setup()
{
	static const TermapyJson_Ops jsonOps = { SerialPut, FormatFloat };
	static const TermapyMem_Ops memOps = {
		TermapyMem_DirectRead,
		TermapyMem_DirectWrite,
		Lock,
		Unlock,
		TermapyMonitor_NativePut,
		kRegions,
		(uint8_t)(sizeof kRegions / sizeof kRegions[0]),
	};
	pinMode(LED_BUILTIN, OUTPUT);
	digitalWrite(LED_BUILTIN, LOW);
	Serial.begin(115200);
	TermapyJson_Init(&jsonOps);
	TermapyMem_Init(&memOps);
	TermapyMonitor_Init(kCommands, sizeof kCommands / sizeof kCommands[0]);
}

void loop()
{
	// The project's own work goes here too: the monitor never waits.
	while (Serial.available() > 0) {
		TermapyMonitor_Feed(Serial.read());
	}
}
