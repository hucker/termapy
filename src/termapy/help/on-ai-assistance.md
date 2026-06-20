# On AI assistance

I built termapy with heavy use of Claude. I'm not going to call it vibe coded.

**I architected it, Claude wrote it, we iterated.** I made the decisions: the plugin system, the three-channel output model, the CLI/TUI shared engine, verifying every catalogued CRC against the reveng catalogue. Claude wrote most of the code that implements them. Sometimes one round, sometimes ten.

## Testing is the contract that doesn't live in your prompt

1259 tests. The engine, capture, dispatch, and protocol layers are 89-97% covered. The prompt evaporates when the response comes back; the tests don't. They're the only thing in the loop that isn't a fuzzy translation of an idea.

## What I bring that the LLM doesn't

Not knowledge. Claude knows plenty about serial ports and USB and timing. What I bring is *what matters to me* in this specific project, and the willingness to push back when the code doesn't reflect it.

`proto_frame_gap_ms` defaults to 50. Not arbitrary: 30ms is the perception floor when a user is waiting for a response, USB Full Speed runs on 1ms frames with OS scheduling jitter on top, and the cost is asymmetric: too long is invisible, too short interleaves the next prompt into the device's response. The LLM could derive most of those facts on request. It can't tell me which ones to weight, or that "interleaved output looks broken to a user" is the failure mode I care about more than the others. That's the part I have to bring.

## How this started

One-night hack for a work project that needed ANSI-colored serial output, and had to run on both Mac and Windows. That ruled out most of the existing terminals before I even started. I had a TUI working by morning. Here's what nobody tells you about 1-kloc TUIs: they're toys. The moment you try to *use* one, you find out what's missing. Config picker, script editor, port picker, confirm dialog, protocol debug screen. Most of termapy is the stuff I added after the hack night, because I was using it daily for the work project and was too stubborn to live with the rough edges.

That's why the AI angle works. Claude can write a plugin in one shot. Claude can't tell me the prompt feels laggy after a long protocol test or that the script picker sorts wrong. I had to use the thing to find out.

If you're skeptical of LLM-built projects, read `test_serial_engine.py` and decide for yourself.
