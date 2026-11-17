/*
 * Host harness for src/termapy/firmware/c/termapy_monitor.c, driven by
 * tests/test_firmware_monitor.py.
 *
 * The platform is stdout as the serial line.  Each stdin line is one
 * directive, and its output is followed by a "--" line so the test can split
 * replies.  stdout is binary so the monitor's \r\n (text) and \n (JSON)
 * reach the test byte for byte.
 *
 * Directives:
 *   <wire text>        -> each byte, then '\n', through TermapyMonitor_Feed
 *   #raw <text>        -> each byte through Feed with no line ending
 *   #cr <text>         -> each byte, then "\r\n", through Feed
 *   #batch <a>|<b>...  -> each part, then '\n', through Feed: several lines
 *                         arriving together
 *   #long <n>          -> n 'x' bytes, then '\n', through Feed
 *   #answer <text>     -> TermapyMonitor_Answer(text)
 *   #reserved          -> TermapyMonitor_Init with a table that reuses "help";
 *                         prints "refused" or "installed"
 *
 * The table is a device's: led (a switch), level (a number from 0 to 255),
 * pair (two words), rgb (an array), temp (a float) and MEM.R, a native
 * command that writes text lines of its own and the line it was given.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "termapy_json.h"
#include "termapy_monitor.h"

static int Put(int c)
{
	return putchar(c);
}

static bool FmtFloat(double value, uint8_t decimals, char *bufP, size_t cap)
{
	return snprintf(bufP, cap, "%.*f", (int)decimals, value) > 0;
}

static bool sLed;

static void CmdLed(const char *argsP)
{
	char word[16];
	const char *restP = TermapyMonitor_NextWord(argsP, word, sizeof word);
	char extra[4];
	if (restP != NULL && TermapyMonitor_NextWord(restP, extra, sizeof extra) != NULL) {
		TermapyMonitor_Fail("usage: led {on|off|1|0}", TERMAPY_MONITOR_BAD_ARGS);
		return;
	}
	if (restP != NULL && !TermapyMonitor_OnOffArg(word, "state", &sLed)) {
		return;
	}
	TermapyJson_Reply(sLed ? "on" : "off");
}

static void CmdLevel(const char *argsP)
{
	long level;
	if (!TermapyMonitor_IntArg(argsP, "level", 0, 255, &level)) {
		return;
	}
	TermapyJson_ReplyInt(level);
}

static void CmdPair(const char *argsP)
{
	char first[16];
	char second[16];
	const char *restP = TermapyMonitor_NextWord(argsP, first, sizeof first);
	restP = TermapyMonitor_NextWord(restP, second, sizeof second);
	if (restP == NULL) {
		TermapyMonitor_Fail("usage: pair <a> <b>", TERMAPY_MONITOR_BAD_ARGS);
		return;
	}
	TermapyJson_ArrayBegin();
	TermapyJson_ItemString(first);
	TermapyJson_ItemString(second);
	TermapyJson_ArrayEnd();
}

static void CmdRgb(const char *argsP)
{
	(void)argsP;
	TermapyJson_ArrayBegin();
	TermapyJson_ItemInt(255);
	TermapyJson_ItemInt(0);
	TermapyJson_ItemInt(32);
	TermapyJson_ArrayEnd();
}

static void CmdTemp(const char *argsP)
{
	(void)argsP;
	TermapyJson_ReplyFloat(21.5, 1);
}

static void MemNative(const char *lineP)
{
	const char *textP = "00000000: 01 02\r\nOK\r\n";
	while (*lineP != '\0') {
		(void)TermapyMonitor_NativePut((unsigned char)*lineP++);
	}
	(void)TermapyMonitor_NativePut('\r');
	(void)TermapyMonitor_NativePut('\n');
	while (*textP != '\0') {
		(void)TermapyMonitor_NativePut((unsigned char)*textP++);
	}
}

static const TermapyMonitor_Command sCommands[] = {
	{ "led", "the LED: led on|off|1|0 sets it, bare led reads it", CmdLed, false },
	{ "level", "a level from 0 to 255, decimal or 0x hex", CmdLevel, false },
	{ "pair", "two words as an array", CmdPair, false },
	{ "rgb", "three levels as an array", CmdRgb, false },
	{ "temp", "a temperature in degrees C", CmdTemp, false },
	{ "MEM.R", "read memory: MEM.R <addr> <len>, the address in hex", MemNative, true },
};

static const TermapyMonitor_Command sBadTable[] = {
	{ "led", "the LED", CmdLed, false },
	{ "help", "a name the monitor answers itself", CmdLed, false },
};

static void FeedText(const char *textP)
{
	while (*textP != '\0') {
		(void)TermapyMonitor_Feed((unsigned char)*textP++);
	}
}

int main(void)
{
	static const TermapyJson_Ops jsonOps = { Put, FmtFloat };
	char line[4096];

#ifdef _WIN32
	_setmode(_fileno(stdout), _O_BINARY);
#endif
	TermapyJson_Init(&jsonOps);
	(void)TermapyMonitor_Init(sCommands, sizeof sCommands / sizeof sCommands[0]);

	while (fgets(line, sizeof line, stdin) != NULL) {
		line[strcspn(line, "\r\n")] = '\0';
		if (strncmp(line, "#raw ", 5) == 0) {
			FeedText(line + 5);
		} else if (strncmp(line, "#cr ", 4) == 0) {
			FeedText(line + 4);
			FeedText("\r\n");
		} else if (strncmp(line, "#batch ", 7) == 0) {
			char *partP = strtok(line + 7, "|");
			while (partP != NULL) {
				FeedText(partP);
				(void)TermapyMonitor_Feed('\n');
				partP = strtok(NULL, "|");
			}
		} else if (strncmp(line, "#long ", 6) == 0) {
			long count = strtol(line + 6, NULL, 10);
			while (count-- > 0) {
				(void)TermapyMonitor_Feed('x');
			}
			(void)TermapyMonitor_Feed('\n');
		} else if (strncmp(line, "#answer ", 8) == 0) {
			TermapyMonitor_Answer(line + 8);
		} else if (strcmp(line, "#reserved") == 0) {
			bool installed = TermapyMonitor_Init(sBadTable, sizeof sBadTable / sizeof sBadTable[0]);
			fputs(installed ? "installed\n" : "refused\n", stdout);
		} else {
			FeedText(line);
			(void)TermapyMonitor_Feed('\n');
		}
		fputs("--\n", stdout);
		fflush(stdout);
	}
	return 0;
}
