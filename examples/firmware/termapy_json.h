/**
 * @file termapy_json.h
 * @brief Reference implementation of termapy's JSON reply wrapper for a
 *        device monitor.
 *
 * Lets a text command monitor answer termapy's NDJSON wire contract WITHOUT
 * rewriting its command table.  The idea is a typed "replacement for puts":
 * a handler hands its answer to a reply function, and one mode boolean
 * decides what reaches the serial line -- the bare value (text mode, today's
 * behaviour) or a JSON envelope.
 *
 *     fw        ->  SB_0_8                                (text mode)
 *     fw        ->  {"ok":true,"result":"SB_0_8"}         (JSON mode)
 *     tick      ->  12345            /  {"ok":true,"result":12345}
 *     temp_c    ->  21.5             /  {"ok":true,"result":21.5}
 *
 * The device decodes NOTHING by default: the host (termapy's REPL or MCP
 * server) already turns a request into the plain command line this monitor
 * understands, so the device only encodes replies.  A request parser for
 * {"cmd":...} lines is available behind TERMAPY_JSON_HAS_EXTRACT for a
 * monitor that wants symmetric JSON without a termapy host.
 *
 * The envelope matches termapy's NDJSON contract (src/termapy/demo_ndjson.py)
 * byte for byte, so a profile with a ``"format": "json"`` response parses
 * each reply into typed ``data``:
 *
 *     success  {"ok":true,"result":<value>,"id":<int>}
 *     error    {"ok":false,"error":"<msg>","code":<int>,"id":<int>}
 *
 * ``result`` is any JSON value: a string, a number, ``null``, or an array /
 * object streamed with the builder below.  Fields are compact (no spaces),
 * ``id`` is last, and ``id`` is OMITTED entirely when the request carried
 * none (not 0, not null).  JSON lines end in ``\n`` (NDJSON framing); text
 * lines end in ``\r\n`` (the sibling monitor convention, matching
 * termapy_mem.c).
 *
 * Three ways to answer, from cheapest to richest:
 *
 * 1. SCALAR replies.  One call, one value.  Reply (string), ReplyRaw (an
 *    already-valid JSON value), ReplyInt / ReplyUInt (numbers), ReplyFloat
 *    (number through an injected formatter), ReplyError.
 *
 * 2. STRUCTURED replies.  A streaming builder for tables and records --
 *    ArrayBegin / ObjectBegin, Item* / Field*, ArrayEnd / ObjectEnd.  The
 *    outermost Begin opens the envelope and the matching End closes it; the
 *    module does the comma and nesting bookkeeping, and nothing is buffered.
 *    In text mode the containers print nothing and each Item / Field prints
 *    one plain line (``value`` or ``key value``), so a handler may either
 *    call the builder unconditionally or branch on TermapyJson_GetMode() and
 *    keep its own text layout.
 *
 * 3. PASSTHROUGH for unconverted commands.  Route the monitor's ordinary
 *    text output through TermapyJson_PassthroughPut in JSON mode: the first
 *    byte opens ``{"ok":true,"result":"``, every byte is JSON-escaped, and
 *    TermapyJson_PassthroughEnd (called by the dispatcher after the handler
 *    returns) closes the envelope.  Every command then yields one valid JSON
 *    line with zero handler changes; converted handlers never touch the
 *    passthrough, so nothing opens for them.
 *
 * One reply per command.  A handler calls exactly one scalar reply, or one
 * balanced Begin..End group, or prints through the passthrough -- never a
 * mix, or the stream carries two envelopes for one request.
 *
 * Portable C99: only <stdbool.h>, <stdint.h>, <stddef.h>, <string.h>.  No
 * stdio, no malloc, no VLAs, no floating point of its own: ReplyFloat calls
 * the formatter the platform injects (or emits ``null`` when there is none),
 * so a device without a float printer links no float code.  Output streams
 * one char at a time through the injected ``put``; there is no result-length
 * limit and no output buffer.
 *
 * Integration with a monitor-style command table:
 *
 *     #include "termapy_json.h"
 *
 *     static bool FmtFloat(double v, uint8_t decimals, char *bufP, size_t cap)
 *     {
 *         return snprintf(bufP, cap, "%.*f", (int)decimals, v) > 0;   // or NULL
 *     }
 *     static const TermapyJson_Ops sJsonOps = { MyUartPut, FmtFloat };
 *
 *     static void CmdFw(const char *argP)   { (void)argP; TermapyJson_Reply("SB_0_8"); }
 *     static void CmdTick(const char *argP) { (void)argP; TermapyJson_ReplyUInt(TickGet()); }
 *     static void CmdTemp(const char *argP)
 *     {
 *         (void)argP;
 *         if (!ReadTemp(&degC)) { TermapyJson_ReplyError("sensor fault", 5); return; }
 *         TermapyJson_ReplyFloat(degC, 1);
 *     }
 *     static void CmdHwm(const char *argP)                    // a table
 *     {
 *         (void)argP;
 *         TermapyJson_ArrayBegin();
 *         for (each row) {
 *             TermapyJson_ObjectBegin();
 *             TermapyJson_FieldString("name", row.nameP);
 *             TermapyJson_FieldUInt("used", row.used);
 *             TermapyJson_FieldUInt("max", row.max);
 *             TermapyJson_ObjectEnd();
 *         }
 *         TermapyJson_ArrayEnd();
 *     }
 *     static void CmdJson(const char *argP)                   // the mode switch
 *     {
 *         if (*argP != '\0') { TermapyJson_SetMode(ParseBool(argP) ? TERMAPY_JSON_JSON : TERMAPY_JSON_TEXT); }
 *         TermapyJson_Reply(TermapyJson_GetMode() == TERMAPY_JSON_JSON ? "on" : "off");
 *     }
 *
 *     // The putc your text printer already uses: in JSON mode go through the
 *     // passthrough so unconverted commands still produce one JSON line.
 *     static void MonitorPutc(int c) { (void)TermapyJson_PassthroughPut(c); }
 *
 *     void OnLine(const char *lineP)         // one call per received line
 *     {
 *         Dispatch(lineP);                   // your existing verb/arg split + table
 *         TermapyJson_PassthroughEnd();      // closes an envelope the passthrough opened
 *     }
 *
 *     int main(void) { TermapyJson_Init(&sJsonOps); ... }
 */

