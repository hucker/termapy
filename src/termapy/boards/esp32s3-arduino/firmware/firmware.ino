// termapy's starter for an ESP32-S3 DevKitC-1 running Arduino (the Arduino-ESP32 core).
//
// One command per line over the chip's own USB port, ended by CR, LF or both.
// It runs on the board, not in termapy: build this folder with arduino-cli,
// with USB CDC On Boot enabled so Serial is that port, and upload it.
// Arduino builds a folder whose .ino is named for it, so termapy copies this
// file under the folder's name: firmware.ino in a config's firmware/ folder.
//
// The commands are the rows of kCommands below, each a name, a one-line
// help and a handler; a new command is a handler and a row.  The monitor
// (termapy_monitor.c) answers two of its own: help lists every command with
// its help line, and json {on|off} switches the reply framing.  The MEM
// rows give termapy's /mem.* the chip's data RAM, kRegions.
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

#include <esp_mac.h>
#include <soc/soc.h>

// led on sets the red, green and blue LEDs all to this level: a dim white.
static const uint8_t ON_LEVEL = 32;

// The DevKitC-1's addressable RGB LED on GPIO48, the core's RGB_BUILTIN; it
// cannot be read back, so the levels last written are kept here.
static uint8_t levels[3] = { 0, 0, 0 };

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

static void ShowLevels(void)
{
	rgbLedWrite(RGB_BUILTIN, levels[0], levels[1], levels[2]);
}

static void CmdLed(const char *argsP)
{
	char word[8];
	char extra[2];
	const char *restP = TermapyMonitor_NextWord(argsP, word, sizeof word);
	bool on;
	if (restP != NULL && TermapyMonitor_NextWord(restP, extra, sizeof extra) != NULL) {
		TermapyMonitor_Fail("usage: led {on|off|1|0}", TERMAPY_MONITOR_BAD_ARGS);
		return;
	}
	if (restP != NULL) {
		if (!TermapyMonitor_OnOffArg(word, "state", &on)) {
			return;
		}
		levels[0] = levels[1] = levels[2] = on ? ON_LEVEL : 0;
		ShowLevels();
	}
	TermapyJson_Reply(levels[0] || levels[1] || levels[2] ? "on" : "off");
}

static void CmdLedRgb(const char *argsP)
{
	char words[4][12];
	const char *restP = argsP;
	long read[3];
	int count = 0;
	while (count < 4 && (restP = TermapyMonitor_NextWord(restP, words[count], sizeof words[0])) != NULL) {
		count++;
	}
	if (count != 0 && count != 3) {
		TermapyMonitor_Fail("usage: led_rgb {<r> <g> <b>}", TERMAPY_MONITOR_BAD_ARGS);
		return;
	}
	if (count == 3) {
		// Every level is read before any is set, so a bad one changes nothing.
		for (int index = 0; index < 3; index++) {
			if (!TermapyMonitor_IntArg(words[index], "level", 0, 255, &read[index])) {
				return;
			}
		}
		for (int index = 0; index < 3; index++) {
			levels[index] = (uint8_t)read[index];
		}
		ShowLevels();
	}
	TermapyJson_ArrayBegin();
	for (int index = 0; index < 3; index++) {
		TermapyJson_ItemUInt(levels[index]);
	}
	TermapyJson_ArrayEnd();
}

static void CmdTemp(const char *argsP)
{
	(void)argsP;
	// The chip's own sensor, in degrees C.
	TermapyJson_ReplyFloat(temperatureRead(), 1);
}

static void CmdId(const char *argsP)
{
	(void)argsP;
	// The factory MAC address in hex, the chip's unique ID.
	uint8_t mac[6];
	char text[13];
	static const char hex[] = "0123456789ABCDEF";
	esp_efuse_mac_get_default(mac);
	for (int index = 0; index < 6; index++) {
		text[2 * index] = hex[mac[index] >> 4];
		text[2 * index + 1] = hex[mac[index] & 0x0F];
	}
	text[12] = '\0';
	TermapyJson_Reply(text);
}

// -- MEM: termapy's /mem.* ----------------------------------------------------

// The memory MEM may touch: the chip's internal data RAM, from the IDF's
// soc.h, to read and write.  Writing it can change anything the sketch or
// the system holds there, a stack too.
static const TermapyMem_Region kRegions[] = {
	{ "dram", SOC_DRAM_LOW, SOC_DRAM_HIGH - SOC_DRAM_LOW, true },
};

// MEM.M holds interrupts off for the read, mask and write of one word, on
// the core the sketch runs on; the other core keeps running.
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
	{ "led", "the RGB LED on, a dim white, or off: led on|off|1|0; bare led reads", CmdLed, false },
	{ "led_rgb", "the RGB LED's levels: led_rgb <r> <g> <b>, 0-255 each, decimal or 0x hex; bare led_rgb reads", CmdLedRgb, false },
	{ "temp", "the on-chip temperature in degrees C", CmdTemp, false },
	{ "id", "the chip's unique ID in hex, its factory MAC address", CmdId, false },
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
	// The LED keeps its last color through a reset; start it dark, matching levels.
	ShowLevels();
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
