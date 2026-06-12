/**
 * Hyrule Engineering Loop Pi extension.
 *
 * Registers /loop as a friendly front-end for the Hyrule Engineering Loop.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { mkdtemp, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";

type LoopConfig = {
	workspaceRoot: string;
	infraRepo: string;
	outputRoot: string;
	defaultRepo: string;
	defaultAllow: string[];
	defaultSources: string[];
};

type LoopEntry = {
	changeId: string;
	repo: string;
	modelSummary?: ModelSummaryItem[];
	diffPreview?: DiffPreviewItem[];
	signoffStatus?: string;
	signoffSummary?: SignoffSummary;
	failureSummary?: FailureSummary;
	requestPath: string;
	outputRoot: string;
	statePath?: string;
	handoffPath?: string;
	tracePath?: string;
	code: number;
	stdout: string;
	stderr: string;
};

type DiffPreviewItem = {
	repo?: string;
	branch?: string;
	written_files?: string[];
	diff_truncated?: boolean;
};

type ModelSummaryItem = {
	role?: string;
	approved?: boolean;
	model_selection?: {
		provider?: string;
		model?: string;
		tier?: string;
		reason?: string;
	};
};

type SignoffSummary = {
	status?: string;
	reason?: string;
	promotion_count?: number;
	review_targets?: {
		repo?: string;
		branch?: string;
		worktree_path?: string;
		written_files?: string[];
	}[];
	next_operator_commands?: string[];
};

type FailureSummary = {
	last_failing_node?: string;
	domain?: string;
	error_excerpt?: string;
	next_operator_command?: string;
};

const DEFAULT_CONFIG: LoopConfig = {
	workspaceRoot: "/home/svag/Dev",
	infraRepo: "/home/svag/Dev/hyrule-infra",
	outputRoot: "/tmp/hyrule-loop",
	defaultRepo: "hyrule-cloud",
	defaultAllow: ["docs"],
	defaultSources: ["README.md"],
};

function slug(value: string): string {
	return (
		value
			.toUpperCase()
			.replace(/[^A-Z0-9]+/g, "_")
			.replace(/^_+|_+$/g, "")
			.slice(0, 64) || "LOOP_REQUEST"
	);
}

function parseArgs(raw: string): { repo?: string; modelPolicy?: string; planMode: boolean; live: boolean; dryLive: boolean; prompt: string } {
	const tokens = raw.trim().split(/\s+/);
	let repo: string | undefined;
	let modelPolicy: string | undefined;
	let planMode = false;
	let live = false;
	let dryLive = false;
	const rest: string[] = [];

	for (let i = 0; i < tokens.length; i++) {
		const token = tokens[i];
		if (token === "--repo" && tokens[i + 1]) {
			repo = tokens[++i];
			continue;
		}
		if (token === "--model-policy" && tokens[i + 1]) {
			modelPolicy = tokens[++i];
			continue;
		}
		if (token === "--plan") {
			planMode = true;
			continue;
		}
		if (token === "--live") {
			live = true;
			continue;
		}
		if (token === "--dry-live") {
			dryLive = true;
			continue;
		}
		rest.push(token);
	}

	return { repo, modelPolicy, planMode, live, dryLive, prompt: rest.join(" ").trim() };
}

function latestPlanModePlan(ctx: ExtensionContext): string | undefined {
	const entries = ctx.sessionManager.getEntries();
	for (let i = entries.length - 1; i >= 0; i--) {
		const entry = entries[i] as {
			type?: string;
			customType?: string;
			data?: { lastProposedPlan?: string };
		};
		if (entry.type === "custom" && entry.customType === "plan-mode" && entry.data?.lastProposedPlan) {
			return entry.data.lastProposedPlan;
		}
	}
	return undefined;
}

async function readJsonConfig(path: string): Promise<Partial<LoopConfig>> {
	try {
		return JSON.parse(await readFile(path, "utf8")) as Partial<LoopConfig>;
	} catch {
		return {};
	}
}

async function loadConfig(ctx: ExtensionContext): Promise<LoopConfig> {
	const local = await readJsonConfig(resolve(ctx.cwd, ".pi", "hyrule-loop.json"));
	return {
		...DEFAULT_CONFIG,
		...local,
		defaultAllow: local.defaultAllow ?? DEFAULT_CONFIG.defaultAllow,
		defaultSources: local.defaultSources ?? DEFAULT_CONFIG.defaultSources,
	};
}

function autodetectRepo(ctx: ExtensionContext, config: LoopConfig): string {
	const root = resolve(config.workspaceRoot);
	let current = resolve(ctx.cwd);
	while (current.startsWith(root)) {
		const name = basename(current);
		if (name.startsWith("hyrule-") && name !== "hyrule-infra") return name;
		if (current === root) break;
		current = dirname(current);
	}
	return config.defaultRepo;
}

function latestSessionLoopEntry(ctx: ExtensionContext): LoopEntry | undefined {
	const entries = ctx.sessionManager.getEntries();
	for (let i = entries.length - 1; i >= 0; i--) {
		const entry = entries[i] as { type?: string; customType?: string; data?: LoopEntry };
		if (entry.type === "custom" && entry.customType === "hyrule-loop" && entry.data) return entry.data;
	}
	return undefined;
}

async function latestArtifactLoopEntry(config: LoopConfig): Promise<LoopEntry | undefined> {
	try {
		const outputEntries = await readdir(config.outputRoot, { withFileTypes: true });
		let latest: { path: string; mtimeMs: number } | undefined;
		for (const outputEntry of outputEntries) {
			if (!outputEntry.isDirectory()) continue;
			const stateDir = join(config.outputRoot, outputEntry.name, "state");
			let stateEntries;
			try {
				stateEntries = await readdir(stateDir, { withFileTypes: true });
			} catch {
				continue;
			}
			for (const stateEntry of stateEntries) {
				if (!stateEntry.isFile() || !stateEntry.name.endsWith(".json")) continue;
				const statePath = join(stateDir, stateEntry.name);
				const metadata = await stat(statePath);
				if (!latest || metadata.mtimeMs > latest.mtimeMs) latest = { path: statePath, mtimeMs: metadata.mtimeMs };
			}
		}
		if (!latest) return undefined;
		const state = JSON.parse(await readFile(latest.path, "utf8")) as Record<string, unknown>;
		const changeId = typeof state.change_id === "string" ? state.change_id : "UNKNOWN";
		const repo = typeof state.feature_target_repo === "string" ? state.feature_target_repo : config.defaultRepo;
		const outputRoot = dirname(dirname(latest.path));
		return {
			changeId,
			repo,
			modelSummary: modelSummaryFromState(state),
			diffPreview: diffPreviewFromState(state),
			signoffStatus: typeof state.signoff_status === "string" ? state.signoff_status : undefined,
			signoffSummary: signoffSummaryFromRecord(state),
			failureSummary: failureSummaryFromRecord(state),
			requestPath: typeof state.feature_request_path === "string" ? state.feature_request_path : "",
			outputRoot,
			statePath: latest.path,
			handoffPath: typeof state.noc_handoff_path === "string" ? state.noc_handoff_path : undefined,
			tracePath: typeof state.loop_trace_path === "string" ? state.loop_trace_path : undefined,
			code: 0,
			stdout: "",
			stderr: "",
		};
	} catch {
		return undefined;
	}
}

function signoffSummaryFromRecord(record: Record<string, unknown>): SignoffSummary | undefined {
	const raw = record.signoff_summary;
	return typeof raw === "object" && raw !== null ? (raw as SignoffSummary) : undefined;
}

function failureSummaryFromRecord(record: Record<string, unknown>): FailureSummary | undefined {
	const raw = record.failure_summary;
	return typeof raw === "object" && raw !== null ? (raw as FailureSummary) : undefined;
}

function formatSignoffSummary(status: string | undefined, summary: SignoffSummary | undefined): string {
	if (!status && !summary) return "signoff: not recorded";
	const lines = [`signoff: ${status ?? summary?.status ?? "unknown"}`];
	if (summary?.reason) lines.push(`reason: ${summary.reason}`);
	for (const target of summary?.review_targets ?? []) {
		lines.push(`- ${target.repo ?? "unknown"} ${target.branch ?? "unknown"} worktree=${target.worktree_path ?? "unknown"}`);
	}
	return lines.join("\n");
}

function formatFailureSummary(summary: FailureSummary | undefined): string {
	if (!summary) return "failure: none";
	return [
		`failure: ${summary.domain ?? "unknown"}/${summary.last_failing_node ?? "unknown"}`,
		`error: ${summary.error_excerpt ?? "unknown"}`,
		`next: ${summary.next_operator_command ?? "unknown"}`,
	].join("\n");
}

function diffPreviewFromState(state: Record<string, unknown>): DiffPreviewItem[] {
	const previews = Array.isArray(state.diff_preview) ? state.diff_preview : [];
	return previews
		.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
		.map((item) => ({
			repo: typeof item.repo === "string" ? item.repo : undefined,
			branch: typeof item.branch === "string" ? item.branch : undefined,
			written_files: Array.isArray(item.written_files)
				? item.written_files.filter((value): value is string => typeof value === "string")
				: undefined,
			diff_truncated: typeof item.diff_truncated === "boolean" ? item.diff_truncated : undefined,
		}));
}

function diffPreviewFromParsedSummary(summary: Record<string, unknown>): DiffPreviewItem[] {
	const previews = Array.isArray(summary.diff_preview) ? summary.diff_preview : [];
	return previews
		.filter((item): item is DiffPreviewItem => typeof item === "object" && item !== null)
		.map((item) => item);
}

function formatDiffPreview(items: DiffPreviewItem[] | undefined): string {
	if (!items?.length) return "diffPreview: none";
	const lines = ["diffPreview:"];
	for (const item of items) {
		lines.push(
			`- ${item.repo ?? "unknown"} ${item.branch ?? "unknown"} files=${(item.written_files ?? []).join(", ") || "none"} truncated=${String(item.diff_truncated ?? false)}`,
		);
	}
	return lines.join("\n");
}

function modelSummaryFromState(state: Record<string, unknown>): ModelSummaryItem[] {
	const outputs = Array.isArray(state.llm_outputs) ? state.llm_outputs : [];
	return outputs
		.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
		.map((item) => ({
			role: typeof item.role === "string" ? item.role : undefined,
			approved: typeof item.approved === "boolean" ? item.approved : undefined,
			model_selection:
				typeof item.model_selection === "object" && item.model_selection !== null
					? (item.model_selection as ModelSummaryItem["model_selection"])
					: undefined,
		}));
}

function modelSummaryFromParsedSummary(summary: Record<string, unknown>): ModelSummaryItem[] {
	const items = Array.isArray(summary.model_summary) ? summary.model_summary : [];
	return items
		.filter((item): item is ModelSummaryItem => typeof item === "object" && item !== null)
		.map((item) => item);
}

function formatModelSummary(items: ModelSummaryItem[] | undefined): string {
	if (!items?.length) return "modelSummary: none";
	const lines = ["modelSummary:"];
	for (const item of items) {
		const selection = item.model_selection ?? {};
		lines.push(
			`- ${item.role ?? "unknown"}: ${selection.provider ?? "unknown"}/${selection.model ?? "unknown"} tier=${selection.tier ?? "unknown"} approved=${String(item.approved)}`,
		);
	}
	return lines.join("\n");
}

async function latestLoopEntry(ctx: ExtensionContext, config: LoopConfig): Promise<LoopEntry | undefined> {
	return latestSessionLoopEntry(ctx) ?? (await latestArtifactLoopEntry(config));
}

function parseSummary(stdout: string): Record<string, unknown> {
	const index = stdout.indexOf("{");
	if (index < 0) return {};
	try {
		return JSON.parse(stdout.slice(index)) as Record<string, unknown>;
	} catch {
		return {};
	}
}

async function runLoopCli(
	pi: ExtensionAPI,
	config: LoopConfig,
	args: string[],
	ctx: ExtensionContext,
) {
	return pi.exec("uv", ["run", "hyrule-engineering-loop", ...args], {
		cwd: config.infraRepo,
		timeout: 120000,
		signal: ctx.signal,
	});
}

async function showLatest(ctx: ExtensionContext): Promise<void> {
	const config = await loadConfig(ctx);
	const latest = await latestLoopEntry(ctx, config);
	if (!latest) {
		ctx.ui.notify("No Hyrule loop run recorded in this Pi session.", "info");
		return;
	}
	ctx.ui.notify(
		[
			`changeId: ${latest.changeId}`,
			`repo: ${latest.repo}`,
			`outputRoot: ${latest.outputRoot}`,
			`statePath: ${latest.statePath ?? "unknown"}`,
			`handoffPath: ${latest.handoffPath ?? "unknown"}`,
			`tracePath: ${latest.tracePath ?? "unknown"}`,
			formatSignoffSummary(latest.signoffStatus, latest.signoffSummary),
			formatFailureSummary(latest.failureSummary),
			formatModelSummary(latest.modelSummary),
			formatDiffPreview(latest.diffPreview),
			`exitCode: ${latest.code}`,
		].join("\n"),
		"info",
	);
}

async function showTrace(ctx: ExtensionContext): Promise<void> {
	const config = await loadConfig(ctx);
	const latest = await latestLoopEntry(ctx, config);
	if (!latest?.tracePath) {
		ctx.ui.notify("No trace path recorded for the latest Hyrule loop run.", "warning");
		return;
	}
	try {
		const trace = JSON.parse(await readFile(latest.tracePath, "utf8")) as {
			event_count?: number;
			events?: { node?: string; output?: { llm_outputs?: ModelSummaryItem[] } }[];
		};
		const nodes = (trace.events ?? []).map((event) => event.node).filter(Boolean).join(" -> ");
		const modelSummary = (trace.events ?? []).flatMap((event) => event.output?.llm_outputs ?? []);
		ctx.ui.notify(
			[
				`tracePath: ${latest.tracePath}`,
				`eventCount: ${trace.event_count ?? 0}`,
				`nodes: ${nodes}`,
				formatModelSummary(modelSummary),
			].join("\n"),
			"info",
		);
	} catch {
		ctx.ui.notify(`Trace path recorded but not readable: ${latest.tracePath}`, "warning");
	}
}

async function cleanupLatest(ctx: ExtensionContext, pi: ExtensionAPI): Promise<void> {
	const config = await loadConfig(ctx);
	const latest = await latestLoopEntry(ctx, config);
	if (!latest?.statePath) {
		ctx.ui.notify("No state path recorded for cleanup.", "warning");
		return;
	}
	const result = await runLoopCli(pi, config, ["state-cleanup", "--state-path", latest.statePath], ctx);
	ctx.ui.notify(result.code === 0 ? `Cleaned up latest loop worktree.\n${result.stdout}` : result.stderr || result.stdout, result.code === 0 ? "info" : "error");
}

async function approveLatest(ctx: ExtensionContext, pi: ExtensionAPI): Promise<void> {
	const config = await loadConfig(ctx);
	const latest = await latestLoopEntry(ctx, config);
	if (!latest?.statePath) {
		ctx.ui.notify("No state path recorded for approval.", "warning");
		return;
	}
	const result = await runLoopCli(pi, config, ["state-approve", "--state-path", latest.statePath], ctx);
	ctx.ui.notify(result.code === 0 ? `Approved latest loop state.\n${result.stdout}` : result.stderr || result.stdout, result.code === 0 ? "info" : "error");
}

async function reviewLessons(ctx: ExtensionContext, pi: ExtensionAPI): Promise<void> {
	const config = await loadConfig(ctx);
	const memoryDir = join(config.infraRepo, "memory");
	const result = await runLoopCli(pi, config, ["lessons", "--memory-dir", memoryDir], ctx);
	if (result.code !== 0) {
		ctx.ui.notify(result.stderr || result.stdout || "lessons command failed", "error");
		return;
	}
	ctx.ui.notify(
		[
			"Hyrule loop memory (merge proposals into memory/lessons/ by hand):",
			"",
			result.stdout.trim(),
		].join("\n"),
		"info",
	);
}

async function runLoop(args: string, ctx: ExtensionContext, pi: ExtensionAPI): Promise<void> {
	const config = await loadConfig(ctx);
	const parsed = parseArgs(args);
	let prompt = parsed.prompt;

	if (!prompt) {
		const input = await ctx.ui.editor("Hyrule loop request:", "");
		if (!input?.trim()) {
			ctx.ui.notify("No loop request provided.", "warning");
			return;
		}
		prompt = input.trim();
	}

	if (parsed.planMode) {
		const plan = latestPlanModePlan(ctx);
		if (!plan) {
			ctx.ui.notify("No stored Plan Mode proposal found. Run /plan and produce a proposed plan first.", "warning");
			return;
		}
		prompt = [
			"Use this Plan Mode proposed plan as the feature request.",
			"",
			"<proposed_plan>",
			plan,
			"</proposed_plan>",
			"",
			prompt ? `Operator note:\n${prompt}` : "",
		]
			.filter(Boolean)
			.join("\n");
	}

	const repo = parsed.repo ?? autodetectRepo(ctx, config);
	const changeId = slug(prompt);
	const requestDir = await mkdtemp(join(tmpdir(), "hyrule-loop-request-"));
	const requestPath = join(requestDir, `${changeId.toLowerCase()}.md`);
	const outputRoot = resolve(config.outputRoot, changeId.toLowerCase());

	await writeFile(
		requestPath,
		[
			"# Hyrule Loop Request",
			"",
			`- change_id: ${changeId}`,
			`- repo: ${repo}`,
			`- cwd: ${ctx.cwd}`,
			"",
			"## Request",
			"",
			prompt,
			"",
		].join("\n"),
		"utf8",
	);

	const commandArgs = [
		"run",
		"hyrule-engineering-loop",
		"feature",
		changeId,
		"--request",
		requestPath,
		"--repo",
		repo,
		"--workspace-root",
		config.workspaceRoot,
		"--output-root",
		outputRoot,
	];
	for (const allowed of config.defaultAllow) commandArgs.push("--allow", allowed);
	for (const source of config.defaultSources) commandArgs.push("--source", source);
	commandArgs.push("--memory-dir", join(config.infraRepo, "memory"));
	if (parsed.modelPolicy) commandArgs.push("--model-policy", parsed.modelPolicy);
	if (parsed.live) commandArgs.push("--live");
	if (parsed.dryLive) commandArgs.push("--dry-live");

	ctx.ui.notify(`Starting Hyrule loop for ${repo}: ${changeId}`, "info");
	const result = await pi.exec("uv", commandArgs, {
		cwd: config.infraRepo,
		timeout: 120000,
		signal: ctx.signal,
	});
	const summary = parseSummary(result.stdout);
	const modelSummary = modelSummaryFromParsedSummary(summary);
	const diffPreview = diffPreviewFromParsedSummary(summary);
	const signoffSummary = signoffSummaryFromRecord(summary);
	const failureSummary = failureSummaryFromRecord(summary);

	pi.appendEntry("hyrule-loop", {
		changeId,
		repo,
		modelSummary,
		diffPreview,
		signoffStatus: typeof summary.signoff_status === "string" ? summary.signoff_status : undefined,
		signoffSummary,
		failureSummary,
		requestPath,
		outputRoot,
		statePath: typeof summary.state_path === "string" ? summary.state_path : undefined,
		handoffPath: typeof summary.handoff_path === "string" ? summary.handoff_path : undefined,
		tracePath: typeof summary.trace_path === "string" ? summary.trace_path : undefined,
		code: result.code,
		stdout: result.stdout,
		stderr: result.stderr,
	});

	if (result.code !== 0) {
		ctx.ui.notify(`Hyrule loop failed for ${changeId}.\n${result.stderr || result.stdout}`, "error");
		return;
	}

	ctx.ui.notify(`Hyrule loop staged ${changeId}.`, "info");
	pi.sendMessage(
		{
			customType: "hyrule-loop-result",
			display: true,
			content: [
				"**Hyrule loop staged a feature request.**",
				"",
				`- change id: \`${changeId}\``,
				`- repo: \`${repo}\``,
				`- output: \`${outputRoot}\``,
				`- request: \`${requestPath}\``,
				`- trace: \`${typeof summary.trace_path === "string" ? summary.trace_path : "not rendered"}\``,
				`- live mode: \`${String(summary.live_mode ?? false)}\``,
				`- dry-live: \`${String(summary.dry_live ?? false)}\``,
				"",
				formatModelSummary(modelSummary),
				"",
				formatDiffPreview(diffPreview),
				"",
				formatSignoffSummary(typeof summary.signoff_status === "string" ? summary.signoff_status : undefined, signoffSummary),
				"",
				formatFailureSummary(failureSummary),
				"",
				"Raw summary:",
				"",
				"```json",
				result.stdout.trim(),
				"```",
			].join("\n"),
		},
		{ triggerTurn: false },
	);
}

export default function hyruleLoopExtension(pi: ExtensionAPI): void {
	pi.registerCommand("loop", {
		description: "Run or manage the Hyrule Engineering Loop",
		handler: async (args: string, ctx: ExtensionContext) => {
			const command = args.trim();
			if (!command) {
				const choice = await ctx.ui.select("Hyrule loop", [
					"Start new request",
					"Show latest summary",
					"Show latest trace",
					"Review lessons & proposals",
					"Cleanup latest worktree",
					"Approve latest state",
				]);
				if (choice === "Start new request") {
					await runLoop("", ctx, pi);
				} else if (choice === "Show latest summary") {
					await showLatest(ctx);
				} else if (choice === "Show latest trace") {
					await showTrace(ctx);
				} else if (choice === "Review lessons & proposals") {
					await reviewLessons(ctx, pi);
				} else if (choice === "Cleanup latest worktree") {
					await cleanupLatest(ctx, pi);
				} else if (choice === "Approve latest state") {
					await approveLatest(ctx, pi);
				}
				return;
			}

			if (/^status$/i.test(command)) return showLatest(ctx);
			if (/^trace$/i.test(command)) return showTrace(ctx);
			if (/^lessons$/i.test(command)) return reviewLessons(ctx, pi);
			if (/^cleanup$/i.test(command)) return cleanupLatest(ctx, pi);
			if (/^approve$/i.test(command)) return approveLatest(ctx, pi);
			await runLoop(args, ctx, pi);
		},
	});

	pi.registerCommand("loop-config", {
		description: "Show Hyrule Engineering Loop Pi extension defaults",
		handler: async (_args: string, ctx: ExtensionContext) => {
			const config = await loadConfig(ctx);
			ctx.ui.notify(
				[
					`workspaceRoot: ${config.workspaceRoot}`,
					`infraRepo: ${config.infraRepo}`,
					`outputRoot: ${config.outputRoot}`,
					`defaultRepo: ${config.defaultRepo}`,
					`autodetectedRepo: ${autodetectRepo(ctx, config)}`,
					`defaultAllow: ${config.defaultAllow.join(", ")}`,
					`defaultSources: ${config.defaultSources.join(", ")}`,
					`project config: ${resolve(ctx.cwd, ".pi", "hyrule-loop.json")}`,
				].join("\n"),
				"info",
			);
		},
	});
}