#ifndef TERMAPY_JSON_H
#define TERMAPY_JSON_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── Configuration (override with -D) ──────────────────────────────────── */

/** Compile the {"cmd":...} request extractor (TermapyJson_Extract) and its
 *  helpers.  Default on for parity with termapy's NDJSON reference device.
 *  A monitor driven by termapy's REPL receives plain text and can set this
 *  to 0: the host already did the decoding.  Unreferenced API is also
 *  removed by --gc-sections, so this knob matters only for footprint
 *  reporting and for the glue a monitor would otherwise write. */
#ifndef TERMAPY_JSON_HAS_EXTRACT
#define TERMAPY_JSON_HAS_EXTRACT 1
#endif

/** Most decimal digits an echoed request id may carry; bounds the id parse
 *  so a runaway digit run cannot overflow.  10 holds any 32-bit id. */
#ifndef TERMAPY_JSON_ID_MAX_DIGITS
#define TERMAPY_JSON_ID_MAX_DIGITS 10u
#endif
#if TERMAPY_JSON_ID_MAX_DIGITS < 1 || TERMAPY_JSON_ID_MAX_DIGITS > 18
#error "TERMAPY_JSON_ID_MAX_DIGITS must be between 1 and 18"
#endif

/** Size of the copy the extractor makes of a request's "args" string, NUL
 *  included.  A longer "args" answers TERMAPY_JSON_IN_BAD (never silently
 *  truncated).  Stack, only while Extract runs. */
#ifndef TERMAPY_JSON_ARGS_MAX
#define TERMAPY_JSON_ARGS_MAX 64u
#endif
#if TERMAPY_JSON_ARGS_MAX < 2
#error "TERMAPY_JSON_ARGS_MAX must be at least 2"
#endif

/** Size of the buffer handed to the injected float formatter, NUL included.
 *  24 holds any double printed with up to 15 decimals. */
