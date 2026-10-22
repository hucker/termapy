/*
 * Host harness for examples/firmware/termapy_json.c, driven by
 * tests/test_firmware_json.py.
 *
 * The platform is stdout as the serial line.  One directive per stdin line;
 * each directive's output is followed by a "--" line so the test can split
 * replies.  stdout is switched to binary so the module's \r\n (text) and \n
 * (json) reach the test byte for byte.
 *
 * Directives:
 *   init | initnofloat            -> TermapyJson_Init with / without a float formatter
 *   mode text | mode json        -> TermapyJson_SetMode(...)
 *   modecmd                      -> TermapyJson_ModeCmd(NULL) (flip the bool)
 *   getmode                      -> prints "text" or "json"
 *   setid <n> | clearid          -> TermapyJson_SetId / ClearId
 *   reply <text>                 -> TermapyJson_Reply(text)
 *   replyraw <text>              -> TermapyJson_ReplyRaw(text)
 *   replyint <n> | replyuint <n> -> TermapyJson_ReplyInt / ReplyUInt
 *   replyfloat <v> <decimals>    -> TermapyJson_ReplyFloat
 *   error <code> <msg...>        -> TermapyJson_ReplyError(msg, code)
 *   abegin | aend | obegin | oend -> the builder's containers
 *   fabegin <key> | fobegin <key> -> keyed containers inside an object
 *   istr <text> | iraw <text> | iint <n> | iuint <n> | ifloat <v> <d>
 *                                -> array items
 *   fstr <key> <text...> | fraw <key> <text> | fint <key> <n>
 *   fuint <key> <n> | ffloat <key> <v> <d>
 *                                -> object fields
 *   pass <text...>               -> each char of text, then '\n', through PassthroughPut
 *   passraw <text...>            -> each char of text, no newline
 *   passend                      -> TermapyJson_PassthroughEnd
 *   extract <raw...>             -> run Extract; prints JSON|<cmd>, TEXT, or BAD
 *   line <raw...>                -> full OnLine flow: Extract then a built-in
 *                                   ver/echo/uptime dispatch then a reply
 *
 * A reply is issued once BEFORE TermapyJson_Init (the first directive the
 * test sends is "reply pre-init"): the module must print nothing for it.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "termapy_json.h"

/* The platform's float printer: plain %.Nf, no padding. */
static bool FmtFloat(double value, uint8_t decimals, char *bufP, size_t cap)
{
	return snprintf(bufP, cap, "%.*f", (int)decimals, value) > 0;
}

/* A tiny dispatch for the "line" directive: proves the OnLine flow. */
static void Dispatch(const char *lineP)
{
	if (strcmp(lineP, "ver") == 0)
	{
		TermapyJson_Reply("1.23");
	}
	else if (strncmp(lineP, "echo", 4) == 0 && (lineP[4] == ' ' || lineP[4] == '\0'))
	{
		const char *argP = (lineP[4] == ' ') ? lineP + 5 : lineP + 4;
		if (*argP == '\0')
		{
			TermapyJson_ReplyError("usage: echo <text>", -1);
		}
		else
		{
			TermapyJson_Reply(argP);
		}
	}
	else if (strcmp(lineP, "uptime") == 0)
	{
		TermapyJson_ReplyRaw("12345");
	}
	else
	{
		TermapyJson_ReplyError("unknown command", -2);
	}
}

static void DoExtract(const char *rawP, bool runDispatch)
{
	char norm[96];
	TermapyJson_InResult r = TermapyJson_Extract(rawP, norm, sizeof norm);
	if (runDispatch)
	{
		switch (r)
		{
		case TERMAPY_JSON_IN_JSON: Dispatch(norm); break;
		case TERMAPY_JSON_IN_BAD:  TermapyJson_ReplyError("malformed json", -32700); break;
		case TERMAPY_JSON_IN_TEXT:
		default:                   Dispatch(rawP); break;
		}
		return;
	}
	switch (r)
	{
	case TERMAPY_JSON_IN_JSON: printf("JSON|%s\n", norm); break;
	case TERMAPY_JSON_IN_BAD:  printf("BAD\n"); break;
	case TERMAPY_JSON_IN_TEXT:
	default:                   printf("TEXT\n"); break;
	}
}

/* "<key> <rest>" -> key (NUL-terminated in place) and a pointer to rest. */
static char *SplitKey(char *argP, char **restPP)
{
	char *restP = strchr(argP, ' ');
	if (restP != NULL) { *restP++ = '\0'; }
	else               { restP = argP + strlen(argP); }
	*restPP = restP;
	return argP;
}

/* "<value> <decimals>" for the float directives. */
static void ParseFloatArgs(const char *argP, double *valueP, uint8_t *decimalsP)
{
	char *endP;
	*valueP = strtod(argP, &endP);
	*decimalsP = (uint8_t)strtoul(endP, NULL, 10);
}

static void Passthrough(const char *textP, bool newline)
{
	while (*textP != '\0')
	{
		(void)TermapyJson_PassthroughPut((int)(unsigned char)*textP++);
	}
	if (newline)
	{
		(void)TermapyJson_PassthroughPut('\n');
	}
}

