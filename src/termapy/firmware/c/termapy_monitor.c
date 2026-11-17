/**
 * @file termapy_monitor.c
 * @brief A complete command monitor for a C device; see termapy_monitor.h.
 */

#include "termapy_monitor.h"

#include <string.h>

#include "termapy_json.h"

#if !TERMAPY_JSON_HAS_EXTRACT
#error "termapy_monitor.c reads JSON requests: build termapy_json.c with TERMAPY_JSON_HAS_EXTRACT 1"
#endif

/* -- State --------------------------------------------------------------- */

/** The longest message a refusal builds (an argument helper's). */
#define MESSAGE_MAX 128u

/** The monitor's own commands, answered before the table is looked at. */
#define BUILTIN_COUNT 2u
static const char *const sBuiltinNames[BUILTIN_COUNT] = { "help", "json" };
static const char *const sBuiltinHelp[BUILTIN_COUNT] = {
	"list the commands; help <command> for one",
	"the reply framing: on for JSON-RPC, off for text",
};

static const TermapyMonitor_Command *sTableP; /* the device's commands      */
static size_t sCount;                         /* how many                   */

static char sLine[TERMAPY_MONITOR_LINE_MAX + 1u];    /* the line being received */
static size_t sLength;                               /* its length so far       */
static bool sTooLong;                                /* ran past LINE_MAX       */
static char sRequest[TERMAPY_MONITOR_LINE_MAX + 1u]; /* a JSON request's line   */
static char sMessage[MESSAGE_MAX];                   /* a refusal being built   */

/* -- Text helpers -------------------------------------------------------- */

static bool IsSpace(char c)
{
	return c == ' ' || c == '\t';
}

/** Write ``textP`` as it stands; for TEXT framing, where the passthrough forwards. */
static void Text(const char *textP)
{
	while (*textP != '\0') {
		(void)TermapyJson_PassthroughPut((unsigned char)*textP);
		textP++;
	}
}

/** Append up to ``count`` characters of ``textP`` to sMessage, cut to fit. */
static void AppendN(const char *textP, size_t count)
{
	size_t at = strlen(sMessage);
	while (count > 0u && *textP != '\0' && at + 1u < MESSAGE_MAX) {
		sMessage[at++] = *textP++;
		count--;
	}
	sMessage[at] = '\0';
}

static void Append(const char *textP)
{
	AppendN(textP, (size_t)-1);
}

/** Append ``value`` in decimal. */
static void AppendLong(long value)
{
	char digits[24];
	size_t at = sizeof digits - 1u;
	/* Through unsigned, so the most negative long has a magnitude. */
	unsigned long magnitude = value < 0 ? 0ul - (unsigned long)value : (unsigned long)value;
	digits[at] = '\0';
	do {
		digits[--at] = (char)('0' + (int)(magnitude % 10u));
		magnitude /= 10u;
	} while (magnitude != 0u);
	if (value < 0) {
		digits[--at] = '-';
	}
	Append(&digits[at]);
}

/** Whether the ``length`` characters at ``nameP`` are exactly ``wordP``. */
static bool Is(const char *nameP, size_t length, const char *wordP)
{
	return strncmp(nameP, wordP, length) == 0 && wordP[length] == '\0';
}

/** The first word of ``textP`` is exactly ``wordP``. */
static bool FirstWordIs(const char *textP, const char *wordP)
{
	size_t length = 0u;
	while (textP[length] != '\0' && !IsSpace(textP[length])) {
		length++;
	}
	return Is(textP, length, wordP);
}

/* -- The command table --------------------------------------------------- */

/** How many commands ``help`` lists: the monitor's own, then the table's. */
static size_t Total(void)
{
	return BUILTIN_COUNT + sCount;
}

static const char *NameAt(size_t index)
{
	return index < BUILTIN_COUNT ? sBuiltinNames[index] : sTableP[index - BUILTIN_COUNT].nameP;
}