#ifndef TERMAPY_JSON_FLOAT_BUF
#define TERMAPY_JSON_FLOAT_BUF 24u
#endif
#if TERMAPY_JSON_FLOAT_BUF < 8
#error "TERMAPY_JSON_FLOAT_BUF must be at least 8"
#endif

/** Line ending for TEXT-mode replies.  \r\n matches termapy_mem.c and the
 *  usual monitor convention; a bare puts replacement wants what puts gives. */
#ifndef TERMAPY_JSON_TEXT_EOL
#define TERMAPY_JSON_TEXT_EOL "\r\n"
#endif

/** Line ending for JSON-mode replies.  \n: NDJSON frames one object per line
 *  terminated by a single newline (see src/termapy/demo_ndjson.py). */
#ifndef TERMAPY_JSON_LINE_EOL
#define TERMAPY_JSON_LINE_EOL "\n"
#endif

/** Deepest container nesting the builder tracks commas for.  Fixed by the
 *  32-bit mask that holds one "has an item" flag per level. */
#define TERMAPY_JSON_MAX_DEPTH 32u

/* ── Types ─────────────────────────────────────────────────────────────── */

/**
 * @brief The output framing.  One boolean under the hood: every writer
 *        branches on it to pick the plain-text form or the JSON envelope.
 */
typedef enum {
	TERMAPY_JSON_TEXT = 0, /* bare value + \r\n, today's behaviour (default) */
	TERMAPY_JSON_JSON = 1  /* {"ok":...,"result":...,"id":N} + \n            */
} TermapyJson_Mode;

/**
 * @brief Format a double for the wire.
 *
 * Writes a plain decimal number (``-12.5``, ``3``, ``1e-6``) into ``bufP``,
 * NUL-terminated, at most ``cap`` bytes, honouring ``decimals`` as the caller
 * asked for them.  Returns false to decline (nothing printable), in which
 * case JSON mode emits ``null`` and text mode an empty value.  No padding
 * and no ``nan`` / ``inf``: JSON has no spelling for them, and the module
 * replaces any output that is not a plain number with ``null``.
 */
typedef bool (*TermapyJson_FmtFloat)(double value, uint8_t decimals, char *bufP, size_t cap);

/**
 * @brief The platform: serial output, and optionally a float formatter.
 */
typedef struct {
	/** Serial output, putchar-compatible.  Return value ignored: a blocking
	 *  TX has nothing to report, and truncation shows up host-side as an
	 *  incomplete reply.  Same shape as TermapyMem_Ops.put. */
	int (*put)(int c);

	/** Float formatter, or NULL.  Only ReplyFloat / ItemFloat / FieldFloat
	 *  use it, so a device that leaves it NULL links no float code and those
	 *  calls emit ``null`` (JSON) or an empty value (text). */
	TermapyJson_FmtFloat fmtFloat;
} TermapyJson_Ops;

#if TERMAPY_JSON_HAS_EXTRACT
/**
 * @brief The result of inspecting one received line.
 */
typedef enum {
	TERMAPY_JSON_IN_TEXT = 0, /* not a JSON object; dispatch the line as-is   */
	TERMAPY_JSON_IN_JSON = 1, /* a {"cmd":...} object; command extracted      */
	TERMAPY_JSON_IN_BAD = 2   /* looked like JSON ('{') but was malformed     */
} TermapyJson_InResult;
#endif

/* ── Lifecycle, mode and id ────────────────────────────────────────────── */

/**
 * @brief Install the platform.  Once, at boot, before any reply.
 *
 * The struct is copied, so a temporary is fine; a partial initializer
 * ``{ MyPut }`` leaves ``fmtFloat`` NULL.  Until this is called, or if
 * ``put`` is NULL, every writer emits nothing -- there is no output path to
 * answer on -- which a host reports as a device without JSON support.  Also
 * resets the builder and passthrough state.
 *
 * @param opsP The platform operations; copied, not retained.
 */
void TermapyJson_Init(const TermapyJson_Ops *opsP);

/** @brief Set the output mode (the framing boolean). */
void TermapyJson_SetMode(TermapyJson_Mode mode);

/** @brief The current output mode. */
TermapyJson_Mode TermapyJson_GetMode(void);