int main(void)
{
	static const TermapyJson_Ops ops = { putchar, FmtFloat };
	static const TermapyJson_Ops opsNoFloat = { putchar, NULL };
	char line[512];

#ifdef _WIN32
	(void)_setmode(_fileno(stdout), _O_BINARY);
#endif

	while (fgets(line, sizeof line, stdin) != NULL)
	{
		char *argP;
		char *restP;
		double value;
		uint8_t decimals;
		line[strcspn(line, "\r\n")] = '\0';
		argP = strchr(line, ' ');
		if (argP != NULL) { *argP++ = '\0'; }
		else              { argP = line + strlen(line); } /* empty arg */

		if (strcmp(line, "init") == 0)              { TermapyJson_Init(&ops); }
		else if (strcmp(line, "initnofloat") == 0)  { TermapyJson_Init(&opsNoFloat); }
		else if (strcmp(line, "mode") == 0)
		{
			TermapyJson_SetMode(strcmp(argP, "json") == 0 ? TERMAPY_JSON_JSON : TERMAPY_JSON_TEXT);
		}
		else if (strcmp(line, "modecmd") == 0)      { TermapyJson_ModeCmd(NULL); }
		else if (strcmp(line, "getmode") == 0)
		{
			printf("%s\n", TermapyJson_GetMode() == TERMAPY_JSON_JSON ? "json" : "text");
		}
		else if (strcmp(line, "setid") == 0)        { TermapyJson_SetId(strtol(argP, NULL, 10)); }
		else if (strcmp(line, "clearid") == 0)      { TermapyJson_ClearId(); }
		else if (strcmp(line, "reply") == 0)        { TermapyJson_Reply(argP); }
		else if (strcmp(line, "replyraw") == 0)     { TermapyJson_ReplyRaw(argP); }
		else if (strcmp(line, "replyint") == 0)     { TermapyJson_ReplyInt(strtol(argP, NULL, 10)); }
		else if (strcmp(line, "replyuint") == 0)    { TermapyJson_ReplyUInt(strtoul(argP, NULL, 10)); }
		else if (strcmp(line, "replyfloat") == 0)
		{
			ParseFloatArgs(argP, &value, &decimals);
			TermapyJson_ReplyFloat(value, decimals);
		}
		else if (strcmp(line, "error") == 0)
		{
			char *msgP = strchr(argP, ' ');
			int code = (int)strtol(argP, NULL, 10);
			msgP = (msgP != NULL) ? msgP + 1 : argP + strlen(argP);
			TermapyJson_ReplyError(msgP, code);
		}
		else if (strcmp(line, "abegin") == 0)       { TermapyJson_ArrayBegin(); }
		else if (strcmp(line, "aend") == 0)         { TermapyJson_ArrayEnd(); }
		else if (strcmp(line, "obegin") == 0)       { TermapyJson_ObjectBegin(); }
		else if (strcmp(line, "oend") == 0)         { TermapyJson_ObjectEnd(); }
		else if (strcmp(line, "fabegin") == 0)      { TermapyJson_FieldArrayBegin(argP); }
		else if (strcmp(line, "fobegin") == 0)      { TermapyJson_FieldObjectBegin(argP); }
		else if (strcmp(line, "istr") == 0)         { TermapyJson_ItemString(argP); }
		else if (strcmp(line, "iraw") == 0)         { TermapyJson_ItemRaw(argP); }
		else if (strcmp(line, "iint") == 0)         { TermapyJson_ItemInt(strtol(argP, NULL, 10)); }
		else if (strcmp(line, "iuint") == 0)        { TermapyJson_ItemUInt(strtoul(argP, NULL, 10)); }
		else if (strcmp(line, "ifloat") == 0)
		{
			ParseFloatArgs(argP, &value, &decimals);
			TermapyJson_ItemFloat(value, decimals);
		}
		else if (strcmp(line, "fstr") == 0)
		{
			char *keyP = SplitKey(argP, &restP);
			TermapyJson_FieldString(keyP, restP);
		}
		else if (strcmp(line, "fraw") == 0)
		{
			char *keyP = SplitKey(argP, &restP);
			TermapyJson_FieldRaw(keyP, restP);
		}
		else if (strcmp(line, "fint") == 0)
		{
			char *keyP = SplitKey(argP, &restP);
			TermapyJson_FieldInt(keyP, strtol(restP, NULL, 10));
		}
		else if (strcmp(line, "fuint") == 0)
		{
			char *keyP = SplitKey(argP, &restP);
			TermapyJson_FieldUInt(keyP, strtoul(restP, NULL, 10));
		}
		else if (strcmp(line, "ffloat") == 0)
		{
			char *keyP = SplitKey(argP, &restP);
			ParseFloatArgs(restP, &value, &decimals);
			TermapyJson_FieldFloat(keyP, value, decimals);
		}
		else if (strcmp(line, "pass") == 0)         { Passthrough(argP, true); }
		else if (strcmp(line, "passraw") == 0)      { Passthrough(argP, false); }
		else if (strcmp(line, "passend") == 0)      { TermapyJson_PassthroughEnd(); }
		else if (strcmp(line, "extract") == 0)      { DoExtract(argP, false); }
		else if (strcmp(line, "line") == 0)         { DoExtract(argP, true); }
		fputs("--\n", stdout);
	}
	return 0;
}
