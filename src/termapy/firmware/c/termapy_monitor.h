/**
 * @file termapy_monitor.h
 * @brief A complete command monitor for a C device: lines in, one reply each.
 *
 * The C twin of the monitor termapy's MicroPython starters run
 * (boards/micropython/firmware/termapy_json.py), on the same wire, so
 * termapy, an MCP agent or any JSON-RPC client talks to a board running
 * this exactly as to one running MicroPython.  Feed it the bytes the serial
 * link receives; at each line ending it answers the line:
 *
 *     led on        ->  on                          (text framing)
 *     {"cmd":"led","args":"on","id":7}
 *                   ->  {"result":"on","id":7}       (JSON framing)
 *     nosuch        ->  ERR unknown command: nosuch
 *
 * A device is a table of commands, each a name, a one-line help and a
 * handler.  A handler answers with exactly one termapy_json.h reply
 * (TermapyJson_Reply, ReplyInt, ReplyFloat, an array or object built with
 * the builder) or refuses with TermapyMonitor_Fail; the framing is the
 * monitor's business, never the handler's.  The monitor answers two
 * commands itself:
 *
 * - ``help`` answers every command's one-line help, sorted by name: in JSON
 *   an object of name to help, in text one aligned ``name  help`` line each.
 *   ``help <command>`` answers that command's line.
 * - ``json on|off`` switches framing (anything but ``on`` means off,
 *   failing closed); bare ``json`` answers the framing.  A line starting
 *   with ``{`` is a JSON request and switches to JSON framing by itself
 *   (TermapyJson_Extract): ``{"cmd":..}`` or ``{"method":..}``, with
 *   ``"args"`` or ``"params"``, and its ``"id"`` echoed in the reply.
 *
 * A NATIVE command has a text grammar of its own (termapy_mem.c's MEM wire
 * spec): its handler gets the whole line and writes its own text lines
 * through TermapyMonitor_NativePut.  A plain line gets that text whatever
 * the framing, which is how termapy's /mem.* reads it; a JSON request gets
 * the text as the result string.  (The MicroPython monitor answers a JSON
 * request with the lines as an array and an ``ERR`` line as an error; this
 * one buffers nothing, so the text is the result as written.)
 *
 * Failures are the contract's: -32700 a malformed request or a line longer
 * than TERMAPY_MONITOR_LINE_MAX, -32601 an unknown command, -32602 bad
 * arguments.  In text a failure is ``ERR <message>``; in JSON it is the
 * JSON-RPC error object.  A message the monitor builds (``unknown command:
 * <name>``, an argument helper's) is cut at 127 characters.
 *
 * Integration, on top of termapy_json.c (and termapy_mem.c for MEM):
 *
 *     #include "termapy_json.h"
 *     #include "termapy_monitor.h"
 *
 *     static void CmdTick(const char *argsP) { (void)argsP; TermapyJson_ReplyUInt(TickGet()); }
 *
 *     static const TermapyMonitor_Command sCommands[] = {
 *         { "tick", "the tick counter", CmdTick, false },
 *         { "MEM.R", "read memory: MEM.R <addr> <len>", MemNative, true },
 *     };
 *
 *     // At boot, once: the platform for the replies, then the table.
 *     TermapyJson_Init(&sJsonOps);
 *     TermapyMonitor_Init(sCommands, sizeof sCommands / sizeof sCommands[0]);
 *
 *     // For every byte the link receives:
 *     TermapyMonitor_Feed(UartGetc());
 *
 * Portable C99: only <stdbool.h>, <stdint.h>, <stddef.h>, <string.h>.  No
 * stdio, no malloc; RAM is one TERMAPY_MONITOR_LINE_MAX line buffer and one
 * request buffer of the same size, both static.
 */

#ifndef TERMAPY_MONITOR_H
#define TERMAPY_MONITOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -- Configuration (override with -D) ------------------------------------ */

/** The longest line the monitor takes, in characters.  A longer line is
 *  refused whole with ``line too long`` (-32700), so a link that never sends
 *  a line ending cannot run past the buffer.  512 holds the longest MEM.W
 *  line (termapy_mem.h's TERMAPY_MEM_LINE_SIZE) wrapped in a JSON request,
 *  as the MicroPython monitor's LINE_MAX does. */
#ifndef TERMAPY_MONITOR_LINE_MAX
#define TERMAPY_MONITOR_LINE_MAX 512u
#endif
#if TERMAPY_MONITOR_LINE_MAX < 16
#error "TERMAPY_MONITOR_LINE_MAX must be at least 16"
#endif