/**
 * @brief A ready-made monitor command body that flips the mode boolean.
 *
 * Register it in your command table (e.g. as ``json``) so a human at the
 * text prompt can switch framing with one command.  It simply toggles
 * text <-> JSON; there is no argument grammar and no status reply.  A
 * monitor that wants ``json on|off`` with a query writes its own two-line
 * handler around SetMode / GetMode, as in the header example.
 *
 * @param argP Ignored; present so the signature matches a command handler.
 */
void TermapyJson_ModeCmd(const char *argP);

/**
 * @brief Set the request id echoed by the next JSON reply.
 *
 * The extractor calls this with the inbound ``"id"`` (or ClearId when the
 * line had none).  A monitor may call it directly for a request that
 * carries an id out of band.  Ignored in text mode.
 */
void TermapyJson_SetId(long id);

/** @brief Drop the current id, so the next JSON reply omits ``"id"``. */
void TermapyJson_ClearId(void);

/* ── Scalar replies: one call, one value ───────────────────────────────── */

/**
 * @brief Reply with a STRING result -- the common case.
 *
 * TEXT: ``resultP`` then the text EOL, exactly like puts.  JSON:
 * ``{"ok":true,"result":"<escaped>","id":N}``.
 */
void TermapyJson_Reply(const char *resultP);

/** @brief Spelled-out alias of Reply, where the call site wants the intent. */
void TermapyJson_ReplyString(const char *resultP);

/**
 * @brief Reply with a RAW JSON result value.
 *
 * ``rawResultP`` is spliced verbatim as the value of ``"result"`` (a number,
 * ``true`` / ``false`` / ``null``, a pre-formatted array or object).  NOT
 * quoted and NOT escaped; the caller owns its validity.  TEXT emits it
 * bare, identical to Reply -- raw and string differ only in JSON mode.
 */
void TermapyJson_ReplyRaw(const char *rawResultP);

/** @brief Reply with a signed integer: TEXT ``-7``, JSON ``"result":-7``. */
void TermapyJson_ReplyInt(long value);

/** @brief Reply with an unsigned integer (ticks, counters, sizes). */
void TermapyJson_ReplyUInt(unsigned long value);

/**
 * @brief Reply with a floating-point number through the injected formatter.
 *
 * ``decimals`` is passed to the formatter.  With no formatter, or when it
 * declines or produces something that is not a plain number, JSON emits
 * ``null`` and TEXT an empty value.
 */
void TermapyJson_ReplyFloat(double value, uint8_t decimals);

/**
 * @brief Reply with an error.
 *
 * TEXT: ``msgP`` then the text EOL -- bare, transparent like puts (add your
 * own ``ERR`` prefix in the message if you want termapy_mem parity).  JSON:
 * ``{"ok":false,"error":"<escaped>","code":<code>,"id":N}``.  A convention
 * that works: negative codes for protocol and usage errors (JSON-RPC
 * numbers: -32601 unknown command, -32602 bad arguments, -32700 parse),
 * positive codes for a device error code passed straight through.
 */
void TermapyJson_ReplyError(const char *msgP, int code);

/* ── Structured replies: a streaming array / object builder ────────────── */

/**
 * @brief Open an array (``[``) or object (``{``) as the reply or as the next
 *        item of the enclosing container.
 *
 * The OUTERMOST Begin emits the envelope head ``{"ok":true,"result":``; the
 * matching End emits the id tail, ``}`` and the line end.  Nested Begins are
 * items of their parent and get their comma automatically.  In TEXT mode the
 * containers print nothing.  Nest at most TERMAPY_JSON_MAX_DEPTH levels.
 */
void TermapyJson_ArrayBegin(void);
void TermapyJson_ArrayEnd(void);
void TermapyJson_ObjectBegin(void);
void TermapyJson_ObjectEnd(void);

/** @brief Open a keyed array / object inside an object: ``"regions":[``. */
void TermapyJson_FieldArrayBegin(const char *keyP);
void TermapyJson_FieldObjectBegin(const char *keyP);

/**
 * @brief Array items.  JSON: the value, comma-separated.  TEXT: the value
 *        and the text EOL, one line each.  Outside any container an Item is
 *        the same as the matching Reply.
 */
