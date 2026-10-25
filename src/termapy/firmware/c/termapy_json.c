/**
 * @file termapy_json.c
 * @brief Reference implementation of termapy's JSON reply wrapper.
 *
 * See termapy_json.h for the wire contract and integration notes.  The
 * envelope is the JSON-RPC 2.0 response shape (``result`` or an ``error``
 * object, then ``id``), which src/termapy/demo_ndjson.py mirrors byte for
 * byte.  Portable C99; only <stdbool.h>, <stdint.h>, <stddef.h> and
 * <string.h> are used.
 *
 * Structure:
 *
 * - OUTPUT: everything reaches the serial line through sState.ops.put, one
 *   char at a time (PutChar / PutStr), so there is no output buffer and no
 *   limit on a result's length.  PutDecUnsigned / PutDecSigned, PutJsonChar /
 *   PutJsonStr and PutFloat are the value writers; PutHead opens the reply
 *   (mirroring the request's "jsonrpc" tag) and PutIdTail appends the
 *   echoed id.
 * - THE SLOT: SlotBegin / SlotEnd place one value.  At depth 0 that is the
 *   whole reply, so they write the envelope head and tail; inside a
 *   container they write the comma (and the key, for a field).  Every
 *   scalar reply, item and field is "SlotBegin, one value writer, SlotEnd",
 *   and the containers are "SlotBegin, bracket, depth++" / "depth--,
 *   bracket, SlotEnd".  One path for everything.
 * - PASSTHROUGH: a lazily opened string envelope for text a handler prints
 *   through the monitor's ordinary putc.
 * - INPUT (optional): Extract() is a tiny exact-substring matcher (NOT a
 *   JSON parser) that pulls "cmd" or "method", optional "args" or "params",
 *   an optional integer "id" and the "jsonrpc" tag out of a {...} request
 *   line, as blessed by the contract.
 *
 * The module owns the framing only; the command table and dispatch are the
 * integrator's.
 */

#include "termapy_json.h"

#include <stddef.h>
#include <string.h>

/* ── Session state (copied by Init; put==NULL => silent) ───────────────── */

typedef struct {
	TermapyJson_Ops ops;     /* ops.put NULL until Init: writers emit nothing   */
	TermapyJson_Mode mode;   /* TERMAPY_JSON_TEXT (0) until SetMode / Extract   */
	bool idPresent;          /* did the current request carry an id?           */
	long id;                 /* its value; valid only when idPresent           */
	bool tagged;             /* request carried "jsonrpc": mirror it, always emit id */
	uint8_t depth;           /* open builder containers                         */
	uint32_t itemMask;       /* bit L-1 set: container at level L has an item   */
	bool passOpen;           /* the passthrough envelope is open                */
	bool passPendingNl;      /* a '\n' is held; dropped if it ends the output   */
} TermapyJson_State;

static TermapyJson_State sState; /* zero-init: text mode, no id, no put */

void TermapyJson_Init(const TermapyJson_Ops *opsP)
{
	sState.ops = *opsP;
	sState.depth = 0u;
	sState.itemMask = 0u;
	sState.passOpen = false;
	sState.passPendingNl = false;
	TermapyJson_ClearId(); /* no pending id or tag from before the platform existed */
}

static bool Initialized(void)
{
	return sState.ops.put != NULL;
}

/* ── Mode and id ───────────────────────────────────────────────────────── */

void TermapyJson_SetMode(TermapyJson_Mode mode)
{
	sState.mode = mode;
}

TermapyJson_Mode TermapyJson_GetMode(void)
{
	return sState.mode;
}

void TermapyJson_ModeCmd(const char *argP)
{
	(void)argP; /* no grammar: flipping the boolean is the whole behaviour */
	sState.mode = (sState.mode == TERMAPY_JSON_JSON) ? TERMAPY_JSON_TEXT : TERMAPY_JSON_JSON;
}

void TermapyJson_SetId(long id)
{
	sState.idPresent = true;
	sState.id = id;
}

void TermapyJson_ClearId(void)
{
	sState.idPresent = false;
	sState.id = 0;
	sState.tagged = false;
}

/* ── Output helpers (everything goes through sState.ops.put) ───────────── */

static void PutChar(char c)
{
	(void)sState.ops.put((int)(unsigned char)c);
}

static void PutStr(const char *stringP)
{
	while (*stringP != '\0')
	{
		PutChar(*stringP++);
	}
}