static const char *HelpAt(size_t index)
{
	const char *helpP =
		index < BUILTIN_COUNT ? sBuiltinHelp[index] : sTableP[index - BUILTIN_COUNT].helpP;
	return helpP != NULL ? helpP : "";
}

/** The index of the command the ``length`` characters at ``nameP`` name, or Total(). */
static size_t Find(const char *nameP, size_t length)
{
	size_t index;
	for (index = 0u; index < Total(); index++) {
		if (Is(nameP, length, NameAt(index))) {
			return index;
		}
	}
	return Total();
}

/** The index of the smallest name after ``afterP`` (NULL: the first), or Total(). */
static size_t NextInOrder(const char *afterP)
{
	size_t best = Total();
	size_t index;
	for (index = 0u; index < Total(); index++) {
		const char *nameP = NameAt(index);
		if (afterP != NULL && strcmp(nameP, afterP) <= 0) {
			continue;
		}
		if (best == Total() || strcmp(nameP, NameAt(best)) < 0) {
			best = index;
		}
	}
	return best;
}

bool TermapyMonitor_Init(const TermapyMonitor_Command *tableP, size_t count)
{
	size_t index;
	size_t other;
	sTableP = NULL;
	sCount = 0u;
	sLength = 0u;
	sTooLong = false;
	if (tableP == NULL && count != 0u) {
		return false;
	}
	for (index = 0u; index < count; index++) {
		const char *nameP = tableP[index].nameP;
		if (nameP == NULL || nameP[0] == '\0' || tableP[index].handler == NULL) {
			return false;
		}
		for (other = 0u; other < BUILTIN_COUNT; other++) {
			if (strcmp(nameP, sBuiltinNames[other]) == 0) {
				return false;
			}
		}
		for (other = 0u; other < index; other++) {
			if (strcmp(nameP, tableP[other].nameP) == 0) {
				return false;
			}
		}
	}
	sTableP = tableP;
	sCount = count;
	return true;
}

/* -- The monitor's own commands ------------------------------------------ */

/** ``help``: every line in name order; ``help <command>``: that command's. */
static void Help(const char *argsP)
{
	size_t index;
	size_t width = 0u;
	const char *previousP = NULL;
	if (*argsP != '\0') {
		size_t length = 0u;
		const char *restP;
		while (argsP[length] != '\0' && !IsSpace(argsP[length])) {
			length++;
		}
		restP = argsP + length;
		while (IsSpace(*restP)) {
			restP++;
		}
		if (*restP != '\0') {
			TermapyMonitor_Fail("usage: help {command}", TERMAPY_MONITOR_BAD_ARGS);
			return;
		}
		index = Find(argsP, length);
		if (index == Total()) {
			sMessage[0] = '\0';
			Append("unknown command: ");
			AppendN(argsP, length);
			TermapyMonitor_Fail(sMessage, TERMAPY_MONITOR_BAD_ARGS);
			return;
		}
		TermapyJson_Reply(HelpAt(index));
		return;
	}
	if (TermapyJson_GetMode() == TERMAPY_JSON_JSON) {
		TermapyJson_ObjectBegin();
		for (index = NextInOrder(NULL); index != Total(); index = NextInOrder(previousP)) {
			previousP = NameAt(index);
			TermapyJson_FieldString(previousP, HelpAt(index));
		}
		TermapyJson_ObjectEnd();
		return;
	}
	/* Text is for a person: the help lines aligned in one column. */
	for (index = 0u; index < Total(); index++) {
		size_t length = strlen(NameAt(index));
		width = length > width ? length : width;
	}
	for (index = NextInOrder(NULL); index != Total(); index = NextInOrder(previousP)) {
		size_t pad;
		previousP = NameAt(index);
		Text(previousP);
		for (pad = strlen(previousP); pad < width + 2u; pad++) {
			Text(" ");
		}
		Text(HelpAt(index));
		Text(TERMAPY_JSON_TEXT_EOL);
	}
}

