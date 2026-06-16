/**
 * Clear Window Pi extension.
 *
 * Adds /clear for quickly dropping the current transcript from view and starting
 * fresh. The default mirrors the common agent "clear" behavior: clear the
 * terminal viewport/scrollback and switch to a new Pi session. Existing session
 * files are left intact so /resume can still recover them.
 */

import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";

type ClearMode = "window" | "session" | "both";

const CLEAR_TERMINAL_SEQUENCE = "\x1b[2J\x1b[3J\x1b[H";
const ARGUMENTS: Array<{ value: ClearMode; label: string; description: string }> = [
	{
		value: "both",
		label: "both",
		description: "Default: clear terminal viewport/scrollback and start a fresh Pi session",
	},
	{
		value: "window",
		label: "window",
		description: "Clear only the terminal viewport/scrollback; keep the current Pi session context",
	},
	{
		value: "session",
		label: "session",
		description: "Start a fresh Pi session; leave terminal scrollback alone",
	},
];

function parseMode(args: string): ClearMode | undefined {
	const value = args.trim().toLowerCase();
	if (!value) return "both";
	if (["both", "all"].includes(value)) return "both";
	if (["window", "screen", "terminal", "scrollback", "view"].includes(value)) return "window";
	if (["session", "history", "context", "conversation", "new"].includes(value)) return "session";
	return undefined;
}

async function clearTerminal(ctx: ExtensionCommandContext): Promise<void> {
	if (ctx.mode !== "tui") {
		if (ctx.hasUI) ctx.ui.notify("/clear window only affects the interactive TUI.", "warning");
		return;
	}

	await ctx.ui.custom<void>((tui, _theme, _keybindings, done) => {
		tui.stop();
		process.stdout.write(CLEAR_TERMINAL_SEQUENCE);
		tui.start();
		tui.requestRender(true);
		done(undefined);
		return { render: () => [], invalidate: () => {} };
	});
}

async function startFreshSession(ctx: ExtensionCommandContext): Promise<boolean> {
	await ctx.waitForIdle();
	const result = await ctx.newSession();
	if (result.cancelled) {
		ctx.ui.notify("/clear session was cancelled by another extension.", "warning");
		return false;
	}
	return true;
}

export default function clearWindowExtension(pi: ExtensionAPI): void {
	pi.registerCommand("clear", {
		description: "Clear Pi's window/scrollback and start a fresh session",
		getArgumentCompletions: (prefix) => {
			const normalized = prefix.trim().toLowerCase();
			const matches = ARGUMENTS.filter((item) => item.value.startsWith(normalized));
			return matches.length > 0 ? matches : null;
		},
		handler: async (args: string, ctx: ExtensionCommandContext) => {
			const mode = parseMode(args);
			if (!mode) {
				ctx.ui.notify("Usage: /clear [both|window|session]", "error");
				return;
			}

			if (mode === "window") {
				await clearTerminal(ctx);
				return;
			}

			if (mode === "session") {
				await startFreshSession(ctx);
				return;
			}

			await ctx.waitForIdle();
			await clearTerminal(ctx);
			await startFreshSession(ctx);
		},
	});
}