/** Unsigned decimal, no stdio. */
static void PutDecUnsigned(unsigned long value)
{
	char buf[20]; /* 18446744073709551615 is 20 digits */
	uint8_t n = 0u;
	do
	{
		buf[n++] = (char)('0' + (value % 10ul));
		value /= 10ul;
	} while (value != 0ul);
	while (n-- > 0u)
	{
		PutChar(buf[n]);
	}
}

/** Signed decimal.  Negate into unsigned so LONG_MIN does not overflow. */
static void PutDecSigned(long value)
{
	if (value < 0)
	{
		PutChar('-');
		PutDecUnsigned(0ul - (unsigned long)value);
	}
	else
	{
		PutDecUnsigned((unsigned long)value);
	}
}

/** One byte of a JSON string body: the mandatory escapes, \u00XX for other
 *  control chars, everything >= 0x20 as-is (a device already holding UTF-8
 *  stays UTF-8; termapy decodes UTF-8). */
static void PutJsonChar(unsigned char c)
{
	switch (c)
	{
	case '"':  PutStr("\\\""); break;
	case '\\': PutStr("\\\\"); break;
	case '\n': PutStr("\\n"); break;
	case '\r': PutStr("\\r"); break;
	case '\t': PutStr("\\t"); break;
	default:
		if (c < 0x20u)
		{
			static const char hex[] = "0123456789abcdef";
			PutStr("\\u00");
			PutChar(hex[(c >> 4) & 0xFu]);
			PutChar(hex[c & 0xFu]);
		}
		else
		{
			PutChar((char)c);
		}
		break;
	}
}

/** A whole JSON string, quotes included. */
static void PutJsonStr(const char *stringP)
{
	PutChar('"');
	while (*stringP != '\0')
	{
		PutJsonChar((unsigned char)*stringP++);
	}
	PutChar('"');
}

/** The reply head: ``{``, the mirrored ``"jsonrpc":"2.0",`` when the request
 *  carried the key, then ``"<key>":`` for the one value (result or error). */
static void PutHead(const char *keyP)
{
	PutStr(sState.tagged ? "{\"jsonrpc\":\"2.0\",\"" : "{\"");
	PutStr(keyP);
	PutStr("\":");
}

/** The ``,"id":<n>`` tail when the current request carried an id.  A tagged
 *  request always gets an id, ``null`` when it had none, so the reply is a
 *  complete JSON-RPC response object.  Untagged and id-less: nothing. */
static void PutIdTail(void)
{
	if (sState.idPresent)
	{
		PutStr(",\"id\":");
		PutDecSigned(sState.id);
	}
	else if (sState.tagged)
	{
		PutStr(",\"id\":null");
	}
}

/** Run the injected formatter; false when there is none or it declines. */
static bool FormatFloat(double value, uint8_t decimals, char *bufP, size_t cap)
{
	if (sState.ops.fmtFloat == NULL)
	{
		return false;
	}
	bufP[0] = '\0';
	return sState.ops.fmtFloat(value, decimals, bufP, cap) && bufP[0] != '\0';
}

/** A plain JSON number: optional '-', a digit, then digits . e E + -.  Keeps
 *  "nan", "inf" and a padded " 7.4" off the wire. */