/** ``json``: the framing in force, after ``json on|off`` switches it. */
static void Framing(const char *argsP)
{
	if (*argsP != '\0') {
		/* Anything but on is off, failing closed. */
		TermapyJson_SetMode(FirstWordIs(argsP, "on") ? TERMAPY_JSON_JSON : TERMAPY_JSON_TEXT);
	}
	TermapyJson_Reply(TermapyJson_GetMode() == TERMAPY_JSON_JSON ? "on" : "off");
}

/* -- Answering a line ---------------------------------------------------- */

/** Answer the line in ``lineP``, which may be trimmed in place. */
static void Respond(char *lineP)
{
	char *endP;
	const char *textP;
	const char *argsP;
	size_t length = 0u;
	size_t index;
	TermapyJson_InResult in;

	while (IsSpace(*lineP)) {
		lineP++;
	}
	endP = lineP + strlen(lineP);
	while (endP > lineP && IsSpace(endP[-1])) {
		endP--;
	}
	*endP = '\0';
	if (*lineP == '\0') {
		return;
	}
	/* A plain line comes back as itself and clears the last request's id. */
	in = TermapyJson_Extract(lineP, sRequest, sizeof sRequest);
	if (in == TERMAPY_JSON_IN_BAD) {
		TermapyMonitor_Fail("malformed json", TERMAPY_MONITOR_PARSE_ERROR);
		return;
	}
	textP = in == TERMAPY_JSON_IN_JSON ? sRequest : lineP;
	while (IsSpace(*textP)) {
		textP++;
	}
	while (textP[length] != '\0' && !IsSpace(textP[length])) {
		length++;
	}
	argsP = textP + length;
	while (IsSpace(*argsP)) {
		argsP++;
	}
	index = Find(textP, length);
	if (index == Total()) {
		sMessage[0] = '\0';
		Append("unknown command: ");
		AppendN(textP, length);
		TermapyMonitor_Fail(sMessage, TERMAPY_MONITOR_UNKNOWN_COMMAND);
	} else if (index == 0u) {
		Help(argsP);
	} else if (index == 1u) {
		Framing(argsP);
	} else {
		const TermapyMonitor_Command *commandP = &sTableP[index - BUILTIN_COUNT];
		if (!commandP->native) {
			commandP->handler(argsP);
		} else if (in == TERMAPY_JSON_IN_JSON) {
			/* The text, through NativePut, becomes the result string. */
			commandP->handler(textP);
		} else {
			/* A plain line gets the native text whatever the framing. */
			TermapyJson_Mode framing = TermapyJson_GetMode();
			TermapyJson_SetMode(TERMAPY_JSON_TEXT);
			commandP->handler(textP);
			TermapyJson_SetMode(framing);
		}
	}
	/* Closes an envelope a handler's passthrough output opened; else nothing. */
	TermapyJson_PassthroughEnd();
}

void TermapyMonitor_Answer(const char *lineP)
{
	size_t length = strlen(lineP);
	if (length > TERMAPY_MONITOR_LINE_MAX) {
		TermapyJson_ClearId();
		TermapyMonitor_Fail("line too long", TERMAPY_MONITOR_PARSE_ERROR);
		return;
	}
	if (lineP != sLine) {
		memcpy(sLine, lineP, length + 1u);
	}
	Respond(sLine);
}

bool TermapyMonitor_Feed(int c)
{
	if (c < 0) {
		return false;
	}
	if (c == '\r' || c == '\n') {
		if (sTooLong) {
			sTooLong = false;
			sLength = 0u;
			TermapyJson_ClearId();
			TermapyMonitor_Fail("line too long", TERMAPY_MONITOR_PARSE_ERROR);
			return true;
		}
		if (sLength == 0u) {
			return false;
		}
		sLine[sLength] = '\0';
		sLength = 0u;
		Respond(sLine);
		return true;
	}
	if (sTooLong) {
		return false;
	}
	if (sLength >= TERMAPY_MONITOR_LINE_MAX) {
		/* Dropped now, not at the line ending, so the buffer is never overrun. */
		sLength = 0u;
		sTooLong = true;
		return false;
	}
	sLine[sLength++] = (char)c;
	return false;
}