/* -- Error codes: the JSON-RPC numbers the contract uses ----------------- */

#define TERMAPY_MONITOR_PARSE_ERROR (-32700)     /* malformed request, line too long */
#define TERMAPY_MONITOR_UNKNOWN_COMMAND (-32601) /* no command by that name            */
#define TERMAPY_MONITOR_BAD_ARGS (-32602)        /* a handler refused its arguments    */

/* -- Types --------------------------------------------------------------- */

/**
 * @brief Answers one command.
 *
 * An ordinary command gets its arguments: the line after the name and the
 * spaces that follow it, ``""`` when there are none.  A native command gets
 * the whole line, its name included.
 */
typedef void (*TermapyMonitor_Handler)(const char *argsP);

/** @brief One row of the device's command table. */
typedef struct {
	const char *nameP;              /* the line's first word, e.g. "led"           */
	const char *helpP;              /* one line for ``help``                       */
	TermapyMonitor_Handler handler; /* answers with exactly one reply              */
	bool native;                    /* its own text grammar, through NativePut     */
} TermapyMonitor_Command;

/* -- Lifecycle and input ------------------------------------------------- */

/**
 * @brief Install the device's command table.  Once, at boot, after
 *        TermapyJson_Init.
 *
 * The table is retained, not copied: make it static const.  Refuses a table
 * with a row that has no name or handler, a name that is ``help`` or ``json``
 * (the monitor's own) or a name twice, and then installs none of it: ``help``
 * lists only the monitor's own commands and every other line is an unknown
 * command, so the mistake shows on the first try rather than hiding one row.
 *
 * @param tableP The commands, in any order; ``help`` sorts them.
 * @param count  How many there are.
 * @return true when the table was installed.
 */
bool TermapyMonitor_Init(const TermapyMonitor_Command *tableP, size_t count);

/**
 * @brief Take one received byte; at a line ending, answer the line.
 *
 * CR, LF or both end a line, and a line with nothing in it is ignored.
 *
 * @param c The byte; a negative value (``Serial.read()`` with nothing
 *          waiting) is ignored.
 * @return true when the byte ended a line that was answered.
 */
bool TermapyMonitor_Feed(int c);

/**
 * @brief Answer one whole line, as Feed does at a line ending.
 *
 * For a device that assembles lines itself.  Leading and trailing spaces
 * are ignored, and a blank line answers nothing.
 */
void TermapyMonitor_Answer(const char *lineP);

/* -- For handlers -------------------------------------------------------- */

/**
 * @brief Refuse the command: ``ERR <message>`` in text, an error object in
 *        JSON.  The handler's one reply.
 *
 * @param msgP The reason, e.g. ``"usage: led {on|off|1|0}"``.
 * @param code TERMAPY_MONITOR_BAD_ARGS for a bad argument, a positive code
 *             for a device fault.
 */
void TermapyMonitor_Fail(const char *msgP, int code);

/**
 * @brief The output a native command writes its text through.
 *
 * putchar-compatible, so it is TermapyMem_Ops.put as it stands.  For a
 * plain line it forwards every byte; for a JSON request it drops the CR of
 * each CRLF, so the text reaches the result string with ``\n`` line breaks.
 */
int TermapyMonitor_NativePut(int c);

/**
 * @brief The next space-separated word of ``textP``, copied into ``wordP``.
 *
 * For a handler that takes several arguments.  A word longer than
 * ``cap - 1`` is cut to fit, so the caller compares it whole and refuses it.
 *
 * @return Where the text after the word starts, or NULL when no word is left.
 */
const char *TermapyMonitor_NextWord(const char *textP, char *wordP, size_t cap);

/**
 * @brief Read ``wordP`` as a whole number from ``low`` to ``high``: decimal,
 *        or hex after ``0x``.
 *
 * Anything else, or a value out of range, is refused with the monitor's
 * message, ``invalid <what>: <word> (use <low> to <high>)``, as the
 * MicroPython monitor's int_arg refuses it, and false is returned: the
 * handler returns without another reply.
 */
bool TermapyMonitor_IntArg(const char *wordP, const char *whatP, long low, long high, long *valueP);

/**
 * @brief Read ``wordP`` as a switch: true for ``on`` or ``1``, false for
 *        ``off`` or ``0``.
 *
 * Anything else is refused, ``invalid <what>: <word> (use on, off, 1 or 0)``,
 * and false is returned with ``*stateP`` untouched.
 */
bool TermapyMonitor_OnOffArg(const char *wordP, const char *whatP, bool *stateP);

#ifdef __cplusplus
}
#endif

#endif /* TERMAPY_MONITOR_H */