void TermapyJson_ItemString(const char *valueP);
void TermapyJson_ItemRaw(const char *rawValueP);
void TermapyJson_ItemInt(long value);
void TermapyJson_ItemUInt(unsigned long value);
void TermapyJson_ItemFloat(double value, uint8_t decimals);

/**
 * @brief Object fields.  JSON: ``"key":value``, comma-separated.  TEXT:
 *        ``key value`` and the text EOL.  Outside any container the key is
 *        dropped and the call behaves like the matching Reply.
 */
void TermapyJson_FieldString(const char *keyP, const char *valueP);
void TermapyJson_FieldRaw(const char *keyP, const char *rawValueP);
void TermapyJson_FieldInt(const char *keyP, long value);
void TermapyJson_FieldUInt(const char *keyP, unsigned long value);
void TermapyJson_FieldFloat(const char *keyP, double value, uint8_t decimals);

/* ── Passthrough: envelope for commands that still print text ──────────── */

/**
 * @brief putchar-compatible output for a monitor's ordinary text printer.
 *
 * TEXT mode: forwards the byte unchanged.  JSON mode: the first byte emits
 * ``{"ok":true,"result":"``, then every byte is JSON-escaped into that
 * string.  A single trailing newline is dropped at PassthroughEnd (a puts
 * style ``value\n`` becomes ``"value"``); inner newlines are kept as ``\n``.
 * Returns the byte, or -1 before Init.  Expects a line feed to be ``\n``
 * only: do your ``\r\n`` translation in text mode, not before this call.
 */
int TermapyJson_PassthroughPut(int c);

/**
 * @brief Close the passthrough envelope if one is open; otherwise nothing.
 *
 * Call it once after every dispatched command (in the dispatcher, after
 * the handler returns).  Converted handlers never open the passthrough, so
 * for them it is a no-op.
 */
void TermapyJson_PassthroughEnd(void);

/* ── Request extractor (optional, TERMAPY_JSON_HAS_EXTRACT) ────────────── */

#if TERMAPY_JSON_HAS_EXTRACT
/**
 * @brief Inspect one received line; if a JSON command object, extract it.
 *
 * Skips leading whitespace.  If the first non-space is not ``{`` the line is
 * plain text: the pending id is cleared, nothing else is touched, and
 * TERMAPY_JSON_IN_TEXT is returned so the caller dispatches it unchanged.
 *
 * Otherwise the line is treated as a JSON command object and output flips to
 * JSON mode immediately (so even a malformed object is answered in JSON, as
 * termapy's NDJSON peer expects).  On success the command name is copied into
 * ``cmdOutP`` (bounded by ``cmdOutCap``); an optional string ``"args"`` is
 * appended after a space (``{"cmd":"echo","args":"hi"}`` -> ``echo hi``); an
 * optional integer ``"id"`` is captured for the reply (absent -> cleared).
 * Returns TERMAPY_JSON_IN_JSON.
 *
 * A ``{`` line with no usable ``"cmd"`` string, an unterminated string, an
 * ``"args"`` that is not a string or does not fit TERMAPY_JSON_ARGS_MAX, or
 * a command plus args longer than ``cmdOutCap`` returns TERMAPY_JSON_IN_BAD;
 * the caller answers ReplyError (already in JSON mode).  Never crashes, never
 * reads past the terminating NUL, never silently drops part of a request.
 *
 * NOTE: ``"cmd"`` / ``"args"`` / ``"id"`` are found by exact substring, not a
 * full JSON parse (deliberate, for a tiny footprint).  A string value
 * elsewhere that literally contains ``"cmd"`` could mislead it; the monitor's
 * own well-formed command lines never do.
 *
 * @param lineP     The received line (NUL-terminated).
 * @param cmdOutP   Buffer for the extracted ``cmd`` (+ inline args).
 * @param cmdOutCap Size of ``cmdOutP`` in bytes, including the NUL.
 */
TermapyJson_InResult TermapyJson_Extract(const char *lineP, char *cmdOutP, size_t cmdOutCap);
#endif /* TERMAPY_JSON_HAS_EXTRACT */

#ifdef __cplusplus
}
#endif

#endif /* TERMAPY_JSON_H */