/* -- For handlers -------------------------------------------------------- */

void TermapyMonitor_Fail(const char *msgP, int code)
{
	if (TermapyJson_GetMode() == TERMAPY_JSON_TEXT) {
		Text("ERR ");
	}
	TermapyJson_ReplyError(msgP, code);
}

int TermapyMonitor_NativePut(int c)
{
	if (c == '\r' && TermapyJson_GetMode() == TERMAPY_JSON_JSON) {
		return c;
	}
	return TermapyJson_PassthroughPut(c);
}

const char *TermapyMonitor_NextWord(const char *textP, char *wordP, size_t cap)
{
	size_t length = 0u;
	if (cap > 0u) {
		wordP[0] = '\0';
	}
	if (textP == NULL) {
		return NULL;
	}
	while (IsSpace(*textP)) {
		textP++;
	}
	if (*textP == '\0') {
		return NULL;
	}
	while (*textP != '\0' && !IsSpace(*textP)) {
		if (length + 1u < cap) {
			wordP[length++] = *textP;
		}
		textP++;
	}
	if (cap > 0u) {
		wordP[length] = '\0';
	}
	return textP;
}

/** The value of one digit in ``base``, or -1. */
static int Digit(char c, unsigned base)
{
	int value = -1;
	if (c >= '0' && c <= '9') {
		value = c - '0';
	} else if (c >= 'a' && c <= 'f') {
		value = c - 'a' + 10;
	} else if (c >= 'A' && c <= 'F') {
		value = c - 'A' + 10;
	}
	return value >= 0 && (unsigned)value < base ? value : -1;
}

bool TermapyMonitor_IntArg(const char *wordP, const char *whatP, long low, long high, long *valueP)
{
	const unsigned long longMax = (unsigned long)-1 >> 1;
	const char *p = wordP;
	unsigned long magnitude = 0u;
	unsigned base = 10u;
	bool negative = false;
	bool valid = true;
	long value = 0;

	if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
		base = 16u;
		p += 2;
	} else if (*p == '-' || *p == '+') {
		negative = *p == '-';
		p++;
	}
	valid = *p != '\0';
	for (; valid && *p != '\0'; p++) {
		int digit = Digit(*p, base);
		if (digit < 0 || magnitude > (longMax + 1u - (unsigned long)digit) / base) {
			valid = false;
		} else {
			magnitude = magnitude * base + (unsigned long)digit;
		}
	}
	if (valid && !negative && magnitude > longMax) {
		valid = false;
	}
	if (valid) {
		value = negative ? (long)(0ul - magnitude) : (long)magnitude;
		valid = value >= low && value <= high;
	}
	if (!valid) {
		sMessage[0] = '\0';
		Append("invalid ");
		Append(whatP);
		Append(": ");
		Append(wordP);
		Append(" (use ");
		AppendLong(low);
		Append(" to ");
		AppendLong(high);
		Append(")");
		TermapyMonitor_Fail(sMessage, TERMAPY_MONITOR_BAD_ARGS);
		return false;
	}
	*valueP = value;
	return true;
}

bool TermapyMonitor_OnOffArg(const char *wordP, const char *whatP, bool *stateP)
{
	if (strcmp(wordP, "on") == 0 || strcmp(wordP, "1") == 0) {
		*stateP = true;
		return true;
	}
	if (strcmp(wordP, "off") == 0 || strcmp(wordP, "0") == 0) {
		*stateP = false;
		return true;
	}
	sMessage[0] = '\0';
	Append("invalid ");
	Append(whatP);
	Append(": ");
	Append(wordP);
	Append(" (use on, off, 1 or 0)");
	TermapyMonitor_Fail(sMessage, TERMAPY_MONITOR_BAD_ARGS);
	return false;
}