static bool IsJsonNumber(const char *stringP)
{
	if (*stringP == '-')
	{
		stringP++;
	}
	if (*stringP < '0' || *stringP > '9')
	{
		return false;
	}
	while (*stringP != '\0')
	{
		char c = *stringP++;
		if (!((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-'))
		{
			return false;
		}
	}
	return true;
}

/* ── The slot: where one value goes ────────────────────────────────────── */

/* The builder's comma bookkeeping: one "has an item" bit per open level. */

static bool LevelHasItem(void)
{
	return sState.depth <= TERMAPY_JSON_MAX_DEPTH &&
	       (sState.itemMask & (1ul << (sState.depth - 1u))) != 0u;
}

static void LevelMarkItem(void)
{
	if (sState.depth <= TERMAPY_JSON_MAX_DEPTH)
	{
		sState.itemMask |= (1ul << (sState.depth - 1u));
	}
}

static void LevelClear(void)
{
	if (sState.depth <= TERMAPY_JSON_MAX_DEPTH)
	{
		sState.itemMask &= ~(1ul << (sState.depth - 1u));
	}
}

/** JSON mode: open the place for one value.  At depth 0 that is the reply
 *  itself, so write the envelope head up to ``"result":`` (a key is
 *  meaningless there and dropped).  Inside a container, write the separating
 *  comma, then the key for a field. */
static void SlotBegin(const char *keyP)
{
	if (sState.depth == 0u)
	{
		PutHead("result");
		return;
	}
	if (LevelHasItem())
	{
		PutChar(',');
	}
	else
	{
		LevelMarkItem();
	}
	if (keyP != NULL)
	{
		PutJsonStr(keyP);
		PutChar(':');
	}
}

/** JSON mode: after one value.  At depth 0 the reply is complete: id tail,
 *  closing brace, line end.  Inside a container, nothing. */
static void SlotEnd(void)
{
	if (sState.depth == 0u)
	{
		PutIdTail();
		PutChar('}');
		PutStr(TERMAPY_JSON_LINE_EOL);
	}
}

/** TEXT mode: ``key `` before a field's value, nothing before an item's. */
static void TextBegin(const char *keyP)
{
	if (keyP != NULL)
	{
		PutStr(keyP);
		PutChar(' ');
	}
}

static void TextEnd(void)
{
	PutStr(TERMAPY_JSON_TEXT_EOL);
}

/* ── Value emitters: scalar reply, item and field are the same code ─────── */

static void EmitString(const char *keyP, const char *valueP, bool raw)
{
	if (!Initialized())
	{
		return;
	}
	if (sState.mode == TERMAPY_JSON_TEXT)
	{
		TextBegin(keyP);
		PutStr(valueP);
		TextEnd();
		return;
	}
	SlotBegin(keyP);
	if (raw)
	{
		PutStr(valueP);
	}
	else
	{
		PutJsonStr(valueP);
	}
	SlotEnd();
}

static void EmitSigned(const char *keyP, long value)
{
	if (!Initialized())
	{
		return;
	}
	if (sState.mode == TERMAPY_JSON_TEXT)
	{
		TextBegin(keyP);
		PutDecSigned(value);
		TextEnd();
		return;
	}
	SlotBegin(keyP);
	PutDecSigned(value);
	SlotEnd();
}

static void EmitUnsigned(const char *keyP, unsigned long value)
{
	if (!Initialized())
	{
		return;
	}
	if (sState.mode == TERMAPY_JSON_TEXT)
	{
		TextBegin(keyP);
		PutDecUnsigned(value);
		TextEnd();
		return;
	}
	SlotBegin(keyP);
	PutDecUnsigned(value);
	SlotEnd();
}

static void EmitFloat(const char *keyP, double value, uint8_t decimals)
{
	char buf[TERMAPY_JSON_FLOAT_BUF];
	bool formatted;
	if (!Initialized())
	{
		return;
	}
	formatted = FormatFloat(value, decimals, buf, sizeof buf);
	if (sState.mode == TERMAPY_JSON_TEXT)
	{
		TextBegin(keyP);
		if (formatted)
		{
			PutStr(buf);
		}
		TextEnd();
		return;
	}
	SlotBegin(keyP);
	if (formatted && IsJsonNumber(buf))
	{
		PutStr(buf);
	}
	else
	{
		PutStr("null");
	}
	SlotEnd();
}

/** The error envelope.  Always a whole reply: an error is never an item. */
static void EmitError(const char *msgP, int code)
{
	if (!Initialized())
	{
		return;
	}
	if (sState.mode == TERMAPY_JSON_TEXT)
	{
		PutStr(msgP);
		TextEnd();
		return;
	}
	PutHead("error");
	PutStr("{\"code\":");
	PutDecSigned((long)code);
	PutStr(",\"message\":");
	PutJsonStr(msgP);
	PutChar('}');
	PutIdTail();
	PutChar('}');
	PutStr(TERMAPY_JSON_LINE_EOL);
}

/* ── Containers ────────────────────────────────────────────────────────── */

static void ContainerBegin(const char *keyP, char open)
{
	if (!Initialized() || sState.mode == TERMAPY_JSON_TEXT)
	{
		return; /* text: the handler prints its own layout */
	}
	SlotBegin(keyP);
	PutChar(open);
	if (sState.depth < 255u)
	{
		sState.depth++;
	}
	LevelClear();
}

static void ContainerEnd(char close)
{
	if (!Initialized() || sState.mode == TERMAPY_JSON_TEXT || sState.depth == 0u)
	{
		return;
	}
	sState.depth--;
	PutChar(close);
	SlotEnd();
}

/* ── Public reply API ──────────────────────────────────────────────────── */

void TermapyJson_Reply(const char *resultP)
{
	EmitString(NULL, resultP, false);
}

void TermapyJson_ReplyString(const char *resultP)
{
	EmitString(NULL, resultP, false);
}

void TermapyJson_ReplyRaw(const char *rawResultP)
{
	EmitString(NULL, rawResultP, true);
}

void TermapyJson_ReplyInt(long value)
{
	EmitSigned(NULL, value);
}

void TermapyJson_ReplyUInt(unsigned long value)
{
	EmitUnsigned(NULL, value);
}

void TermapyJson_ReplyFloat(double value, uint8_t decimals)
{
	EmitFloat(NULL, value, decimals);
}

void TermapyJson_ReplyError(const char *msgP, int code)
{
	EmitError(msgP, code);
}

void TermapyJson_ArrayBegin(void)
{
	ContainerBegin(NULL, '[');
}

void TermapyJson_ArrayEnd(void)
{
	ContainerEnd(']');
}

void TermapyJson_ObjectBegin(void)
{
	ContainerBegin(NULL, '{');
}

void TermapyJson_ObjectEnd(void)
{
	ContainerEnd('}');
}

void TermapyJson_FieldArrayBegin(const char *keyP)
{
	ContainerBegin(keyP, '[');
}

void TermapyJson_FieldObjectBegin(const char *keyP)
{
	ContainerBegin(keyP, '{');
}

void TermapyJson_ItemString(const char *valueP)
{
	EmitString(NULL, valueP, false);
}

void TermapyJson_ItemRaw(const char *rawValueP)
{
	EmitString(NULL, rawValueP, true);
}

void TermapyJson_ItemInt(long value)
{
	EmitSigned(NULL, value);
}

void TermapyJson_ItemUInt(unsigned long value)
{
	EmitUnsigned(NULL, value);
}

void TermapyJson_ItemFloat(double value, uint8_t decimals)
{
	EmitFloat(NULL, value, decimals);
}

void TermapyJson_FieldString(const char *keyP, const char *valueP)
{
	EmitString(keyP, valueP, false);
}

void TermapyJson_FieldRaw(const char *keyP, const char *rawValueP)
{
	EmitString(keyP, rawValueP, true);
}

void TermapyJson_FieldInt(const char *keyP, long value)
{
	EmitSigned(keyP, value);
}

void TermapyJson_FieldUInt(const char *keyP, unsigned long value)
{
	EmitUnsigned(keyP, value);
}

void TermapyJson_FieldFloat(const char *keyP, double value, uint8_t decimals)
{
	EmitFloat(keyP, value, decimals);
}

/* ── Passthrough ───────────────────────────────────────────────────────── */

int TermapyJson_PassthroughPut(int c)
{
	if (!Initialized())
	{
		return -1;
	}
	if (sState.mode == TERMAPY_JSON_TEXT)
	{
		return sState.ops.put(c);
	}
	if (!sState.passOpen)
	{
		PutHead("result");
		PutChar('"');
		sState.passOpen = true;
		sState.passPendingNl = false;
	}
	if (sState.passPendingNl)
	{
		PutStr("\\n"); /* an inner newline: it was not the last byte after all */
		sState.passPendingNl = false;
	}
	if (c == '\n')
	{
		sState.passPendingNl = true; /* hold it; dropped if the output ends here */
		return c;
	}
	PutJsonChar((unsigned char)c);
	return c;
}

void TermapyJson_PassthroughEnd(void)
{
	if (!sState.passOpen)
	{
		return;
	}
	sState.passOpen = false;
	sState.passPendingNl = false;
	PutChar('"');
	PutIdTail();
	PutChar('}');
	PutStr(TERMAPY_JSON_LINE_EOL);
}

#if TERMAPY_JSON_HAS_EXTRACT

/* ── Input helpers (exact substring, not a JSON parser) ────────────────── */

static void SkipSpaces(const char **cursorPP)
{
	while (**cursorPP == ' ' || **cursorPP == '\t')
	{
		(*cursorPP)++;
	}
}

/** Find the literal ``"key"`` followed (spaces allowed) by ``:`` and leave
 *  the cursor just past the colon; false when there is no such key.  A plain
 *  substring scan (strstr), per the contract; the colon check skips a VALUE
 *  that merely equals a key name, so {"cmd":"params"} still runs. */
static bool FindKey(const char **cursorPP, const char *keyP)
{
	char quoted[16]; /* "jsonrpc" is the longest key: 10 bytes quoted */
	size_t n = strlen(keyP);
	const char *scan = *cursorPP;
	if (n + 3u > sizeof quoted)
	{
		return false;
	}
	quoted[0] = '"';
	memcpy(quoted + 1, keyP, n);
	quoted[n + 1u] = '"';
	quoted[n + 2u] = '\0';
	for (;;)
	{
		const char *hit = strstr(scan, quoted);
		const char *p;
		if (hit == NULL)
		{
			return false;
		}
		p = hit + (n + 2u);
		SkipSpaces(&p);
		if (*p == ':')
		{
			*cursorPP = p + 1;
			return true;
		}
		scan = hit + 1; /* a value that happens to equal the key name */
	}
}

/** After a key's colon: require ``"`` (spaces allowed before it).  Leaves
 *  the cursor just past the opening quote. */
static bool ExpectQuote(const char **cursorPP)
{
	const char *p = *cursorPP;
	SkipSpaces(&p);
	if (*p != '"') { return false; }
	p++;
	*cursorPP = p;
	return true;
}

/** Copy a JSON string body (cursor just past the opening quote) into outP,
 *  decoding the minimal escapes, up to the closing unescaped quote.  False
 *  on overflow or an unterminated string; leaves the cursor past the close
 *  on success. */
static bool CopyJsonString(const char **cursorPP, char *outP, size_t cap)
{
	const char *p = *cursorPP;
	size_t n = 0u;
	while (*p != '"')
	{
		char c;
		if (*p == '\0') { return false; } /* unterminated */
		if (*p == '\\')
		{
			p++;
			switch (*p)
			{
			case '"':  c = '"'; break;
			case '\\': c = '\\'; break;
			case '/':  c = '/'; break;
			case 'n':  c = '\n'; break;
			case 'r':  c = '\r'; break;
			case 't':  c = '\t'; break;
			case '\0': return false; /* trailing backslash */
			default:   c = *p; break; /* unknown escape: take the char */
			}
			p++;
		}
		else
		{
			c = *p++;
		}
		if (n + 1u >= cap) { return false; } /* leave room for NUL */
		outP[n++] = c;
	}
	outP[n] = '\0';
	*cursorPP = p + 1; /* past the closing quote */
	return true;
}

/** Append a space and a string to a buffer already holding a NUL-terminated
 *  token; false on overflow (buffer restored to what it held before). */
static bool AppendArg(char *outP, size_t cap, const char *argP)
{
	size_t n0 = strlen(outP);
	size_t n = n0;
	if (n + 1u >= cap) { return false; }
	outP[n++] = ' ';
	while (*argP != '\0')
	{
		if (n + 1u >= cap) { outP[n0] = '\0'; return false; }
		outP[n++] = *argP++;
	}
	outP[n] = '\0';
	return true;
}

/** A bare array element (number, true, false, null): everything up to ``,``
 *  ``]`` space tab or NUL.  False when empty, over long, or when it opens a
 *  container (``[`` or ``{``): nested params are malformed. */
static bool CopyBareToken(const char **cursorPP, char *outP, size_t cap)
{
	const char *p = *cursorPP;
	size_t n = 0u;
	if (*p == '[' || *p == '{') { return false; }
	while (*p != '\0' && *p != ',' && *p != ']' && *p != ' ' && *p != '\t')
	{
		if (n + 1u >= cap) { return false; }
		outP[n++] = *p++;
	}
	if (n == 0u) { return false; }
	outP[n] = '\0';
	*cursorPP = p;
	return true;
}

/** The value of "args" / "params", cursor just past the key's colon.  A
 *  string is appended as one token after a space.  An array (allowArray,
 *  the "params" form) appends each element -- quoted string or bare scalar
 *  -- after a space; ``[]`` appends nothing.  Anything else (a number, an
 *  object, null, a nested array, a trailing comma, an unterminated value)
 *  is false.  Each element must fit TERMAPY_JSON_ARGS_MAX; the whole line
 *  must fit cap. */
static bool AppendArgs(const char **cursorPP, char *outP, size_t cap, bool allowArray)
{
	char buf[TERMAPY_JSON_ARGS_MAX];
	const char *p = *cursorPP;
	SkipSpaces(&p);
	if (*p == '"')
	{
		p++;
		if (!CopyJsonString(&p, buf, sizeof buf) || !AppendArg(outP, cap, buf))
		{
			return false;
		}
		*cursorPP = p;
		return true;
	}
	if (*p != '[' || !allowArray)
	{
		return false;
	}
	p++;
	SkipSpaces(&p);
	if (*p == ']')
	{
		*cursorPP = p + 1; /* []: nothing appended */
		return true;
	}
	for (;;)
	{
		SkipSpaces(&p);
		if (*p == '"')
		{
			p++;
			if (!CopyJsonString(&p, buf, sizeof buf)) { return false; }
		}
		else if (!CopyBareToken(&p, buf, sizeof buf))
		{
			return false; /* empty element, trailing comma, nested, line end */
		}
		if (!AppendArg(outP, cap, buf)) { return false; }
		SkipSpaces(&p);
		if (*p == ',') { p++; continue; }
		if (*p == ']') { *cursorPP = p + 1; return true; }
		return false; /* two elements with no comma, or the line ended */
	}
}

/** Signed decimal for a numeric key.  The cursor is just past the key's
 *  colon (e.g. after ``"id":``), so consume spaces, then an optional ``-``
 *  and up to TERMAPY_JSON_ID_MAX_DIGITS digits.  False when no digit follows
 *  or the run is too long. */
static bool ParseIntSigned(const char **cursorPP, long *outP)
{
	const char *p = *cursorPP;
	long value = 0;
	bool negative = false;
	unsigned digits = 0u;
	SkipSpaces(&p);
	if (*p == '-') { negative = true; p++; }
	while (*p >= '0' && *p <= '9')
	{
		if (digits >= TERMAPY_JSON_ID_MAX_DIGITS) { return false; }
		value = value * 10 + (*p - '0');
		digits++;
		p++;
	}
	if (digits == 0u) { return false; }
	*cursorPP = p;
	*outP = negative ? -value : value;
	return true;
}

TermapyJson_InResult TermapyJson_Extract(const char *lineP, char *cmdOutP, size_t cmdOutCap)
{
	const char *p = lineP;
	const char *cursor;
	long id;

	SkipSpaces(&p);
	if (*p != '{')
	{
		/* A plain line carries no id and no tag: a reply to it must not
		 * echo the last request's.  Nothing else is touched. */
		TermapyJson_ClearId();
		return TERMAPY_JSON_IN_TEXT;
	}

	/* A JSON object: answer in JSON from here, even if it turns out bad. */
	sState.mode = TERMAPY_JSON_JSON;
	TermapyJson_ClearId();

	/* Tag and id first, so even a BAD request is answered with the mirrored
	 * tag and its id (null when there is none), as JSON-RPC asks.  Every key
	 * is scanned from the line start: key order on the wire is not
	 * guaranteed. */
	cursor = lineP;
	sState.tagged = FindKey(&cursor, "jsonrpc"); /* the value is not checked */
	cursor = lineP;
	if (FindKey(&cursor, "id") && ParseIntSigned(&cursor, &id))
	{
		TermapyJson_SetId(id);
	}

	/* "cmd", else "method"; cmd wins when both are present. */
	cursor = lineP;
	if (!FindKey(&cursor, "cmd"))
	{
		cursor = lineP;
		if (!FindKey(&cursor, "method"))
		{
			return TERMAPY_JSON_IN_BAD;
		}
	}
	if (!ExpectQuote(&cursor) || !CopyJsonString(&cursor, cmdOutP, cmdOutCap))
	{
		return TERMAPY_JSON_IN_BAD;
	}

	/* Optional "args" (a string), else "params" (a string or a flat array):
	 * appended after a space so a space-splitting dispatcher sees "cmd args".
	 * Present but unusable (wrong type, too long for the copy or for the
	 * output) is BAD, never a silent drop. */
	cursor = lineP;
	if (FindKey(&cursor, "args"))
	{
		if (!AppendArgs(&cursor, cmdOutP, cmdOutCap, false))
		{
			return TERMAPY_JSON_IN_BAD;
		}
	}
	else
	{
		cursor = lineP;
		if (FindKey(&cursor, "params") && !AppendArgs(&cursor, cmdOutP, cmdOutCap, true))
		{
			return TERMAPY_JSON_IN_BAD;
		}
	}

	return TERMAPY_JSON_IN_JSON;
}

#endif /* TERMAPY_JSON_HAS_EXTRACT */
